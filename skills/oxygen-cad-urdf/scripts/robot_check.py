"""Lint URDF structure, units-sensitive fields, resources and inertial tensors."""
import argparse
import json
import math
from pathlib import Path
import sys
from urllib.parse import unquote, urlparse
import xml.etree.ElementTree as ET


def xml(path):
    data=Path(path).read_bytes()
    if len(data)>10*1024*1024:raise ValueError('Robot XML exceeds 10 MiB')
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():raise ValueError('DTD/entities are not supported')
    return ET.fromstring(data)


def numbers(text,count=None):
    values=[float(v) for v in (text or '').split()]
    if (count is not None and len(values)!=count) or not all(math.isfinite(v) for v in values):
        raise ValueError('Wrong vector size or nonfinite value')
    return values


def determinant(a):
    return a[0][0]*(a[1][1]*a[2][2]-a[1][2]*a[2][1])-a[0][1]*(a[1][0]*a[2][2]-a[1][2]*a[2][0])+a[0][2]*(a[1][0]*a[2][1]-a[1][1]*a[2][0])


def psd(a):
    tolerance=max(max(abs(v) for row in a for v in row),1e-30)*1e-9
    return (all(a[i][i]>=-tolerance for i in range(3)) and
            all(a[i][i]*a[j][j]-a[i][j]**2>=-tolerance**2 for i in range(3) for j in range(i)) and
            determinant(a)>=-tolerance**3)


def inertia(values):
    xx,yy,zz,xy,xz,yz=values
    a=[[xx,xy,xz],[xy,yy,yz],[xz,yz,zz]]
    covariance=[[(sum((xx,yy,zz))/2 if i==j else 0)-a[i][j] for j in range(3)] for i in range(3)]
    return xx>0 and xx*yy-xy*xy>0 and determinant(a)>0 and psd(covariance)


def resource(uri,folder,packages):
    if uri.startswith('package://'):
        package,_,tail=uri[10:].partition('/')
        if package not in packages:return None
        return Path(packages[package])/unquote(tail)
    parsed=urlparse(uri)
    if parsed.scheme not in ('','file'):return None
    return Path(unquote(parsed.path)) if parsed.scheme=='file' else folder/unquote(uri)


def check(path,packages=None):
    packages=packages or {};root=xml(path);issues=[]
    def issue(code,message,severity='error'):issues.append({'severity':severity,'code':code,'message':message})
    if root.tag!='robot':raise ValueError('Expected a robot root; expand xacro before linting')
    links={};joints={};parent={}
    for element,table in [('link',links),('joint',joints)]:
        for node in root.findall(element):
            name=node.get('name')
            if not name or name in table:issue('duplicate_name',f'Missing or duplicate {element} name: {name}')
            table[name]=node
    if not root.get('name') or not links:issue('robot','Robot name and at least one link are required')
    for name,joint in joints.items():
        try:
            kind=joint.get('type');p=joint.find('parent');c=joint.find('child')
            p=p.get('link') if p is not None else None;c=c.get('link') if c is not None else None
            if kind not in ('fixed','revolute','continuous','prismatic','floating','planar'):raise ValueError('Unknown joint type')
            if p not in links or c not in links or p==c:raise ValueError('Invalid parent/child links')
            if c in parent:raise ValueError('Link has more than one parent joint')
            parent[c]=(p,name)
            axis=joint.find('axis')
            if axis is not None and sum(v*v for v in numbers(axis.get('xyz'),3))<=0:raise ValueError('Zero joint axis')
            if kind in ('revolute','prismatic','continuous'):
                limit=joint.find('limit')
                if limit is None:raise ValueError('Moving joint lacks limit element')
                for key in ('effort','velocity'):
                    if numbers(limit.get(key),1)[0]<=0:raise ValueError(f'Invalid {key} limit')
                if kind!='continuous' and numbers(limit.get('lower'),1)[0]>numbers(limit.get('upper'),1)[0]:raise ValueError('Reversed joint limits')
            mimic=joint.find('mimic')
            if mimic is not None and (mimic.get('joint') not in joints or mimic.get('joint')==name):raise ValueError('Unknown/self mimic target')
        except (ValueError,TypeError) as e:issue('joint',f'{name}: {e}')
    roots=set(links)-set(parent)
    if len(roots)!=1:issue('tree',f'Expected one root link; found {sorted(str(v) for v in roots)}')
    for link in links:
        seen=set();current=link
        while current in parent:
            if current in seen:issue('cycle',f'Link cycle through {current}');break
            seen.add(current);current=parent[current][0]
    for name,joint in joints.items():
        seen=set();current=name
        while current in joints and joints[current].find('mimic') is not None:
            if current in seen:issue('mimic_cycle',f'Mimic cycle through {current}');break
            seen.add(current);current=joints[current].find('mimic').get('joint')
    for node in root.findall('.//origin'):
        try:
            for key in ('xyz','rpy'):
                if node.get(key) is not None:numbers(node.get(key),3)
        except ValueError as e:issue('origin',str(e))
    for name,link in links.items():
        mass=link.find('inertial/mass');tensor=link.find('inertial/inertia')
        if link.find('inertial') is None:
            issue('missing_inertial',f'{name}: no inertial; acceptable for frame-only links, incomplete for a physical dynamic link','warning')
        else:
            try:
                if mass is None or tensor is None or numbers(mass.get('value'),1)[0]<=0:raise ValueError('Positive mass and inertia required')
                values=[numbers(tensor.get(key),1)[0] for key in ('ixx','iyy','izz','ixy','ixz','iyz')]
                if not inertia(values):raise ValueError('Tensor violates positive-definite or physical inertia conditions')
            except ValueError as e:issue('inertia',f'{name}: {e}')
        for geometry in link.findall('.//geometry'):
            nodes=list(geometry)
            if len(nodes)!=1:issue('geometry',f'{name}: geometry needs one shape');continue
            shape=nodes[0];kind=shape.tag
            try:
                if kind=='box':values=numbers(shape.get('size'),3)
                elif kind=='cylinder':values=[*numbers(shape.get('radius'),1),*numbers(shape.get('length'),1)]
                elif kind=='sphere':values=numbers(shape.get('radius'),1)
                elif kind=='mesh':
                    values=numbers(shape.get('scale','1 1 1'),3);uri=shape.get('filename','')
                    local=resource(uri,Path(path).resolve().parent,packages)
                    if local is None:issue('resource_unresolved',f'{name}: {uri}','warning')
                    elif not local.is_file():issue('missing_mesh',str(local))
                else:issue('unknown_geometry',f'{name}: {kind}','warning');continue
                if any(v<=0 for v in values):raise ValueError('Geometry dimensions/scale must be positive')
            except ValueError as e:issue('geometry',f'{name}: {e}')
    return {'file':str(Path(path).resolve()),'robot':root.get('name'),'links':list(links),'joints':[{'name':name,'type':j.get('type'),
            'parent':j.find('parent').get('link') if j.find('parent') is not None else None,
            'child':j.find('child').get('link') if j.find('child') is not None else None} for name,j in joints.items()],
            'issues':issues,'ok':not any(i['severity']=='error' for i in issues),
            'coverage':'Limited URDF lint; not a full schema, geometry, collision or dynamics validator',
            'unverified':['spatial placement','mesh scale correctness','joint direction','physical material data','consumer load/motion']}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('urdf',type=Path);p.add_argument('--package',action='append',default=[]);p.add_argument('--strict',action='store_true');a=p.parse_args()
    try:
        packages=dict(v.split('=',1) for v in a.package);result=check(a.urdf,packages)
        print(json.dumps(result,indent=2,allow_nan=False));return 0 if result['ok'] and not (a.strict and result['issues']) else 2
    except (OSError,ValueError,ET.ParseError) as e:
        print(json.dumps({'error':str(e)}),file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
