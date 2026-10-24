"""Offline discovery and bounded single-SKU inspection; no database or network."""
import hashlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from o3d.catalog.objects import Objects
from o3d.catalog.vendor_cad import (
    MAX_EXPANDED, VendorCAD, VendorCADSearch, _download, _public_dns,
    _select_step, _validate_url, cad_links,
)

URL = 'https://www.revrobotics.com/content/cad/REV-41-1300.step'
STEP = b"ISO-10303-21;\nHEADER;ENDSEC;DATA;#1=SI_UNIT(.MILLI.,.METRE.);ENDSEC;END-ISO-10303-21;\n"
GEOMETRY = {'valid': True, 'unit': 'mm', 'bounds_mm': [15.0, 15.0, 420.0],
            'volume_mm3': 1234.0, 'solid_count': 1, 'face_count': 12}


def row(identifier='one', sku='REV-41-1300', **payload):
    body = {'product_url': 'https://www.revrobotics.com/rev-41-1300/',
            'facts': {'sku': sku, 'step_url': URL}, 'source_url': 'https://www.revrobotics.com/rev-41-1300/'}
    body.update(payload)
    return {'id': identifier, 'name': '15 mm extrusion pack', 'provider_key': 'rev',
            'external_id': sku, 'source_id': 'source-one', 'payload': body,
            'evidence_url': body['source_url'], 'evidence_sha256': 'a' * 64,
            'evidence_verification': 'verified_capture'}


class Connection:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def execute(self, sql, args):
        self.queries.append((sql, args))
        assert 'INSERT' not in sql and 'UPDATE' not in sql
        return iter(r for r in self.rows if (not args.get('record_id') or r['id'] == args['record_id'])
                    and (not args.get('provider') or r['provider_key'] == args['provider']))


def response(body=STEP, status=200, headers=None):
    return SimpleNamespace(content=body, status_code=status, headers=headers or {})


def archive(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as stream:
        for name, body in files:
            stream.writestr(name, body)
    return output.getvalue()


class VendorCADTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.objects = Objects(Path(temporary.name))
        self.conn = Connection([row()])
        self.vendor = VendorCAD(SimpleNamespace(conn=self.conn, objects=self.objects))

    def inspect(self, **kwargs):
        return self.vendor.inspect('one', fetcher=lambda url: response(), **kwargs)

    def test_literal_cad_links_and_false_metadata(self):
        links = cad_links({'facts': {'step_url': URL, 'cad_available': True,
            'cad_notes': 'download at ' + URL, 'cad_url': 'Yes',
            'image_url': 'https://www.revrobotics.com/photo.png',
            'product_url': 'https://www.revrobotics.com/rev-41-1300/',
            'cad_reference_url': 'https://www.revrobotics.com/cad/'}})
        self.assertEqual({URL, 'https://www.revrobotics.com/cad/'}, {link['url'] for link in links})
        nested = cad_links({'cad': {'url': URL}, 'assets': [{'kind': 'cad', 'url': URL}]})
        self.assertEqual(1, len(nested))
        self.assertEqual('step', nested[0]['format'])
        self.assertEqual([], cad_links({'notes': [URL], 'cad': URL}))

    def test_eligible_filter_before_pagination_exact_sku_and_counts(self):
        self.conn.rows = [row(str(i), str(i), facts={'sku': str(i), 'cad_available': True}) for i in range(120)]
        self.conn.rows += [row('match-a', '001'), row('match-b', '0010'), row('match-c', '001')]
        result = self.vendor.search(VendorCADSearch(sku='001', limit=1, offset=1))
        self.assertEqual(123, result['scanned_records'])
        self.assertEqual(3, result['eligible_records'])
        self.assertEqual(2, result['total'])
        self.assertEqual('match-c', result['items'][0]['id'])
        self.assertEqual('001', result['items'][0]['sku'])
        self.assertEqual('rev', result['items'][0]['origin_provider'])
        self.assertEqual('unknown', result['items'][0]['license'])
        self.assertEqual('source-one', result['source_chains'][0]['source_id'])

    def test_product_and_actual_url_required_no_synthetic_join(self):
        self.conn.rows = [row('cad-only', product_url=None),
            row('prose-order', product_url=None, order_path='buy online'),
            row('literal-order', product_url=None, order_path='https://www.revrobotics.com/order/')]
        result = self.vendor.search({})
        self.assertEqual(['literal-order'], [item['id'] for item in result['items']])
        self.assertIsNone(result['items'][0]['product_url'])

    def test_parser_missing_hash_persistence_no_asset_permission_mutation(self):
        before = json.dumps(self.conn.rows)
        with patch('o3d.catalog.vendor_cad.importlib.util.find_spec', return_value=None), patch.dict(os.environ, {}, clear=True):
            report = self.inspect()
        self.assertEqual('parser_unavailable', report['status'])
        self.assertEqual('step_signature_verified', report['validation_level'])
        self.assertEqual('unknown', report['unit'])
        self.assertEqual('unverified', report['physical_accuracy'])
        self.assertEqual(hashlib.sha256(STEP).hexdigest(), report['step_sha256'])
        self.assertEqual(STEP, self.objects.read(report['step_object_key']))
        self.assertEqual(0, stat.S_IMODE((self.objects.root / report['step_object_key']).stat().st_mode) & 0o222)
        self.assertEqual(before, json.dumps(self.conn.rows))
        self.assertEqual(1, len(list((self.objects.root / 'vendor-cad').glob('*.json'))))
        self.assertEqual(report['status'], self.vendor.search({})['items'][0]['cad_links'][0]['inspection']['status'])
        self.assertEqual([], self.vendor.search({'geometry_validated': True})['items'])

    def test_fake_interpreter_measures_bounds_no_fit_or_revision_claim(self):
        # Exercise the subprocess seam itself with an executable, not fabricated
        # dimensions embedded in the vendor payload. This is still an offline
        # parser-contract test, not evidence about real geometry.
        executable = self.objects.root / 'fake-cad-python'
        executable.write_text('#!' + sys.executable + '\nimport json\nprint(' + repr(json.dumps(GEOMETRY)) + ')\n')
        executable.chmod(0o755)
        report = self.inspect(cad_python=str(executable), expected_dimensions_mm=[420, 15, 15])
        self.assertEqual('geometry_validated', report['status'])
        self.assertEqual('within_numerical_tolerance', report['dimension_comparison']['state'])
        self.assertEqual('unverified', report['physical_accuracy'])
        self.assertEqual('unverified', report['sku_revision_match'])
        self.assertEqual('unverified', report['geometry_scope'])
        self.assertEqual('unknown', report['redistribution'])
        self.assertEqual(1, self.vendor.search({'geometry_validated': True})['total'])

    def test_geometry_numeric_comparison_surfaces_and_missing_units(self):
        with patch('o3d.catalog.vendor_cad._parse_step', return_value=GEOMETRY):
            result = self.inspect(cad_python='test', expected_dimensions_mm=[15, 15, 419.8])
        self.assertEqual('outside_numerical_tolerance', result['dimension_comparison']['state'])
        with patch('o3d.catalog.vendor_cad._parse_step', return_value=dict(GEOMETRY, solid_count=0)):
            result = self.inspect(cad_python='test')
        self.assertEqual('parsed_surface_geometry', result['status'])
        self.assertFalse(result['solid_model'])
        unknown = STEP.replace(b'#1=SI_UNIT(.MILLI.,.METRE.);', b'')
        with patch('o3d.catalog.vendor_cad._parse_step', return_value=GEOMETRY):
            result = self.vendor.inspect('one', cad_python='test', fetcher=lambda url: response(unknown))
        self.assertEqual('unit_unverified', result['status'])
        self.assertEqual('unknown', result['unit'])

    def test_url_must_exist_in_exact_record_fixed_scope_and_private_dns(self):
        with self.assertRaisesRegex(ValueError, 'literal CAD link'):
            self.inspect(cad_url='https://www.revrobotics.com/other.step')
        for url in ['http://www.revrobotics.com/one.step', 'https://127.0.0.1/one.step',
                    'https://www.revrobotics.com.evil.test/one.step', 'https://user:secret@www.revrobotics.com/one.step',
                    'https://raw.githubusercontent.com/unknown/repo/main/a.step',
                    'https://raw.githubusercontent.com/adafruit/Adafruit_CAD_Parts/../other/a.step']:
            with self.assertRaises(ValueError):
                _validate_url(url)
        _validate_url('https://raw.githubusercontent.com/adafruit/Adafruit_CAD_Parts/master/a.step')
        with patch('socket.getaddrinfo', return_value=[(2, 1, 6, '', ('10.0.0.1', 443))]):
            with self.assertRaisesRegex(ValueError, 'private'):
                _public_dns(URL)

    def test_redirect_revalidation_no_access_bypass_and_bounded_size(self):
        calls = []
        def redirect(url):
            calls.append(url)
            return response(b'', 302, {'location': 'https://evil.test/private.step'})
        with self.assertRaisesRegex(ValueError, 'allowlist'):
            _download(URL, redirect)
        self.assertEqual([URL], calls)
        for status in [401, 403, 404]:
            result = self.vendor.inspect('one', fetcher=lambda url: response(b'', status))
            self.assertEqual('inspection_failed', result['status'])
            self.assertIn(str(status), result['error'])
        with patch('o3d.catalog.vendor_cad.MAX_DOWNLOAD', 3):
            result = self.inspect()
        self.assertEqual('inspection_failed', result['status'])
        self.assertIn('20 MB', result['error'])
        result = self.vendor.inspect('one', fetcher=lambda url: response(b'<html>login</html>'))
        self.assertEqual('unsupported_format', result['status'])

    def test_zip_matching_sku_ambiguity_traversal_symlink_and_expanded_limit(self):
        correct = archive([('REV-41-1300.step', STEP), ('REV-41-13000.step', STEP)])
        name, data = _select_step(correct, 'REV-41-1300', 'REV-41-1300')
        self.assertEqual('REV-41-1300.step', name)
        self.assertEqual(STEP, data)
        for files in [[('REV-41-13000.step', STEP)],
                      [('REV-41-1300-a.step', STEP), ('REV-41-1300-b.stp', STEP)]]:
            with self.assertRaisesRegex(ValueError, 'unambiguous'):
                _select_step(archive(files), 'REV-41-1300', 'REV-41-1300')
        for name in ['../REV-41-1300.step', '/REV-41-1300.step', 'C:/REV-41-1300.step', '..\\REV-41-1300.step']:
            with self.assertRaisesRegex(ValueError, 'Unsafe'):
                _select_step(archive([(name, STEP)]), 'REV-41-1300', 'REV-41-1300')
        with patch('o3d.catalog.vendor_cad.MAX_EXPANDED', 3):
            with self.assertRaisesRegex(ValueError, 'expanded-size'):
                _select_step(correct, 'REV-41-1300', 'REV-41-1300')
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as stream:
            link = zipfile.ZipInfo('REV-41-1300.step')
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            stream.writestr(link, 'target')
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            _select_step(output.getvalue(), 'REV-41-1300', 'REV-41-1300')

    def test_zip_hash_and_ambiguity_report(self):
        packed = archive([('REV-41-1300.step', STEP)])
        with patch('o3d.catalog.vendor_cad._parse_step', return_value=GEOMETRY):
            result = self.vendor.inspect('one', cad_python='test', fetcher=lambda url: response(packed))
        self.assertEqual(hashlib.sha256(packed).hexdigest(), result['archive_sha256'])
        self.assertEqual(hashlib.sha256(STEP).hexdigest(), result['step_sha256'])
        ambiguous = archive([('REV-41-1300-a.step', STEP), ('REV-41-1300-b.step', STEP)])
        result = self.vendor.inspect('one', fetcher=lambda url: response(ambiguous))
        self.assertEqual('archive_selection_required', result['status'])
        self.assertEqual(2, len(result['choices']))

    def test_source_reference_is_discoverable_without_claiming_importability(self):
        reference = 'https://www.revrobotics.com/cad/'
        self.conn.rows = [row(facts={'sku': 'REV-41-1300', 'cad_reference_url': reference})]
        item = self.vendor.search({})['items'][0]
        self.assertEqual('reference', item['cad_links'][0]['format'])
        self.assertEqual('source_provided', item['validation_level'])
        with self.assertRaisesRegex(ValueError, 'Select one exact'):
            self.inspect()

    def test_expected_dimensions_validation(self):
        for dimensions in [[1, 2], [1, -2, 3], [1, float('nan'), 3]]:
            with self.assertRaisesRegex(ValueError, 'three positive'):
                self.inspect(expected_dimensions_mm=dimensions)
        for tolerance in [-1, float('inf')]:
            with self.assertRaisesRegex(ValueError, 'finite and nonnegative'):
                self.inspect(tolerance_mm=tolerance)


if __name__ == '__main__':
    unittest.main()
