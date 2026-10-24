"""Electrical design domain: one editable source shared by browser/API/agent.

Authoring operations return {changed: True, data: ...}; the application applies
that candidate through Store with the original revision. Worker results carry a
source_hash/revision and are stale when either changes. Importing this module
never launches external tools or loads native CAD libraries.
"""
from __future__ import annotations
import copy
import hashlib
import json
import math
from .eda.schema import Document, LibraryPart, Component, Pad, Pin, SpiceModel, digest
from .eda.checks import erc, drc, mcad, flattened, harness_report


def validate(data):
    return Document.model_validate(data or {}).model_dump(mode='json')


def validate_transition(before,after,actor='agent'):
    """Host hook, including direct domain-save/whole-project replace paths."""
    old=Document.model_validate(before or {});new=Document.model_validate(after or {})
    if actor!='agent':return
    for collection in ('components','board.placements','board.tracks','board.vias'):
        a=old;b=new
        for key in collection.split('.'):a=getattr(a,key);b=getattr(b,key)
        replacements={x.id:x for x in b}
        for item in a:
            if item.locked and (item.id not in replacements or replacements[item.id]!=item):
                raise ValueError(f'{collection}/{item.id} is locked by the user')


def library_diff(doc,payload):
    old=next((p for p in doc.library if p.id==payload['library_id'] and p.version==payload['from_version']),None)
    if old is None:raise ValueError('Original library version not found')
    new=LibraryPart.model_validate(payload['part']);oldpins={p.id:p.number for p in old.pins};newpins={p.id:p.number for p in new.pins}
    oldmap={p.id:(p.pin_id,p.number) for p in old.pads};newmap={p.id:(p.pin_id,p.number) for p in new.pads}
    errors=[]
    if oldpins!=newpins:errors.append('Symbol pin identity/numbering changed')
    if oldmap!=newmap:errors.append('Physical pad-to-pin identity changed')
    for rating in ('voltage_rating','current_rating'):
        a,b=getattr(old,rating),getattr(new,rating)
        if a is not None and (b is None or b<a):errors.append(f'{rating} is missing or reduced')
    fields=[k for k,v in old.model_dump().items() if new.model_dump().get(k)!=v]
    return {'compatible':not errors,'errors':errors,'changed_fields':fields,'before':old.model_dump(mode='json'),'after':new.model_dump(mode='json'),'affected_components':[c.id for c in doc.components if (c.library_id,c.library_version)==(old.id,old.version)]}


def package(payload):
    """Original SOIC pad generator; dimensions must come from attributed input."""
    count=int(payload['pin_count']);pitch=float(payload['pitch']);body=tuple(map(float,payload['body_size']));row=float(payload['row_spacing']);size=tuple(map(float,payload['pad_size']))
    if count<4 or count>128 or count%2 or min(pitch,row,*body,*size)<=0:raise ValueError('SOIC requires even pin count and positive datasheet dimensions')
    if size[1]>=pitch:raise ValueError('Adjacent package pads overlap at the requested pitch')
    pins=[];pads=[];n=count//2
    for i in range(count):
        left=i<n;index=i if left else count-1-i;pid=f'p{i+1}';at=(-row/2 if left else row/2,(index-(n-1)/2)*pitch)
        pins.append(Pin(id=pid,number=str(i+1),at=(-body[0]/2-2 if left else body[0]/2+2,at[1])))
        pads.append(Pad(id=f'pad{i+1}',pin_id=pid,number=str(i+1),at=at,size=size))
    margin=float(payload.get('courtyard_margin',0.25));w=max(body[0],row+size[0])/2+margin;h=max(body[1],(n-1)*pitch+size[1])/2+margin
    return LibraryPart(id=payload['id'],version=payload['version'],name=payload.get('name',f'SOIC-{count}'),source=payload['source'],licence=payload['licence'],pins=pins,pads=pads,body_size=body,courtyard=[(-w,-h),(w,-h),(w,h),(-w,h)])


def example():
    """Original four-layer RC test design; deliberately unrouted for editing."""
    def part(lid,name,kind,size,spacing):
        return {'id':lid,'version':'1','name':name,'source':'Oxygen original demonstration geometry; not a qualified purchasable part','licence':'MIT','pins':[{'id':'p1','number':'1','name':'+','electrical':'power_out' if kind=='V' else 'passive','at':[-3,0]},{'id':'p2','number':'2','name':'-','at':[3,0]}],'pads':[{'id':'pad1','pin_id':'p1','number':'1','at':[-spacing/2,0],'size':size},{'id':'pad2','pin_id':'p2','number':'2','at':[spacing/2,0],'size':size}],'body_size':[spacing,1.5],'spice':{'kind':kind,'pin_order':['p1','p2']}}
    return validate({'name':'Four-layer RC sensor test board','library':[part('voltage','Voltage source fixture','V',[1.5,1.5],4),part('resistor','Original two-pad resistor','R',[1.2,1.4],3),part('capacitor','Original two-pad capacitor','C',[1.2,1.4],3)],'components':[{'id':'v1','sheet_id':'root','library_id':'voltage','library_version':'1','reference':'V1','value':'5','at':[10,10]},{'id':'r1','sheet_id':'root','library_id':'resistor','library_version':'1','reference':'R1','value':'1k','at':[25,10]},{'id':'c1','sheet_id':'root','library_id':'capacitor','library_version':'1','reference':'C1','value':'1u','at':[40,10]}],'nets':[{'id':'vin','sheet_id':'root','name':'VIN','members':[{'component_id':'v1','pin_id':'p1'},{'component_id':'r1','pin_id':'p1'}]},{'id':'vout','sheet_id':'root','name':'OUT','members':[{'component_id':'r1','pin_id':'p2'},{'component_id':'c1','pin_id':'p1'}]},{'id':'gnd','sheet_id':'root','name':'GND','global_name':True,'members':[{'component_id':'v1','pin_id':'p2'},{'component_id':'c1','pin_id':'p2'}]}],'board':{'outline':[[0,0],[40,0],[40,30],[0,30]],'cutouts':[[[20,20],[24,20],[24,24],[20,24]]],'holes':[{'id':'mount1','at':[3,3],'diameter':1.2},{'id':'mount2','at':[37,27],'diameter':1.2},{'id':'slot','at':[8,25],'diameter':1,'plated':True,'slot':[2,0]}],'stackup':[{'id':'F.Cu','kind':'copper','thickness':0.035},{'id':'prepreg1','kind':'dielectric','thickness':0.2,'material':'FR4','epsilon_r':4.5},{'id':'In1.Cu','kind':'copper','thickness':0.035},{'id':'core','kind':'dielectric','thickness':1.06,'material':'FR4','epsilon_r':4.5},{'id':'In2.Cu','kind':'copper','thickness':0.035},{'id':'prepreg2','kind':'dielectric','thickness':0.2,'material':'FR4','epsilon_r':4.5},{'id':'B.Cu','kind':'copper','thickness':0.035}],'placements':[{'id':'pv1','component_id':'v1','at':[8,10]},{'id':'pr1','component_id':'r1','at':[18,10]},{'id':'pc1','component_id':'c1','at':[28,10],'side':'bottom','rotation':90}]},'variants':[{'id':'no_cap','name':'DNP capacitor','dnp':['c1']} ]})


def edit(doc,payload):
    data=doc.model_dump(mode='json');changes=payload.get('changes',[])
    if not changes or len(changes)>500:raise ValueError('Edit requires 1–500 explicit changes')
    collections={'library','sheets','components','nets','buses','wires','no_connect','rules','differential_pairs','variants','waivers','mates','harness','board.placements','board.tracks','board.vias','board.holes','board.zones','board.stackup'}
    fields={'name','board.outline','board.cutouts','board.origin','board.transform','board.outline_owner','board.mechanical_occurrence_id','process'}
    for change in changes:
        path=change['collection'];action=change['action'];target=data
        keys=path.split('.')
        for key in keys[:-1]:target=target[key]
        key=keys[-1]
        if action=='set' and path in fields:target[key]=change['value'];continue
        if path not in collections:raise ValueError('Collection is not authorable')
        items=target[key]
        def matches(item):
            if path=='no_connect':return f"{item['component_id']}/{item['pin_id']}"==change['id']
            if path=='waivers':return item['finding_id']==change['id']
            if path=='library':return item['id']==change['id'] and item['version']==change.get('version',change.get('value',{}).get('version'))
            return item['id']==change['id']
        indexes=[i for i,item in enumerate(items) if matches(item)]
        if action=='remove':
            if not indexes:raise ValueError('Cannot remove missing entity')
            items.pop(indexes[0])
        elif action=='upsert':
            value=change['value']
            expected=value.get('finding_id') if path=='waivers' else f"{value['component_id']}/{value['pin_id']}" if path=='no_connect' else value.get('id')
            if expected!=change['id']:raise ValueError('Entity ID must match edit selector')
            if indexes:items[indexes[0]]=value
            else:items.append(value)
        else:raise ValueError('Unsupported edit action')
    return Document.model_validate(data)


def instantiate(doc,payload):
    """Clone an authored sheet subtree, remapping all local IDs and ports."""
    source=payload['sheet_id'];suffix=payload['suffix']
    if not suffix or not suffix.replace('_','').isalnum():raise ValueError('Module instance suffix must be alphanumeric')
    if source not in {s.id for s in doc.sheets}:raise ValueError('Unknown module sheet')
    selected={source}
    while True:
        more={s.id for s in doc.sheets if s.parent_id in selected}
        if more<=selected:break
        selected|=more
    data=doc.model_dump(mode='json');mapping={s:s+'_'+suffix for s in selected};nets={n.id:n.id+'_'+suffix for n in doc.nets if n.sheet_id in selected};comps={c.id:c.id+'_'+suffix for c in doc.components if c.sheet_id in selected};ports=payload.get('ports',{})
    for original in doc.sheets:
        if original.id not in selected:continue
        s=original.model_dump(mode='json');s['id']=mapping[s['id']];s['name']+=' '+suffix;s['parent_id']=mapping.get(s['parent_id'],payload.get('parent_id',s['parent_id']))
        for p in s['ports']:
            p['id']+='_'+suffix;p['net_id']=nets[p['net_id']];p['parent_net_id']=nets.get(p['parent_net_id'],ports.get(p['name'],p['parent_net_id']))
        data['sheets'].append(s)
    for c in doc.components:
        if c.id in comps:
            c=c.model_dump(mode='json');c['id']=comps[c['id']];c['sheet_id']=mapping[c['sheet_id']];c['reference']+='_'+suffix;c['at']=[c['at'][0]+float(payload.get('offset_x',0)),c['at'][1]+float(payload.get('offset_y',15))];data['components'].append(c)
    for n in doc.nets:
        if n.id in nets:
            n=n.model_dump(mode='json');n['id']=nets[n['id']];n['sheet_id']=mapping[n['sheet_id']]
            for m in n['members']:m['component_id']=comps[m['component_id']]
            data['nets'].append(n)
    for collection in ('wires','buses'):
        for obj in getattr(doc,collection):
            if obj.sheet_id not in selected:continue
            value=obj.model_dump(mode='json');value['id']+='_'+suffix;value['sheet_id']=mapping[obj.sheet_id]
            if collection=='buses':value['net_ids']=[nets[n] for n in value['net_ids']]
            else:
                value['net_id']=nets[value['net_id']];value['junction_ids']=[j+'_'+suffix for j in value['junction_ids']]
                for ref in ('start','end'):
                    if value[ref]:value[ref]['component_id']=comps[value[ref]['component_id']]
                value['points']=[[p[0]+float(payload.get('offset_x',0)),p[1]+float(payload.get('offset_y',15))] for p in value['points']]
            data[collection].append(value)
    for nc in doc.no_connect:
        if nc.component_id in comps:data['no_connect'].append({'component_id':comps[nc.component_id],'pin_id':nc.pin_id})
    return Document.model_validate(data)


def execute(store,project,operation,payload,cancel=None):
    doc=Document.model_validate(getattr(project,'domains',{}).get('electronics',{}));payload=payload or {};actor=payload.get('actor','agent');new=None;extra={}
    if payload.get('expected_source_hash') and payload['expected_source_hash']!=digest(doc):
        raise ValueError('Electrical source conflict: design changed since the candidate was prepared')
    if operation=='example':new=Document.model_validate(example())
    elif operation=='inspect':return {'source_hash':digest(doc),'revision':project.revision,'netlist':flattened(doc),'library':[{'id':p.id,'version':p.version,'pins':len(p.pins),'pads':len(p.pads),'model3d':'available' if p.model3d else 'missing','source':p.source,'licence':p.licence} for p in doc.library],'stack_thickness_mm':sum(l.thickness for l in doc.board.stackup)}
    elif operation=='edit':new=edit(doc,payload)
    elif operation=='instantiate':new=instantiate(doc,payload)
    elif operation=='package':
        part=package(payload);new=edit(doc,{'changes':[{'collection':'library','action':'upsert','id':part.id,'version':part.version,'value':part.model_dump(mode='json')}]})
    elif operation=='library_diff':return library_diff(doc,payload)
    elif operation=='substitute':
        diff=library_diff(doc,payload)
        if not diff['compatible']:raise ValueError('Incompatible library substitution: '+'; '.join(diff['errors']))
        data=doc.model_dump(mode='json');part=LibraryPart.model_validate(payload['part'])
        if any(p['id']==part.id and p['version']==part.version for p in data['library']):raise ValueError('Library version already exists; publish a new version')
        data['library'].append(part.model_dump(mode='json'))
        for c in data['components']:
            if c['id'] in diff['affected_components']:c['library_id']=part.id;c['library_version']=part.version
        new=Document.model_validate(data);extra={'diff':diff}
    elif operation=='route':
        from .eda.router import route
        new,extra=route(doc,payload,cancel)
    elif operation in ('erc','drc','mcad','harness'):
        return {'revision':project.revision,**{'erc':erc,'drc':drc,'mcad':mcad,'harness':harness_report}[operation](doc)}
    elif operation=='assembly':
        from .eda.workers import assembly
        return {'revision':project.revision,**assembly(doc,payload.get('variant'))}
    elif operation=='simulate':
        from .eda.workers import simulate
        return {'revision':project.revision,**simulate(store,doc,payload,cancel)}
    elif operation in ('kicad_drc','fabrication','release','export_step'):
        from .eda.workers import kicad
        return {'revision':project.revision,**kicad(store,doc,operation,payload,cancel)}
    elif operation=='export':
        from .eda.workers import folder,write_sources,artifacts
        path=folder(store,doc,'source');write_sources(doc,path,payload.get('variant'))
        return {'revision':project.revision,'state':'exported','source_hash':digest(doc),'artifacts':artifacts(store,path)}
    elif operation=='import':
        from .eda.kicad import import_board
        fmt=payload.get('format');text=payload.get('text','')
        if not isinstance(text,str) or len(text)>20_000_000:raise ValueError('Import requires text limited to 20 MB')
        if fmt=='oxygen':new=Document.model_validate_json(text);extra={'loss_report':{'findings':[],'requires_review':False}}
        elif fmt=='kicad_board':
            candidate,loss=import_board(text)
            sha=hashlib.sha256(text.encode()).hexdigest();path=store.cache/'electronics'/'imports'/sha;path.mkdir(parents=True,exist_ok=True);original=path/'original.kicad_pcb'
            if not original.exists():original.write_text(text)
            candidate.provenance.append({'format':fmt,'sha256':sha,'original_artifact':str(original.relative_to(store.cache)),'licence':payload.get('licence','unspecified'),'loss_report':loss})
            extra={'loss_report':loss,'candidate':candidate.model_dump(mode='json')}
            if not payload.get('accept_loss'):return {'changed':False,**extra,'state':'review_required'}
            new=candidate
        else:raise ValueError(f'Unsupported editable import format {fmt!r}; EAGLE, hierarchical KiCad schematic and Altium import are not qualified')
    else:raise ValueError(f'Unknown electronics operation: {operation}')
    new=Document.model_validate(new.model_dump());validate_transition(doc.model_dump(),new.model_dump(),actor)
    return {'changed':True,'data':new.model_dump(mode='json'),'previous_source_hash':digest(doc),'source_hash':digest(new),'revision':project.revision,**extra}
