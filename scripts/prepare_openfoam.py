#!/usr/bin/env python3
"""Extract verified Debian packages into an isolated, administrator-owned flow runtime.

No sudo, package installation, user CAD, shell rc file or HOME changes. Obtain the
exact packages listed in src/o3d/data/openfoam-laminar.json with apt-get download.
The receipt retains package and corresponding-source identities. This Linux/amd64
recipe is separate from the packaged Docker runtime; it does not prove macOS support.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def sha(path):
    value=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):value.update(chunk)
    return value.hexdigest()


def prepare(packages,output):
    contract=json.loads((Path(__file__).resolve().parents[1]/'src/o3d/data/openfoam-laminar.json').read_text())
    if output.exists():raise ValueError('Use a new isolated output directory; existing runtimes are never overwritten')
    archives=[]
    for p in contract['packages']:
        archive=packages/p['file']
        if archive.is_symlink() or not archive.is_file() or archive.stat().st_size!=p['bytes'] or sha(archive)!=p['sha256']:
            raise ValueError('Missing or changed pinned package: '+p['file'])
        archives.append(archive)
    output.mkdir(parents=True)
    for archive in archives:subprocess.run(['dpkg-deb','-x',str(archive),str(output)],check=True)
    for name,digest in contract['binaries_sha256'].items():
        if sha(output/'usr/bin'/name)!=digest:raise ValueError('Extracted OpenFOAM binary identity differs')
    files={str(p.relative_to(output)):sha(p) for folder in ['usr/lib','usr/share/openfoam/etc'] for p in sorted((output/folder).rglob('*')) if p.is_file()}
    (output/'empty-site').mkdir()
    receipt={'schema':1,'contract_sha256':sha(Path(__file__).resolve().parents[1]/'src/o3d/data/openfoam-laminar.json'),'files_sha256':files}
    (output/'oxygen-openfoam.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps({'runtime':str(output),'files':len(files),'source':contract['source']}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--packages',required=True,type=Path);parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args();prepare(args.packages.resolve(),args.output.resolve())
