"""Offline provenance tests; run with unittest without optional database packages."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from o3d.catalog.supply_evidence import normalize_evidence_tree

STAMP = '2026-10-24T21:00:00Z'
URL = 'https://www.samtec.com/products/one'


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, *, group='one', leaf='samtec', body=b'Official material: steel', sources=None, records=None, **extra):
        directory = self.root / 'assignments' / group / leaf
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'capture.txt').write_bytes(body)
        source = {'url': URL, 'path': 'capture.txt', 'status': 200,
                  'sha256': hashlib.sha256(body).hexdigest(), 'captured_at': STAMP}
        record = {'external_id': 'SKU-A', 'name': 'Product', 'record_type': 'material',
                  'material': 'Steel', 'source_url': URL, 'facts': {'label': 'Steel'},
                  'constraints': [], 'license_status': 'unknown'}
        data = {'task': 'research', 'provider': 'Samtec', 'sources': sources if sources is not None else [source],
                'records': records if records is not None else [record], **extra}
        result = directory / 'results.json'
        result.write_text(json.dumps(data))
        return result, data

    def normalize(self):
        return normalize_evidence_tree(self.root)

    def test_exact_file_preserves_variants_and_idempotence(self):
        _, data = self.write()
        first = dict(data['records'][0], external_id='A', name='Shared name')
        second = dict(first, external_id='B')
        self.write(records=[first, second])
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        output = self.normalize()
        self.assertEqual(output, self.normalize())
        self.assertEqual(2, output['summary']['records'])
        self.assertEqual({'A', 'B'}, {r['external_id'] for r in output['bundles'][0]['records']})
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        self.assertTrue(output['bundles'][0]['task'])
        self.assertEqual('unknown', output['bundles'][0]['records'][0]['reuse_permission'])

    def test_embedded_body_hash_and_exact_file_scope(self):
        wrapper = {'url': URL, 'status': 200, 'body': 'Steel', 'captured_at': STAMP}
        body = json.dumps(wrapper).encode()
        source = {'url': URL, 'path': 'capture.txt', 'status': 200, 'captured_at': STAMP,
                  'sha256': hashlib.sha256(b'Steel').hexdigest(), 'sha256_scope': 'embedded_body_utf8'}
        self.write(body=body, sources=[source])
        output = self.normalize()
        captured = output['bundles'][0]['sources'][0]
        self.assertEqual(1, output['summary']['records'])
        self.assertEqual(hashlib.sha256(body).hexdigest(), captured['sha256'])
        source['sha256_scope'] = 'exact_file_bytes'
        self.write(body=body, sources=[source])
        self.assertEqual(0, self.normalize()['summary']['records'])
        source['sha256_scope'] = 'embedded_body_utf8'
        source['sha256'] = hashlib.sha256(body).hexdigest()
        self.write(body=body, sources=[source])
        self.assertEqual(0, self.normalize()['summary']['records'])

    def test_unretained_original_hash_requires_independent_retained_representation_proof(self):
        wrapper = {'url': URL, 'status': 200, 'content': 'Extracted PDF text', 'captured_at': STAMP}
        raw = json.dumps(wrapper).encode()
        source = {'url': URL, 'path': 'capture.txt', 'status': 200, 'captured_at': STAMP,
                  'sha256': 'a' * 64, 'sha256_scope': 'original_response_body_not_retained',
                  'capture_file_sha256': hashlib.sha256(raw).hexdigest(),
                  'saved_content_sha256': hashlib.sha256(wrapper['content'].encode()).hexdigest()}
        self.write(body=raw, sources=[source])
        result = self.normalize()
        self.assertEqual(1, result['summary']['records'])
        self.assertFalse(result['bundles'][0]['sources'][0]['original_response_hash_verified'])
        source['saved_content_sha256'] = 'b' * 64
        self.write(body=raw, sources=[source])
        self.assertEqual(0, self.normalize()['summary']['records'])

    def test_real_wrapper_metadata_does_not_upgrade_reader_to_http_verified(self):
        self.write(body=json.dumps({'url': URL, 'status': 200, 'body': 'Steel', 'captured_at': STAMP,
                                   'capture_method': 'web.run open'}).encode())
        result = self.normalize()
        self.assertEqual(0, result['summary']['records'])
        self.assertEqual('observational_unverified', result['bundles'][0]['sources'][0]['verification'])

    def test_tampered_hash_never_admits_record(self):
        path, data = self.write()
        data['sources'][0]['sha256'] = '0' * 64
        path.write_text(json.dumps(data))
        result = self.normalize()
        self.assertEqual(0, result['summary']['records'])
        self.assertTrue(any('SHA256' in q['reason'] for q in result['quarantined']))

    def test_missing_traversal_symlink_and_http_failure(self):
        for variant in ('missing.txt', '/etc/passwd', '../../../../etc/passwd', 'escape.txt', '403', 'HTTP 200', 'conflict'):
            with self.subTest(variant=variant):
                path, data = self.write()
                if variant == 'escape.txt':
                    outside = self.root.parent / (self.root.name + '-outside.txt')
                    outside.write_bytes(b'Official material: steel')
                    self.addCleanup(outside.unlink, missing_ok=True)
                    escape = path.parent / 'escape.txt'
                    escape.symlink_to(outside)
                    data['sources'][0]['path'] = variant
                elif variant == '403':
                    data['sources'][0]['status'] = 403
                elif variant == 'HTTP 200':
                    data['sources'][0]['status'] = variant
                elif variant == 'conflict':
                    wrapper = {'url': URL, 'status': 403, 'body': 'Denied'}
                    raw = json.dumps(wrapper).encode()
                    (path.parent / 'capture.txt').write_bytes(raw)
                    data['sources'][0]['sha256'] = hashlib.sha256(raw).hexdigest()
                else:
                    data['sources'][0]['path'] = variant
                path.write_text(json.dumps(data))
                self.assertEqual(0, self.normalize()['summary']['records'])

    def test_explicit_license_is_not_a_grant_and_worker_gates_apply(self):
        _, data = self.write()
        record = dict(data['records'][0], license_status='explicit')
        self.write(records=[record])
        output = self.normalize()
        self.assertEqual('unknown', output['bundles'][0]['records'][0]['reuse_permission'])
        for gate in ({'automatic_ingestion_allowed': False}, {'allow_ingestion': False},
                     {'summary': {'database_import_status': 'blocked_pending_permission'}},
                     {'blockers': ['Terms prohibit automated scraping and commercial database redistribution.']},
                     {'summary': {'manual_review_required': True}}):
            with self.subTest(gate=gate):
                self.write(records=[record], **gate)
                gated = self.normalize()
                self.assertEqual(0, gated['summary']['records'])
                self.assertEqual('derived_metadata', gated['bundles'][0]['sources'][0]['source_kind'])
                self.assertNotIn('path', gated['bundles'][0]['sources'][0])

    def test_unknown_copyright_does_not_ban_manufacturer_facts(self):
        self.write(blockers=['Dataset rights unknown; code license is not a data license. No API requests attempted.'])
        self.assertEqual(1, self.normalize()['summary']['records'])

    def test_reader_capture_and_denial_page_do_not_claim_http_success(self):
        path, data = self.write()
        data['sources'][0]['status'] = None
        data['sources'][0]['capture_method'] = 'web.run open'
        path.write_text(json.dumps(data))
        output = self.normalize()
        self.assertEqual(0, output['summary']['records'])
        self.assertEqual('observational_unverified', output['bundles'][0]['sources'][0]['verification'])
        self.assertIsNone(output['bundles'][0]['sources'][0]['status'])
        self.write(body=b'<html><title>Access Denied</title><body>Search API</body></html>')
        self.assertEqual(0, self.normalize()['summary']['records'])

    def test_official_https_required(self):
        for url in ('http://www.samtec.com/a', 'https://samtec.com.evil.test/a', 'https://attacker.test/a', 'https://user@www.samtec.com/a'):
            path, data = self.write()
            data['sources'][0]['url'] = url
            data['records'][0]['source_url'] = url
            path.write_text(json.dumps(data))
            self.assertEqual(0, self.normalize()['summary']['records'])

    def test_group_summaries_ignored_and_only_exact_cross_group_duplicates_removed(self):
        path, data = self.write()
        aggregate = path.parent.parent / 'results.json'
        aggregate.write_text(json.dumps(data))
        self.write(group='two')
        result = self.normalize()
        self.assertEqual(2, result['summary']['leaves'])
        self.assertEqual(1, result['summary']['records'])
        self.assertEqual(1, result['summary']['duplicate_records'])
        data['records'][0]['facts']['dimension'] = '2 mm'
        self.write(group='two', records=data['records'])
        result = self.normalize()
        records = result['bundles'][0]['records']
        self.assertEqual(2, len(records))
        self.assertEqual(2, len({r['external_id'] for r in records}))
        self.assertEqual('SKU-A', records[1]['original_external_id'])

    def test_same_name_and_shared_external_id_do_not_collapse_distinct_rows(self):
        _, data = self.write()
        record = data['records'][0]
        second = dict(record, material='Aluminum')
        self.write(records=[record, second])
        result = self.normalize()
        self.assertEqual(2, result['summary']['records'])
        self.assertEqual(2, len({r['external_id'] for r in result['bundles'][0]['records']}))

    def test_repeated_same_group_variant_reuses_assigned_identity(self):
        _, data = self.write()
        red = dict(data['records'][0], facts={'color': 'red'})
        blue = dict(red, facts={'color': 'blue'})
        self.write(records=[red, blue, blue])
        records = self.normalize()['bundles'][0]['records']
        self.assertEqual(3, len(records))
        self.assertEqual('SKU-A', records[0]['external_id'])
        self.assertNotEqual(records[0]['external_id'], records[1]['external_id'])
        self.assertEqual(records[1], records[2])
        self.assertEqual('SKU-A', records[2]['original_external_id'])
        self.assertEqual('SKU-A', records[2]['facts']['original_external_id'])
        self.assertEqual('distinct evidence variant sharing supplier identifier', records[2]['identity_basis'])

    def test_null_id_is_marked_local_evidence_identity(self):
        _, data = self.write()
        data['records'][0]['external_id'] = None
        self.write(records=data['records'])
        record = self.normalize()['bundles'][0]['records'][0]
        self.assertTrue(record['external_id'].startswith('evidence:'))
        self.assertIsNone(record['original_external_id'])
        self.assertEqual('local_evidence_id', record['identifier_kind'])

    def test_offer_redaction_and_inert_derived_metadata(self):
        _, data = self.write()
        record = dict(data['records'][0], record_type='purchased_part', pricing={'tier': 120}, unitPrice='12', currentStock=5, facts={
            'model': 'A', 'price': '100', 'nested': {'stock': 2, 'diameter': '3 mm', 'quantityAvailable': 15},
            'quote': '<script>steal()</script><b onclick="steal()">Steel</b>',
        })
        self.write(body=b'<html>Full catalog with raw price 100</html>', records=[record])
        output = self.normalize()
        bundle = output['bundles'][0]
        facts = bundle['records'][0]['facts']
        self.assertNotIn('pricing', bundle['records'][0])
        self.assertNotIn('unitPrice', bundle['records'][0])
        self.assertNotIn('currentStock', bundle['records'][0])
        self.assertNotIn('quantityAvailable', facts['nested'])
        self.assertNotIn('price', facts)
        self.assertNotIn('stock', facts['nested'])
        self.assertEqual('3 mm', facts['nested']['diameter'])
        self.assertNotIn('steal', facts['quote'])
        source = bundle['sources'][0]
        self.assertEqual('derived_metadata', source['source_kind'])
        self.assertEqual('verified_derived_metadata', source['verification'])
        self.assertTrue(source['derived_from_original_verified'])
        self.assertEqual(200, source['original_http_status'])
        self.assertEqual(source['sha256'], hashlib.sha256(source['content'].encode()).hexdigest())
        self.assertNotIn('raw price', source['content'])
        from o3d.catalog.offer_policy import contains_offer_values
        self.assertFalse(contains_offer_values(bundle['records'][0]))
        self.assertNotIn('path', source)
        self.assertTrue({'facts.price', 'facts.nested.stock', 'facts.nested.quantityAvailable', 'pricing', 'unitPrice', 'currentStock'} <= set(output['excluded_fields'][0]['excluded_paths']))


if __name__ == '__main__':
    unittest.main()
