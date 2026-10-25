#!/usr/bin/env python3
"""Build separate GPL verification probes from a pinned native solver archive.

No application/production entry point is called. Source, compiler, static input
and runtime-library hashes accompany the standalone element/material probes.
"""
import argparse,hashlib,json,os
from pathlib import Path
import re,shutil,subprocess

SOURCE_SHA='9c88385c10fb04f5dc6c4e98027a51bebdd8aee3920e05190d6c1dd08357d6e7'


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def libraries(binary,environment=None):
    result=subprocess.run(['ldd',str(binary)],env=environment,text=True,capture_output=True,check=True)
    paths=[]
    for line in result.stdout.splitlines():
        match=re.search(r'(?:=>\s*)?(/.*?)\s+\(0x[0-9a-fA-F]+\)',line)
        if match:paths.append(Path(match[1]).resolve())
    if not paths or 'not found' in result.stdout:raise ValueError('Probe runtime-library identity unavailable')
    return {str(p):sha(p) for p in sorted(set(paths))}


def native_tensor(packed):
    values=[[0.]*6 for _ in range(6)];k=0
    for column in range(6):
        for row in range(column+1):values[row][column]=values[column][row]=packed[k];k+=1
    return values


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--calculix-build',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--fortran-compiler',default='gfortran');args=parser.parse_args()
    if args.output.exists():raise ValueError('Choose a fresh verification output')
    receipt=json.loads((args.calculix_build/'build-manifest.json').read_text());archive=args.calculix_build/'verification-ccx_2.23.a'
    if receipt.get('source_sha256')!=SOURCE_SHA or sha(archive)!=receipt.get('verification_archive_sha256'):raise ValueError('Pinned native verification archive missing or changed; build with --retain-verification-archive')
    compiler=shutil.which(args.fortran_compiler)
    if compiler is None:raise ValueError('Existing GNU Fortran compiler required')
    args.output.mkdir(parents=True);sources=args.output/'source';sources.mkdir();files={};binaries={};runtimes={}
    source_root=Path(__file__).resolve().parent
    if not (source_root/'bulk_kernel_probe.f90').is_file():source_root=source_root/'source'
    env=dict(os.environ,LD_LIBRARY_PATH=os.pathsep.join(sorted({str(Path(p).parent) for p in receipt['libraries']})))
    for key,filename in [('bulk','bulk_kernel_probe.f90'),('material','material_kernel_probe.f90')]:
        driver=sources/filename;shutil.copyfile(source_root/filename,driver);binary=args.output.resolve()/(key+'-kernel-probe')
        command=[compiler,'-O2','-fallow-argument-mismatch','-ffree-line-length-none',str(driver.resolve()),str(archive.resolve()),*receipt['libraries'],'-lpthread','-lm','-fopenmp','-o',str(binary)]
        result=subprocess.run(command,text=True,capture_output=True);(args.output/(key+'-build.log')).write_text(result.stdout+result.stderr);result.check_returncode()
        files['source/'+filename]=sha(driver);files[binary.name]=sha(binary);binaries[key]=binary.name;runtimes[key]=libraries(binary,env)
    checks=[]
    for nonlinear in [0,1]:
        lines=[f'{nonlinear} 200000 .3 2 .1 1e-8','.01 -.002 -.002 .003 .001 -.002',' '.join(['0']*13),'0 250','.01 350']
        output=subprocess.run([str(args.output.resolve()/binaries['material'])],env=env,input='\n'.join(lines)+'\n',text=True,capture_output=True,check=True)
        numbers=[float(x.replace('D','E')) for x in output.stdout.split()]
        if len(numbers)!=148:raise ValueError('Incomplete native material derivative output')
        native=native_tensor(numbers[19:40]);fd=[numbers[40+6*i:46+6*i] for i in range(6)]
        norm=sum(x*x for row in fd for x in row)**.5;error=sum((native[i][j]-fd[i][j])**2 for i in range(6) for j in range(6))**.5/norm
        checks.append({'finite_strain':bool(nonlinear),'native_tensor_vs_native_stress_fd_relative_error':error,'force_derivative_qualified':error<1e-6,'input_lines':lines,'native_values':numbers})
    shutil.copyfile(args.calculix_build/'COPYING',args.output/'COPYING');files['COPYING']=sha(args.output/'COPYING')
    shutil.copyfile(__file__,args.output/'reference_bulk_kernel.py');files['reference_bulk_kernel.py']=sha(args.output/'reference_bulk_kernel.py')
    value={'schema':1,'source_sha256':SOURCE_SHA,'native_archive_sha256':sha(archive),'native_build_receipt_sha256':sha(args.calculix_build/'build-manifest.json'),'material_api':'native-j2-stress-148-v1','binaries':binaries,'files_sha256':files,'runtime_libraries_sha256':runtimes,'compiler':subprocess.check_output([compiler,'--version'],text=True).splitlines()[0],'material_checks':checks,'scope':'Separate unmodified native e_c3d and incplas/incplas_lin verification. A native algorithmic tensor is not automatically an exact force derivative; finite-strain discrepancy is retained, not waived.'}
    (args.output/'result.json').write_text(json.dumps(value,indent=2,sort_keys=True)+'\n');print(json.dumps({'native_archive_sha256':value['native_archive_sha256'],'material_checks':[{k:v for k,v in c.items() if k not in ['input_lines','native_values']} for c in checks]}))

if __name__=='__main__':main()
