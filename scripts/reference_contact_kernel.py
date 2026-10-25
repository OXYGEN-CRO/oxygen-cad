#!/usr/bin/env python3
"""Compile exact upstream contact kernels and retain independent native derivatives.

Uses an existing GNU Fortran compiler. Installs nothing; does not modify or invoke
the production solver. The resulting JSON records original source/driver/compiler
identity, native force/stiffness, and native force finite differences.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import tarfile

SOURCE_SHA='9c88385c10fb04f5dc6c4e98027a51bebdd8aee3920e05190d6c1dd08357d6e7'
MEMBERS=['springforc_f2f.f','springstiff_f2f.f','shape3tri.f','shape4q.f','shape6tri.f','shape8q.f',
         'materialdata_sp.f','ident.f','ident2.f','plcopy.f','plmix.f','plinterpol.f','gauss.f']


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dot(a,b):return sum(x*y for x,y in zip(a,b))


def cases():
    values=[]
    for normal in [[1.,0.,0.],[.36,.48,.8]]:
        axis=[1.,0.,0.] if abs(normal[0])<.9 else [0.,0.,1.]
        tangent=[v-dot(axis,normal)*n for v,n in zip(axis,normal)];size=math.sqrt(dot(tangent,tangent));tangent=[v/size for v in tangent]
        cross=[normal[1]*tangent[2]-normal[2]*tangent[1],normal[2]*tangent[0]-normal[0]*tangent[2],normal[0]*tangent[1]-normal[1]*tangent[0]]
        for name,slip,mu in [('frictionless',.001,0.),('stick',1e-5,.3),('sliding',.001,.3),('reverse',-.001,.3)]:
            jump=[-25/1e7*n+slip*(t+.4*c) for n,t,c in zip(normal,tangent,cross)]
            plastic=[(.0002*t if name=='reverse' else 0.) for t in tangent]
            old=plastic+plastic+plastic
            values.append({'name':name+('-rotated' if normal[0]!=1 else ''),'normal':normal,'relative_displacement_mm':jump,
                'initial_state':old,'normal_penalty_n_mm3':1e7,'tangential_penalty_n_mm3':1e5,'friction_coefficient':mu,'area_mm2':.5})
    return values


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-archive',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--fortran-compiler',default='gfortran');args=parser.parse_args()
    if args.output.exists():raise ValueError('Choose a fresh verification directory')
    if sha(args.source_archive)!=SOURCE_SHA:raise ValueError('Upstream source archive differs from the pinned CalculiX2.23')
    compiler=shutil.which(args.fortran_compiler)
    if not compiler:raise ValueError('An existing GNU Fortran compiler is required')
    args.output.mkdir(parents=True);source=args.output/'source';source.mkdir()
    with tarfile.open(args.source_archive) as archive:
        for name in MEMBERS:
            matches=[m for m in archive.getmembers() if m.name.endswith('/src/'+name)]
            if len(matches)!=1:raise ValueError('Missing/ambiguous upstream kernel source')
            (source/name).write_bytes(archive.extractfile(matches[0]).read())
    driver=Path(__file__).with_name('contact_kernel_probe.f90');shutil.copyfile(driver,source/driver.name)
    binary=args.output.resolve()/'contact-kernel-probe'
    command=[compiler,'-O2','-fallow-argument-mismatch','-ffree-line-length-none','-I',str(source.resolve()),
             str(source.resolve()/driver.name),*[str(source.resolve()/name) for name in MEMBERS if name!='gauss.f'],'-o',str(binary)]
    subprocess.run(command,check=True,capture_output=True,text=True)
    results=[]
    for case in cases():
        values=[*case['normal'],*case['relative_displacement_mm'],*case['initial_state'],case['normal_penalty_n_mm3'],case['tangential_penalty_n_mm3'],case['friction_coefficient'],case['area_mm2']]
        output=subprocess.run([str(binary)],input=' '.join(format(v,'.17g') for v in values)+'\n',text=True,capture_output=True,check=True).stdout
        numbers=[float(v.replace('D','E')) for v in output.split()]
        if len(numbers)!=36 or not all(math.isfinite(v) for v in numbers):raise ValueError('Native contact probe output is incomplete/nonfinite')
        stiffness=[numbers[18+3*i:21+3*i] for i in range(3)];difference=[numbers[27+3*i:30+3*i] for i in range(3)]
        error=math.sqrt(sum((a-b)**2 for x,y in zip(stiffness,difference) for a,b in zip(x,y)))/math.sqrt(sum(v*v for row in stiffness for v in row))
        if error>1e-6:raise ValueError('Native tangent differs from native force finite differences')
        results.append(case|{'force_n':numbers[:3],'contact_displacement_stress':numbers[3:9],'state':numbers[9:18],
            'stiffness_n_mm':stiffness,'force_finite_difference_n_mm':difference,'finite_difference_relative_error':error})
    value={'schema':1,'upstream':'CalculiX2.23 unmodified springforc_f2f/springstiff_f2f','source_sha256':SOURCE_SHA,
        'source_members_sha256':{name:sha(source/name) for name in MEMBERS},'driver_sha256':sha(driver),
        'compiler':subprocess.check_output([compiler,'--version'],text=True).splitlines()[0],'binary_sha256':sha(binary),'cases':results,
        'scope':'Local closed-contact frozen-map force/tangent and finite differences only; not global/current-contact stability or physical calibration'}
    (args.output/'result.json').write_text(json.dumps(value,indent=2)+'\n');print(json.dumps({'cases':len(results),'maximum_difference':max(r['finite_difference_relative_error'] for r in results)}))


if __name__=='__main__':main()
