"""Bounded reader for generated OpenFOAM ASCII fields, without directive evaluation."""
from pathlib import Path
import re
import numpy as np


def dictionary(path):
    path=Path(path)
    if path.is_symlink() or path.stat().st_size>200_000_000:raise ValueError('Unsafe or oversized native flow field')
    value=path.read_text()
    value=re.sub(r'/\*.*?\*/|//[^\n]*','',value,flags=re.S)
    if '#' in value or '$' in value:raise ValueError('Native field directives and substitutions are not accepted')
    # Native location is quoted; no arbitrary include/library/function is evaluated.
    tokens=re.findall(r'"[^"\n]*"|[^\s;{}()\[\]]+|[;{}()\[\]]',value)
    position=0
    def read(nested=False,depth=0):
        nonlocal position
        if depth>8:raise ValueError('Native field dictionary is too deeply nested')
        result={}
        while position<len(tokens):
            key=tokens[position];position+=1
            if key=='}':
                if not nested:raise ValueError('Unexpected native field dictionary closure')
                return result
            if key in result or key in {'{',';','(',')','[',']'}:raise ValueError('Duplicate or malformed native field entry')
            if position>=len(tokens):raise ValueError('Truncated native field entry')
            if tokens[position]=='{':
                position+=1;result[key]=read(True,depth+1)
            else:
                start=position;stack=[]
                while position<len(tokens) and (tokens[position]!=';' or stack):
                    token=tokens[position];position+=1
                    if token in {'(','['}:stack.append(token)
                    elif token in {')',']'}:
                        if not stack or stack.pop()!=('(' if token==')' else '['):raise ValueError('Malformed native field list')
                    elif token in {'{','}'}:raise ValueError('Truncated native field value')
                if position>=len(tokens) or stack:raise ValueError('Truncated native field value')
                result[key]=tokens[start:position];position+=1
        if nested:raise ValueError('Truncated native field dictionary')
        return result
    result=read()
    if result.get('FoamFile',{}).get('format')!=['ascii']:raise ValueError('Only native ASCII fields are accepted')
    return result


def values(tokens,count,components):
    if not isinstance(tokens,list) or not tokens:raise ValueError('Missing native field values')
    if tokens[0]=='uniform':
        flat=tokens[1:]
        if components==3:
            if len(flat)!=5 or flat[0]!='(' or flat[-1]!=')':raise ValueError('Malformed uniform native vector')
            flat=flat[1:-1]
        elif len(flat)!=1:raise ValueError('Malformed uniform native scalar')
        result=np.tile(np.array(flat,dtype=float),(count,1)) if components==3 else np.full(count,float(flat[0]))
    elif tokens[:2]==['nonuniform',f'List<{"vector" if components==3 else "scalar"}>']:
        if len(tokens)<5 or tokens[2]!=str(count) or tokens[3]!='(' or tokens[-1]!=')':raise ValueError('Native field count differs from its control mesh')
        flat=tokens[4:-1]
        if components==3:
            if len(flat)!=5*count or any(flat[i]!='(' or flat[i+4]!=')' for i in range(0,len(flat),5)):raise ValueError('Malformed nonuniform native vectors')
            result=np.array([flat[i+1:i+4] for i in range(0,len(flat),5)],dtype=float)
        else:
            if len(flat)!=count:raise ValueError('Malformed nonuniform native scalars')
            result=np.array(flat,dtype=float)
    else:raise ValueError('Unsupported native field encoding')
    if not np.isfinite(result).all():raise ValueError('Native flow field contains nonfinite values')
    return result


def read_field(path,count,components,dimensions,kind):
    data=dictionary(path)
    if data.get('dimensions')!=['[',*[str(v) for v in dimensions],']'] or data['FoamFile'].get('class')!=[kind]:
        raise ValueError('Native flow field dimensions or class differ from the declared SI physics')
    return values(data.get('internalField'),count,components),data.get('boundaryField',{})
