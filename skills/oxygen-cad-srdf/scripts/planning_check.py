"""Cross-check SRDF names, chains and states against an explicitly selected URDF."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET


def common():
    path=Path(__file__).resolve().parents[2]/'oxygen-cad-urdf/scripts/robot_check.py'
    spec=importlib.util.spec_from_file_location('oxygen_robot_check',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def check(source,urdf):
    core=common();u=core.xml(urdf);s=core.xml(source);issues=[]
    def issue(code,msg):issues.append({'severity':'error','code':code,'message':msg})
    if s.tag!='robot' or s.get('name')!=u.get('name'):issue('pair','SRDF robot name must match selected URDF')
    base=core.check(urdf)
    if not base['ok']:issue('urdf','URDF lint failed; repair it first')
    links={l.get('name') for l in u.findall('link')};joints={j.get('name'):j for j in u.findall('joint')}
    groups={};parent={j.find('child').get('link'):(j.find('parent').get('link'),name) for name,j in joints.items() if j.find('parent') is not None and j.find('child') is not None}
    for group in s.findall('group'):
        name=group.get('name')
        if not name or name in groups:issue('group','Missing/duplicate planning group')
        groups[name]=group
    resolved={}
    def group_joints(name,stack=()):
        if name in stack:issue('group_cycle',str(stack+(name,)));return set()
        if name not in groups:issue('unknown_group',str(name));return set()
        if name in resolved:return resolved[name]
        result=set()
        for child in groups[name]:
            value=child.get('name')
            if child.tag=='joint':
                if value not in joints:issue('unknown_joint',str(value))
                else:result.add(value)
            elif child.tag=='link':
                if value not in links:issue('unknown_link',str(value))
                elif value in parent:result.add(parent[value][1])
            elif child.tag=='group':result|=group_joints(value,stack+(name,))
            elif child.tag=='chain':
                start,end=child.get('base_link'),child.get('tip_link');seen=set()
                if start not in links or end not in links:issue('chain','Unknown base/tip link');continue
                while end!=start and end in parent and end not in seen:
                    seen.add(end);end,joint=parent[end];result.add(joint)
                if end!=start:issue('chain','Tip is not a descendant of base')
            else:issue('group_element',child.tag)
        resolved[name]=result;return result
    for name in groups:group_joints(name)
    for state in s.findall('group_state'):
        group=state.get('group');members=group_joints(group)
        for node in state.findall('joint'):
            name=node.get('name');joint=joints.get(name)
            if joint is None or name not in members:issue('state_joint',str(name));continue
            try:
                if joint.get('type') in ('fixed','floating','planar') or joint.find('mimic') is not None:raise ValueError('Scalar state is not valid for this joint')
                value=core.numbers(node.get('value'),1)[0];limit=joint.find('limit')
                if joint.get('type')!='continuous' and (limit is None or not float(limit.get('lower'))<=value<=float(limit.get('upper'))):raise ValueError('State outside joint limits')
            except (TypeError,ValueError) as e:issue('state',f'{name}: {e}')
    for node in s.findall('disable_collisions'):
        a,b=node.get('link1'),node.get('link2')
        if a not in links or b not in links or a==b:issue('collision_pair','Unknown/self link pair')
        if node.get('reason','').lower()=='adjacent' and not ((a in parent and parent[a][0]==b) or (b in parent and parent[b][0]==a)):issue('adjacency','Adjacent reason does not match URDF topology')
    for node in s.findall('passive_joint'):
        if node.get('name') not in joints:issue('passive_joint','Unknown passive joint')
    for node in s.findall('virtual_joint'):
        if node.get('child_link') not in links or node.get('type') not in ('fixed','planar','floating'):issue('virtual_joint','Invalid virtual joint')
    for node in s.findall('end_effector'):
        group=node.get('group');pg=node.get('parent_group')
        if group not in groups or node.get('parent_link') not in links or (pg and pg not in groups):issue('end_effector','Unknown end-effector group/link')
        if pg and group_joints(group)&group_joints(pg):issue('end_effector_overlap','End effector shares joints with parent group')
    return {'file':str(Path(source).resolve()),'urdf':str(Path(urdf).resolve()),'ok':not issues,'issues':issues,
            'groups':{name:sorted(members) for name,members in resolved.items()},
            'coverage':'Limited SRDF topology/state lint; no IK, sampled collision matrix or planning proof',
            'unverified':['group-state completeness','end-effector link overlap','IK','collision exclusions from motion sampling','MoveIt load']}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('srdf',type=Path);p.add_argument('--urdf',type=Path,required=True);a=p.parse_args()
    try:
        result=check(a.srdf,a.urdf);print(json.dumps(result,indent=2));return 0 if result['ok'] else 2
    except (OSError,ValueError,AttributeError,ET.ParseError) as e:
        print(json.dumps({'error':str(e)}),file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
