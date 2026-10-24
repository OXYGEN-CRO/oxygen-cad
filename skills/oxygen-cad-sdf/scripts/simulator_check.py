"""Lint local SDFormat frames/resources and optionally invoke installed gz sdf."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET


def core():
    p=Path(__file__).resolve().parents[2]/'oxygen-cad-urdf/scripts/robot_check.py'
    spec=importlib.util.spec_from_file_location('oxygen_robot_common',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def check(path,gz_check='auto'):
    c=core();root=c.xml(path);issues=[]
    def issue(code,msg,severity='error'):issues.append({'severity':severity,'code':code,'message':msg})
    if root.tag!='sdf' or not root.get('version'):raise ValueError('Expected sdf root with a target version')
    models=root.findall('model')+root.findall('world/model')
    if not models and root.find('world') is None:issue('document','Expected model or world')
    for model in models:
        nodes=list(model.findall('link'))+list(model.findall('frame'))+list(model.findall('joint'))
        names=[node.get('name') for node in nodes]
        if any(not name for name in names) or len(set(names))!=len(names):issue('names','Missing/duplicate model entity names')
        known=set(names)|{'__model__','world',model.get('name')};links={n.get('name') for n in model.findall('link')};graph={};attached={}
        for node in nodes:
            name=node.get('name');pose=node.find('pose')
            if pose is not None:
                try:c.numbers(pose.text,7 if pose.get('rotation_format')=='quat_xyzw' else 6)
                except ValueError as e:issue('pose',f'{name}: {e}')
                reference=pose.get('relative_to')
                if reference:
                    if '::' in reference:issue('scoped_frame',f'{reference}: nested frame resolution requires libsdformat','warning')
                    elif reference not in known:issue('unknown_frame',reference)
                    else:graph[name]=reference
            if node.tag=='frame':
                reference=node.get('attached_to','__model__');attached[name]=reference
                if reference not in known:issue('attached_to',str(reference))
                if name not in graph:graph[name]=reference
            elif node.tag=='joint':
                p=node.findtext('parent');child=node.findtext('child')
                if p not in links|{'world'} or child not in links or p==child:issue('joint','Unknown/self parent or child')
                if name not in graph:graph[name]=child
                for axis in node.findall('axis')+node.findall('axis2'):
                    xyz=axis.find('xyz')
                    try:
                        if xyz is None or sum(v*v for v in c.numbers(xyz.text,3))<=0:raise ValueError('Missing/zero axis')
                        frame=xyz.get('expressed_in')
                        if frame and frame not in known:issue('axis_frame',frame)
                    except ValueError as e:issue('axis',str(e))
            elif name not in graph:graph[name]='__model__'
        for label,mapping in [('pose',graph),('attachment',attached)]:
            for start in mapping:
                current=start;seen=set()
                while current in mapping:
                    if current in seen:issue('frame_cycle',f'{label}: {current}');break
                    seen.add(current);current=mapping[current]
        if model.findall('model'):issue('nested_models','Nested models need libsdformat validation','warning')
        for link in model.findall('link'):
            inertial=link.find('inertial')
            if inertial is None:issue('inertial',f'{link.get("name")}: no explicit inertia; validate consumer defaults','warning')
            elif inertial.get('auto')=='true':issue('auto_inertia','Automatic inertia requires target-consumer evaluation','warning')
            else:
                try:
                    if c.numbers(inertial.findtext('mass'),1)[0]<=0:raise ValueError('Mass must be positive')
                    values=[c.numbers(inertial.findtext('inertia/'+key),1)[0] for key in ('ixx','iyy','izz','ixy','ixz','iyz')]
                    if not c.inertia(values):raise ValueError('Nonphysical inertia tensor')
                except ValueError as e:issue('inertia',str(e))
    for uri in root.findall('.//mesh/uri'):
        local=c.resource(uri.text or '',Path(path).resolve().parent,{})
        if local is None:issue('resource_unresolved',uri.text or 'empty URI','warning')
        elif not local.is_file():issue('missing_mesh',str(local))
    external={'state':'not_run'};executable=shutil.which('gz')
    if gz_check!='never' and executable:
        result=subprocess.run([executable,'sdf','--check',str(Path(path).resolve())],text=True,capture_output=True,timeout=30)
        external={'state':'passed' if result.returncode==0 else 'failed','output':(result.stdout+result.stderr)[-8000:]}
        if result.returncode:issue('gz_check','libsdformat check failed')
    elif gz_check=='required':issue('gz_missing','gz not installed');external['state']='unavailable'
    elif gz_check=='auto':external['state']='unavailable'
    return {'file':str(Path(path).resolve()),'version':root.get('version'),'ok':not any(i['severity']=='error' for i in issues),
            'issues':issues,'gz_check':external,'coverage':'Limited local frame/resource/inertia lint plus separately reported consumer check',
            'unverified':['full SDFormat schema without gz','plugins/sensors','dynamic behavior','spatial placement','remote includes']}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('sdf',type=Path);p.add_argument('--gz-check',choices=['auto','required','never'],default='auto');a=p.parse_args()
    try:
        result=check(a.sdf,a.gz_check);print(json.dumps(result,indent=2));return 0 if result['ok'] else 2
    except (OSError,ValueError,ET.ParseError,subprocess.TimeoutExpired) as e:
        print(json.dumps({'error':str(e)}),file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
