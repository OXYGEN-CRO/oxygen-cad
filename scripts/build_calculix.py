#!/usr/bin/env python3
"""Build a pinned external CalculiX with read-only contact state output.

Requires an existing GNU C/Fortran toolchain and SPOOLES/ARPACK/BLAS/LAPACK.
Installs no packages and never modifies application environments. The original
source archive, patch, build recipe and GPL text accompany the output binary.
"""
from __future__ import annotations
import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
UPSTREAM_URL='https://www.dhondt.de/ccx_2.23.src.tar.bz2'
UPSTREAM_SHA256='9c88385c10fb04f5dc6c4e98027a51bebdd8aee3920e05190d6c1dd08357d6e7'
NONLINGEO_SHA256='8684bb6d7fa7097c0a854db5a45eb9e2d11adfde574e688184ecbbbad56ff83f'
HOOK='''
      /* Read-only accepted-output snapshot, after results populated stx/ener
         and before those output buffers are freed. */
      if((*nmethod==1)&&(*mortar==1)&&(*nener==1)&&(*ithermal<2)){
        oxygen_contact_snapshot(jobnamec,*istep,iinc,*ttime+time,dtime,
          ne0,*ne,*nintpoint,*nk,co,v,vini,clearini,mi,ipkon,kon,lakon,islavsurf,pslavsurf,
          pmastsurf,springarea,stx,ener,*nstate_,xstate,xstateini);
      }
'''


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def patch_nonlingeo(original):
    if hashlib.sha256(original.encode()).hexdigest()!=NONLINGEO_SHA256:
        raise ValueError('Unexpected upstream nonlingeo.c; do not apply an approximate patch')
    text=original.replace('#define max(a,b)','#include "oxygen_contact_telemetry.h"\n\n#define max(a,b)',1)
    needle='&intscheme,physcon,dam,damn,iponoel);'
    start=text.index('    /* output */');end=text.index('  /*   end of the increment loop',start)
    part=text[start:end]
    if part.count(needle)!=1:raise ValueError('Accepted increment output hook is ambiguous')
    text=text[:start]+part.replace(needle,needle+HOOK)+text[end:]
    start=text.index('  if(jprint!=0){',text.index('  /*   end of the increment loop'))
    part=text[start:]
    if part.count(needle)!=1:raise ValueError('Final output hook is ambiguous')
    return text[:start]+part.replace(needle,needle+HOOK)


def quoted(value):return shlex.quote(str(value)).replace('$','$$')


def build(args):
    out=args.output.resolve()
    if out.exists():raise ValueError('Output already exists; choose a fresh versioned directory')
    cc=shutil.which(args.cc);fc=shutil.which(args.fc)
    if not cc or not fc:raise ValueError('An existing C and Fortran compiler are required; no system packages will be installed')
    for path in [args.spooles_include,*args.library]:
        if not path.exists():raise ValueError(f'Missing declared build dependency: {path}')
    out.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='oxygen-ccx-',dir=args.work_parent) as temporary:
        work=Path(temporary);archive=work/'ccx_2.23.src.tar.bz2'
        if args.source_archive:shutil.copyfile(args.source_archive,archive)
        else:
            with urllib.request.urlopen(UPSTREAM_URL,timeout=60) as source,archive.open('wb') as target:shutil.copyfileobj(source,target)
        if sha(archive)!=UPSTREAM_SHA256:raise ValueError('CalculiX source archive checksum mismatch')
        with tarfile.open(archive) as source:source.extractall(work,filter='data')
        source=work/'CalculiX/ccx_2.23/src'
        original=(source/'nonlingeo.c').read_text();patched=patch_nonlingeo(original)
        assets=ROOT/'third_party/calculix'
        if not assets.is_dir():assets=Path(__file__).resolve().parent
        header=assets/'oxygen_contact_telemetry.h'
        (source/'nonlingeo.c').write_text(patched);shutil.copyfile(header,source/header.name)
        makefile=(source/'Makefile').read_text()
        target='ccx_2.23: $(OCCXMAIN) ccx_2.23.a  $(LIBS)'
        if makefile.count(target)!=1 or makefile.count('./date.pl; $(CC)')!=1:raise ValueError('Unexpected upstream build recipe')
        makefile=makefile.replace(target,'ccx_2.23: $(OCCXMAIN) ccx_2.23.a').replace('./date.pl; $(CC)','$(CC)')
        makefile+='\nnonlingeo.o: oxygen_contact_telemetry.h\n'
        (source/'Makefile').write_text(makefile)
        cflags=f'-O2 -Wno-error=return-mismatch -I {quoted(args.spooles_include.resolve())} -DARCH="Linux" -DSPOOLES -DARPACK -DMATRIXSTORAGE -DNETWORKOUT'
        fflags='-O2 -cpp -fallow-argument-mismatch'
        libraries=' '.join(quoted(p.resolve()) for p in args.library)+' -lpthread -lm -lc'
        command=['make',f'-j{args.jobs}','ccx_2.23',f'CC={quoted(cc)}',f'FC={quoted(fc)}',f'CFLAGS={cflags}',f'FFLAGS={fflags}',f'LIBS={libraries}']
        subprocess.run(command,cwd=source,check=True)
        staged=work/'release';staged.mkdir()
        shutil.copyfile(source/'ccx_2.23',staged/'ccx');(staged/'ccx').chmod(0o755)
        shutil.copyfile(archive,staged/archive.name);shutil.copyfile(header,staged/header.name)
        shutil.copyfile(Path(__file__),staged/'build_calculix.py')
        shutil.copyfile(assets/'COPYING',staged/'COPYING')
        delta=''.join(difflib.unified_diff(original.splitlines(True),patched.splitlines(True),fromfile='a/nonlingeo.c',tofile='b/nonlingeo.c'))
        (staged/'nonlingeo.c.patch').write_text(delta)
        manifest={'schema':1,'telemetry_schema':1,'upstream_version':'2.23','source_url':UPSTREAM_URL,
            'source_sha256':UPSTREAM_SHA256,'upstream_nonlingeo_sha256':NONLINGEO_SHA256,
            'patched_nonlingeo_sha256':hashlib.sha256(patched.encode()).hexdigest(),
            'telemetry_header_sha256':sha(header),'build_script_sha256':sha(__file__),
            'binary':'ccx','binary_sha256':sha(staged/'ccx'),
            'c_compiler':subprocess.check_output([cc,'--version'],text=True).splitlines()[0],
            'fortran_compiler':subprocess.check_output([fc,'--version'],text=True).splitlines()[0],
            'cflags':cflags,'fflags':fflags,'libraries':{str(p.resolve()):sha(p.resolve()) for p in args.library},
            'scope':'Read-only output of accepted static C3D10 face-penalty contact state; numerical routines unchanged; not contact or physical qualification',
            'source_distribution':['ccx_2.23.src.tar.bz2','nonlingeo.c.patch','oxygen_contact_telemetry.h','build_calculix.py','COPYING']}
        (staged/'build-manifest.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
        # Publish only a complete binary/source/provenance bundle.
        pending=Path(tempfile.mkdtemp(prefix='.oxygen-ccx-release-',dir=out.parent))
        try:
            shutil.copytree(staged,pending,dirs_exist_ok=True)
            if out.exists():raise ValueError('Output appeared while building; existing directory preserved')
            pending.rename(out)
        finally:
            if pending.exists():shutil.rmtree(pending)
        return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--source-archive',type=Path)
    parser.add_argument('--work-parent',type=Path)
    parser.add_argument('--cc',default='cc');parser.add_argument('--fc',default='gfortran')
    parser.add_argument('--spooles-include',type=Path,required=True)
    parser.add_argument('--library',type=Path,action='append',required=True,help='Repeat for SPOOLES, ARPACK, LAPACK and BLAS, in link order')
    parser.add_argument('--jobs',type=int,choices=range(1,9),default=2)
    manifest=build(parser.parse_args());print(json.dumps({'binary_sha256':manifest['binary_sha256'],'telemetry_schema':manifest['telemetry_schema']}))


if __name__=='__main__':main()
