"""Prepare a Bambu application handoff; never send printer commands."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import urlencode,quote


def plan(file,app='studio',platform=sys.platform):
    path=Path(file).resolve()
    if not path.is_file():raise ValueError('File does not exist')
    if app=='connect':
        if not (path.name.lower().endswith('.gcode.3mf') or path.suffix.lower()=='.gcode'):raise ValueError('Connect handoff requires sliced gcode or gcode.3mf')
        uri='bambu-connect://import-file?'+urlencode({'path':str(path),'name':path.stem,'version':'1.0.0'},quote_via=quote,safe='')
        if platform=='darwin':command=['open',uri]
        elif platform=='win32':command=['os.startfile',uri]
        else:raise ValueError('Use Bambu Studio on this platform; Connect availability must be verified')
    else:
        if path.suffix.lower() not in ('.stl','.step','.stp','.3mf'):raise ValueError('Studio handoff expects a model file')
        uri=None
        if platform=='darwin':command=['open','-a','BambuStudio',str(path)]
        elif platform=='win32':command=['os.startfile',str(path)]
        else:command=['bambu-studio',str(path)]
    return {'argv':command,'uri':uri,'file':str(path),'app':app,'state':'planned','print_started':False}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('file',type=Path);p.add_argument('--app',choices=['studio','connect'],default='studio');p.add_argument('--open',action='store_true');a=p.parse_args()
    try:
        result=plan(a.file,a.app)
        if a.open:
            if sys.platform=='win32':
                os.startfile(result['argv'][1])
            else:
                if not shutil.which(result['argv'][0]):raise ValueError('Application opener not installed')
                subprocess.run(result['argv'],check=True,timeout=30,capture_output=True)
            result['state']='opener_returned_success'  # Not proof the application loaded the file.
        print(json.dumps(result,indent=2));return 0
    except (OSError,ValueError,subprocess.SubprocessError) as e:
        print(json.dumps({'error':str(e)}),file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
