"""Search one catalog page or download one explicitly selected STEP part."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import urlencode, quote, urlparse
from urllib.request import urlopen

MAX_BYTES = 50 * 1024 * 1024


def fetch(url, limit=MAX_BYTES):
    if urlparse(url).scheme not in ('https', 'http'):
        raise ValueError('Only HTTP(S) resources are supported')
    with urlopen(url, timeout=30) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Resource exceeds size limit')
    return data


def search(origin, query, limit):
    # API page size is named pageSize, not limit. Retain server metadata.
    return json.loads(fetch(origin.rstrip('/') + '/v1/parts?' + urlencode({'q': query, 'pageSize': limit})))


def download(origin, part_id, output):
    output = Path(output)
    if output.exists():
        raise ValueError('Output already exists; choose a new filename')
    record = json.loads(fetch(origin.rstrip('/') + '/v1/parts/' + quote(part_id, safe='')))
    if 'part' in record:
        record = record['part']
    if record.get('id') != part_id:
        raise ValueError('Catalog returned a different part id')
    content = fetch(record['stepUrl'])
    if not content.lstrip().startswith(b'ISO-10303-21;'):
        raise ValueError('Downloaded resource is not text STEP')
    actual = hashlib.sha256(content).hexdigest()
    expected = record.get('sha256')
    if expected and actual.lower() != expected.lower():
        raise ValueError('Catalog checksum mismatch; no file written')
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation also protects a destination created during the request.
    with output.open('xb') as stream:
        stream.write(content)
    return {'id': part_id, 'path': str(output.resolve()), 'bytes': len(content), 'sha256': actual,
            'catalog_checksum_verified': bool(expected), 'source': record['stepUrl'], 'page': record.get('pageUrl')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--origin', default='https://api.step.parts')
    sub = parser.add_subparsers(dest='action', required=True)
    s = sub.add_parser('search'); s.add_argument('query'); s.add_argument('--limit', type=int, default=10)
    d = sub.add_parser('download'); d.add_argument('id'); d.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.action == 'search':
            if not 1 <= args.limit <= 100:
                raise ValueError('limit must be between 1 and 100')
            result = search(args.origin, args.query, args.limit)
        else:
            result = download(args.origin, args.id, args.out)
        print(json.dumps(result, indent=2, allow_nan=False))
    except (OSError, ValueError, KeyError) as error:
        print(json.dumps({'error': str(error)}), file=sys.stderr); return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
