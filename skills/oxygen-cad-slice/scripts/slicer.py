"""Resolve supplied Orca profiles and plan or run a local-only slicing command."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import zipfile


def resolve(source,profiles):
    source=Path(source);index={}
    for path in sorted(Path(profiles).rglob('*.json')):
        value=json.loads(path.read_text())
        if not isinstance(value,dict):raise ValueError(f'Preset is not an object: {path}')
        name=value.get('name',path.stem)
        index.setdefault(name,[]).append((path,value))
    def visit(path,value,stack):
        key=str(path.resolve())
        if key in stack:raise ValueError('Cyclic preset inheritance')
        parent=value.get('inherits');result={}
        if parent:
            if not isinstance(parent,str) or len(index.get(parent,[]))!=1:raise ValueError(f'Missing or ambiguous parent profile: {parent}')
            p,v=index[parent][0];result=visit(p,v,stack+(key,))
        return result|{k:v for k,v in value.items() if k!='inherits'}
    return visit(source,json.loads(source.read_text()),())


def plan(slicer,model,printer,process,filament,output):
    paths=[Path(v).resolve() for v in (model,printer,process,filament)]
    if paths[0].suffix.lower() not in ('.stl','.obj','.3mf','.amf'):raise ValueError('Export a supported mesh before slicing')
    if not all(p.is_file() for p in paths):raise ValueError('Model and all three resolved presets must exist')
    for path in paths[1:]:
        preset=json.loads(path.read_text())
        if not isinstance(preset,dict):raise ValueError('Presets must be JSON objects')
        if preset.get('inherits'):raise ValueError('Resolve preset inheritance first')
        if ';' in str(path):raise ValueError('Preset filenames cannot contain semicolons in Orca CLI')
    output=Path(output).resolve()
    if output.exists():raise ValueError('Use a fresh output directory to avoid accepting stale slices')
    return [str(slicer),str(paths[0]),'--load-settings',str(paths[2])+';'+str(paths[1]),'--load-filaments',str(paths[3]),
            '--arrange','1','--slice','0','--outputdir',str(output),'--export-3mf','job.gcode.3mf']


def slice_outputs(directory):
    result=[]
    for path in Path(directory).glob('*.gcode'):
        if path.stat().st_size>0:result.append(path)
    for path in Path(directory).glob('*.gcode.3mf'):
        try:
            with zipfile.ZipFile(path) as archive:
                if any(item.filename.lower().endswith('.gcode') and item.file_size>0 for item in archive.infolist()):
                    result.append(path)
        except zipfile.BadZipFile:
            continue
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    r=sub.add_parser('resolve');r.add_argument('preset',type=Path);r.add_argument('--profiles',type=Path,required=True);r.add_argument('--out',type=Path,required=True)
    s=sub.add_parser('slice');s.add_argument('model',type=Path);s.add_argument('--slicer',required=True)
    for key in ('printer','process','filament','out'):s.add_argument('--'+key,type=Path,required=True)
    s.add_argument('--run',action='store_true');a=p.parse_args()
    try:
        if a.action=='resolve':
            value=resolve(a.preset,a.profiles);a.out.parent.mkdir(parents=True,exist_ok=True)
            with a.out.open('x') as stream:json.dump(value,stream,indent=2)
            result={'output':str(a.out.resolve()),'name':value.get('name')}
        else:
            command=plan(a.slicer,a.model,a.printer,a.process,a.filament,a.out)
            result={'argv':command,'executed':False,'printer_contacted':False}
            if a.run:
                a.out.mkdir(parents=True);process=subprocess.run(command,text=True,capture_output=True,timeout=600)
                outputs=slice_outputs(a.out)
                if process.returncode or not outputs:raise ValueError('Slicer failed or produced no slice: '+(process.stdout+process.stderr)[-5000:])
                result|={'executed':True,'outputs':[str(p.resolve()) for p in outputs],'log':(process.stdout+process.stderr)[-5000:]}
        print(json.dumps(result,indent=2));return 0
    except (OSError,ValueError,subprocess.TimeoutExpired) as e:
        print(json.dumps({'error':str(e)}),file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
