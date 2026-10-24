"""Editable KiCad 8/9 board boundary, with explicit translation-loss inventory.

KiCad is executed independently (GPL-3.0-or-later). No KiCad libraries/assets are
redistributed by this adapter. The authored footprint geometry is original.
"""
from __future__ import annotations
import json
import math
import re
import uuid
from .schema import Document, LibraryPart, Pin, Pad, Component, Placement, Track, Via, Hole, Layer, Zone
from .checks import flattened, net_map, pad_world, placed_layers, rotate


def q(value): return json.dumps(str(value),ensure_ascii=False)
def uid(value): return str(uuid.uuid5(uuid.NAMESPACE_URL,'oxygen-eda/'+value))
def num(value): return f'{value:.9f}'.rstrip('0').rstrip('.') or '0'


def export_board(doc):
    """Generate a fully editable board; connectivity and pad identity are retained.

    Footprints carry stable Oxygen IDs and library version properties. Pad local
    geometry is pre-transformed for bottom mirroring; KiCad source coordinates
    follow its XY board convention. Placement export documents its Y inversion.
    """
    board=doc.board; layers=[l.id for l in board.stackup if l.kind=='copper']; aliases,pins=net_map(doc)
    netlist=flattened(doc); nets={n['id']:i+1 for i,n in enumerate(netlist)}; names={n['id']:n['name'] for n in netlist}
    lnums={name:(0 if i==0 else 31 if i==len(layers)-1 else i) for i,name in enumerate(layers)}
    layer_defs=' '.join(f'({lnums[l]} {q(l)} signal)' for l in layers)
    layer_defs += ' (32 "B.Adhes" user) (33 "F.Adhes" user) (34 "B.Paste" user) (35 "F.Paste" user) (36 "B.SilkS" user) (37 "F.SilkS" user) (38 "B.Mask" user) (39 "F.Mask" user) (44 "Edge.Cuts" user) (46 "B.CrtYd" user) (47 "F.CrtYd" user) (48 "B.Fab" user) (49 "F.Fab" user)'
    out=['(kicad_pcb (version 20240108) (generator "oxygen_cad")',f'(general (thickness {num(sum(l.thickness for l in board.stackup))}))','(paper "A4")',f'(layers {layer_defs})']
    stack=[]
    for i,l in enumerate(board.stackup):
        typ='copper' if l.kind=='copper' else 'core'
        stack.append(f'(layer {q(l.id if l.kind=="copper" else f"dielectric {i}")} (type {q(typ)}) (thickness {num(l.thickness)})'+(f' (material {q(l.material)}) (epsilon_r {num(l.epsilon_r or 4.5)}) (loss_tangent 0.02)' if l.kind=='dielectric' else '')+')')
    out.append(f'(setup (stackup {" ".join(stack)}) (pad_to_mask_clearance 0) (aux_axis_origin {num(board.origin[0])} {num(board.origin[1])}))')
    out.append('(net 0 "")')
    for n in netlist: out.append(f'(net {nets[n["id"]]} {q(n["name"])})')
    components={c.id:c for c in doc.components}; parts={(p.id,p.version):p for p in doc.library}
    for place in board.placements:
        c=components[place.component_id]; part=parts[c.library_id,c.library_version]; side='F' if place.side=='top' else 'B'
        # KiCad rotates footprint local coordinates by -angle in screen-space.
        # Using local transformed pad coordinates with zero footprint angle avoids
        # undocumented mirror conventions; logical placement remains a property.
        out.append(f'(footprint {q(part.id+"@"+part.version)} (layer "{side}.Cu") (uuid {q(uid(place.id))}) (at {num(place.at[0])} {num(place.at[1])})')
        if place.locked: out.append('(locked yes)')
        identity={uid(place.id+'/'+p.id):{'pad_id':p.id,'pin_id':p.pin_id} for p in part.pads}
        for name,value in [('Reference',c.reference),('Value',c.value),('OxygenComponentID',c.id),('OxygenPlacementID',place.id),('OxygenLibraryID',part.id),('OxygenLibraryVersion',part.version),('OxygenRotation',str(place.rotation)),('OxygenPadIdentity',json.dumps(identity)),('OxygenLibraryRecord',part.model_dump_json()),('MPN',part.mpn),('Manufacturer',part.manufacturer)]:
            out.append(f'(property {q(name)} {q(value)} (at 0 0 0) (layer "{side}.Fab") (hide yes) (effects (font (size 1 1) (thickness 0.15))))')
        out.append('(attr '+('smd' if all(p.kind=='smd' for p in part.pads) else 'through_hole')+')')
        for pad in part.pads:
            x,y=pad_world(pad,place); local=(x-place.at[0],y-place.at[1]); angle=(place.rotation+(-pad.rotation if place.side=='bottom' else pad.rotation))%360
            kind={'smd':'smd','pth':'thru_hole','npth':'np_thru_hole'}[pad.kind]
            ls=placed_layers(pad,place,layers)
            ls=ls+([side+'.Mask',side+'.Paste'] if pad.kind=='smd' else ['F.Mask','B.Mask'])
            if pad.kind!='smd': ls=['*.Cu','*.Mask']
            drill=''
            if pad.kind!='smd':
                if pad.slot:
                    # KiCad oval drill axes follow pad orientation. Only axis-aligned local slots qualified.
                    dx,dy=pad.slot
                    if abs(dx)>1e-7 and abs(dy)>1e-7: raise ValueError('KiCad export supports axis-aligned local pad slots only')
                    drill=f'(drill oval {num(pad.drill+abs(dx))} {num(pad.drill+abs(dy))})'
                else: drill=f'(drill {num(pad.drill)})'
            net=pins.get(f'{c.id}/{pad.pin_id}'); net_text=f'(net {nets[net]} {q(names[net])})' if net else ''
            out.append(f'(pad {q(pad.number)} {kind} {pad.shape} (at {num(local[0])} {num(local[1])} {num(-angle)}) (size {num(pad.size[0])} {num(pad.size[1])}) {drill} (layers {" ".join(q(l) for l in ls)}) {net_text} (solder_mask_margin {num(pad.mask_margin)}) (solder_paste_margin {num(pad.paste_margin)}) (uuid {q(uid(place.id+"/"+pad.id))}))')
        out.append(')')
    for hole in board.holes:
        drill=f'(drill {num(hole.diameter)})';at=hole.at
        if hole.slot:
            dx,dy=hole.slot
            if abs(dx)>1e-7 and abs(dy)>1e-7: raise ValueError('KiCad export supports axis-aligned hole slots only')
            drill=f'(drill oval {num(hole.diameter+abs(dx))} {num(hole.diameter+abs(dy))})';at=(at[0]+dx/2,at[1]+dy/2)
        ring=hole.annular_ring if hole.plated else 0
        out.append(f'(footprint "Oxygen:BoardHole" (layer "F.Cu") (uuid {q(uid(hole.id))}) (at {num(at[0])} {num(at[1])}) (property "OxygenHoleID" {q(hole.id)} (at 0 0) (layer "F.Fab") (hide yes) (effects (font (size 1 1) (thickness 0.15)))) (pad "" {"thru_hole" if hole.plated else "np_thru_hole"} {"oval" if hole.slot else "circle"} (at 0 0) (size {num(hole.diameter+2*ring+(abs(hole.slot[0]) if hole.slot else 0))} {num(hole.diameter+2*ring+(abs(hole.slot[1]) if hole.slot else 0))}) {drill} (layers "*.Cu" "*.Mask")))')
    for ci,poly in enumerate([board.outline]+board.cutouts):
        for i,(a,b) in enumerate(zip(poly,poly[1:]+poly[:1])):
            out.append(f'(gr_line (start {num(a[0])} {num(a[1])}) (end {num(b[0])} {num(b[1])}) (stroke (width 0.05) (type default)) (layer "Edge.Cuts") (uuid {q(uid(f"contour/{ci}/{i}"))}))')
    for t in board.tracks:
        for i,(a,b) in enumerate(zip(t.points,t.points[1:])):
            out.append(f'(segment (start {num(a[0])} {num(a[1])}) (end {num(b[0])} {num(b[1])}) (width {num(t.width)}) (layer {q(t.layer)}) (net {nets[aliases[t.net_id]]}) (uuid {q(uid(t.id+":"+str(i)))})'+(' (locked yes)' if t.locked else '')+')')
    for v in board.vias:
        typ='blind' if v.technology in ('blind','buried') else 'micro' if v.technology=='microvia' else ''
        out.append(f'(via {typ} (at {num(v.at[0])} {num(v.at[1])}) (size {num(v.diameter)}) (drill {num(v.drill)}) (layers {q(v.start_layer)} {q(v.end_layer)}) (net {nets[aliases[v.net_id]]}) (uuid {q(uid(v.id))}))')
    for z in board.zones:
        net=aliases[z.net_id] if z.net_id else None
        out.append(f'(zone (net {nets[net] if net else 0}) (net_name {q(names[net] if net else "")}) (layer {q(z.layer)}) (uuid {q(uid(z.id))}) (hatch edge 0.5)')
        if z.kind=='keepout':out.append('(keepout (tracks not_allowed) (vias not_allowed) (pads not_allowed) (copperpour not_allowed) (footprints allowed))')
        out.append(f'(connect_pads (clearance {num(z.clearance)})) (min_thickness 0.2) (fill yes (thermal_gap {num(z.thermal_gap)}) (thermal_bridge_width {num(z.thermal_spoke)})) (polygon (pts {" ".join(f"(xy {num(p[0])} {num(p[1])})" for p in z.polygon)})))')
    out.append(')');return '\n'.join(out)+'\n'


def parse_sexpr(text):
    if len(text)>20_000_000:raise ValueError('KiCad input exceeds 20 MB')
    tokens=re.findall(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+',text)
    stack=[]; root=None
    for token in tokens:
        if token=='(':
            if len(stack)>150:raise ValueError('KiCad nesting limit exceeded')
            node=[]
            if stack:stack[-1].append(node)
            elif root is not None:raise ValueError('Multiple KiCad roots')
            else:root=node
            stack.append(node)
        elif token==')':
            if not stack:raise ValueError('Unbalanced KiCad input')
            stack.pop()
        else:
            if not stack:raise ValueError('Invalid KiCad input')
            stack[-1].append(json.loads(token) if token.startswith('"') else token)
    if stack or not root:raise ValueError('Unbalanced/empty KiCad input')
    return root


def children(node,kind):return [n for n in node[1:] if isinstance(n,list) and n and n[0]==kind]
def child(node,kind,default=None):return next(iter(children(node,kind)),default)
def val(node,kind,default=''):return (child(node,kind) or ['',default])[1]
def point(node,kind):return tuple(map(float,child(node,kind)[1:3]))


def import_board(text):
    root=parse_sexpr(text)
    if root[0]!='kicad_pcb':raise ValueError('Expected an editable kicad_pcb document; schematic/project import is not yet qualified')
    doc=Document();loss=[];net_ids={};net_names={};nets=[]
    from .schema import Net,PinRef
    for n in children(root,'net'):
        code=int(n[1]);net_ids[code]='net_'+str(code);net_names[code]=str(n[2])
        if code:nets.append(Net(id=net_ids[code],sheet_id='root',name=str(n[2])))
    net_objects={n.id:n for n in nets}; doc.nets=nets
    copper_layers=[str(l[1]) for l in (child(root,'layers') or [])[1:] if isinstance(l,list) and len(l)>2 and l[2]=='signal']
    if not copper_layers:raise ValueError('No copper layers found')
    stack=child(child(root,'setup') or [],'stackup');stackup=[]
    if stack:
        for i,l in enumerate(children(stack,'layer')):
            name=str(l[1]);kind='copper' if name in copper_layers else 'dielectric'
            if kind=='dielectric' and val(l,'type','') in ('Top Solder Mask','Bottom Solder Mask','Top Silk Screen','Bottom Silk Screen','Top Solder Paste','Bottom Solder Paste'):continue
            thick=float(val(l,'thickness',0))
            if thick>0:stackup.append(Layer(id=name if kind=='copper' else f'dielectric_{i}',kind=kind,thickness=thick,material=str(val(l,'material','')),epsilon_r=float(val(l,'epsilon_r',4.5)) if kind=='dielectric' else None))
    if not stackup:
        thickness=float(val(child(root,'general') or [],'thickness',1.6)); dielectric=(thickness-0.035*len(copper_layers))/(len(copper_layers)-1)
        for i,l in enumerate(copper_layers):
            stackup.append(Layer(id=l,kind='copper',thickness=0.035))
            if i<len(copper_layers)-1:stackup.append(Layer(id=f'dielectric_{i}',kind='dielectric',thickness=dielectric,material='unknown'))
        loss.append({'kind':'stackup','severity':'warning','message':'Source lacks physical stackup; assumed 35um copper and equal dielectric spacing, review required'})
    doc.board.stackup=stackup
    for i,fp in enumerate(children(root,'footprint')+children(root,'module')):
        props={str(p[1]):str(p[2]) for p in children(fp,'property') if len(p)>2}
        for p in children(fp,'fp_text'):
            if p[1]=='reference':props.setdefault('Reference',str(p[2]))
            if p[1]=='value':props.setdefault('Value',str(p[2]))
        at=child(fp,'at') or ['',0,0];px,py=map(float,at[1:3]);angle=float(at[3]) if len(at)>3 else 0;side='bottom' if str(val(fp,'layer','F.Cu')).startswith('B.') else 'top'
        pads=children(fp,'pad');hid=props.get('OxygenHoleID')
        if hid and len(pads)==1:
            p=pads[0];dr=child(p,'drill');diam=min(float(dr[2]),float(dr[3])) if dr and dr[1]=='oval' else float(dr[1]);slot=None
            if dr and dr[1]=='oval':slot=(float(dr[2])-diam,float(dr[3])-diam)
            size=point(p,'size');ring=min((size[k]-diam-(slot[k] if slot else 0))/2 for k in range(2))
            doc.board.holes.append(Hole(id=hid,at=(px-(slot[0]/2 if slot else 0),py-(slot[1]/2 if slot else 0)),diameter=diam,plated=p[2]=='thru_hole',annular_ring=round(ring,9) if p[2]=='thru_hole' else 0.15,slot=slot));continue
        cid=props.get('OxygenComponentID',f'component_{i}');lid=props.get('OxygenLibraryID',f'footprint_{i}');version=props.get('OxygenLibraryVersion','imported');pid=props.get('OxygenPlacementID',f'placement_{i}')
        embedded=LibraryPart.model_validate_json(props['OxygenLibraryRecord']) if 'OxygenLibraryRecord' in props else None
        identity=json.loads(props.get('OxygenPadIdentity','{}'))
        placement_rotation=float(props.get('OxygenRotation',-angle))
        pin_records={p.id:p for p in embedded.pins} if embedded else {};pad_records=[]
        for j,p in enumerate(pads):
            number=str(p[1]) or f'NPTH{j}';ident=identity.get(str(val(p,'uuid')),{});pinid=ident.get('pin_id','pin_'+re.sub(r'[^A-Za-z0-9_.:-]','_',number))
            if pinid not in pin_records:pin_records[pinid]=Pin(id=pinid,number=number)
            pa=child(p,'at') or ['',0,0];x,y=map(float,pa[1:3]);pr=float(pa[3]) if len(pa)>3 else 0
            # KiCad local coordinates rotate clockwise numerically; canonical model uses CCW.
            world=rotate((x,y),-angle);unrotated=rotate(world,-placement_rotation);local=(unrotated[0]*(-1 if side=='bottom' else 1),unrotated[1])
            shape=str(p[3]);kind={'smd':'smd','thru_hole':'pth','np_thru_hole':'npth'}.get(str(p[2]))
            if kind is None:loss.append({'kind':'pad','severity':'error','object':f'{cid}/{number}','message':f'Unsupported pad technology {p[2]}'});continue
            if shape not in ('rect','circle','oval'):
                loss.append({'kind':'pad_shape','severity':'error','object':f'{cid}/{number}','message':f'Unsupported {shape} pad approximated as bounding rectangle'});shape='rect'
            dr=child(p,'drill');diam=0;slot=None
            if dr:
                if dr[1]=='oval':
                    dx,dy=float(dr[2]),float(dr[3]);diam=min(dx,dy);slot=(dx-diam,dy-diam)
                else:diam=float(dr[1])
            ls=[str(x) for x in (child(p,'layers') or [])[1:] if str(x).endswith('.Cu')]
            # Local F.Cu becomes physical B.Cu on a bottom placement.
            if side=='bottom':ls=['F.Cu' if l=='B.Cu' else 'B.Cu' if l=='F.Cu' else l for l in ls]
            size=point(p,'size');pad_records.append(Pad(id=ident.get('pad_id',f'pad_{j}'),pin_id=pinid,number=number,at=local,size=size,shape=shape,kind=kind,rotation=(-pr-angle-placement_rotation)*(-1 if side=='bottom' else 1),drill=diam,slot=slot,layers=ls or ['F.Cu'],mask_margin=float(val(p,'solder_mask_margin',0)),paste_margin=float(val(p,'solder_paste_margin',0))))
            n=child(p,'net')
            if n and int(n[1]) and int(n[1]) in net_ids:
                ref=PinRef(component_id=cid,pin_id=pinid)
                if ref not in net_objects[net_ids[int(n[1])]].members:net_objects[net_ids[int(n[1])]].members.append(ref)
        part=LibraryPart.model_validate({**(embedded.model_dump() if embedded else {}),'id':lid,'version':version,'name':embedded.name if embedded else str(fp[1]),'manufacturer':props.get('Manufacturer',''),'mpn':props.get('MPN',''),'source':embedded.source if embedded else 'User supplied KiCad board; source asset hash recorded in provenance','licence':embedded.licence if embedded else 'Unspecified: user must supply source asset licence','pins':list(pin_records.values()),'pads':pad_records})
        existing=next((p for p in doc.library if (p.id,p.version)==(lid,version)),None)
        if existing and existing.model_dump()!=part.model_dump():
            lid=lid+f'_{i}';part.id=lid;loss.append({'kind':'library_identity','severity':'warning','object':cid,'message':'Same library/version has differing embedded geometry; retained as distinct imported part'})
        if not any((p.id,p.version)==(lid,version) for p in doc.library):doc.library.append(part)
        reference=props.get('Reference',f'U{i+1}')
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*',reference):reference=f'U{i+1}';loss.append({'kind':'reference','severity':'warning','message':'Invalid/missing refdes replaced; inspect source identity'})
        doc.components.append(Component(id=cid,sheet_id='root',library_id=lid,library_version=version,reference=reference,value=props.get('Value','')))
        doc.board.placements.append(Placement(id=pid,component_id=cid,at=(px,py),side=side,rotation=placement_rotation,locked=child(fp,'locked') is not None or 'locked' in fp))
        if children(fp,'model'):loss.append({'kind':'3d_model','severity':'warning','object':cid,'message':'External model reference not resolved; model remains explicitly missing'})
        if children(fp,'fp_poly') or children(fp,'fp_arc') or children(fp,'fp_line'):loss.append({'kind':'footprint_graphics','severity':'warning','object':cid,'message':'Silk/courtyard/fab graphics not imported'})
    contours=[];segments=[]
    for item in children(root,'gr_line'):
        if val(item,'layer')=='Edge.Cuts':segments.append((point(item,'start'),point(item,'end')))
    while segments:
        a,b=segments.pop(0);poly=[a,b]
        while math.dist(poly[-1],poly[0])>1e-6:
            match=next(((i,v if math.dist(poly[-1],u)<1e-6 else u) for i,(u,v) in enumerate(segments) if min(math.dist(poly[-1],u),math.dist(poly[-1],v))<1e-6),None)
            if match is None:raise ValueError('Edge.Cuts contains an open contour; repair before import')
            i,p=match;segments.pop(i);poly.append(p)
        contours.append(poly[:-1])
    for rect in children(root,'gr_rect'):
        if val(rect,'layer')=='Edge.Cuts':
            a,b=point(rect,'start'),point(rect,'end');contours.append([a,(b[0],a[1]),b,(a[0],b[1])])
    if contours:
        contours.sort(key=lambda p:abs(sum(a[0]*b[1]-a[1]*b[0] for a,b in zip(p,p[1:]+p[:1]))),reverse=True);doc.board.outline=contours[0];doc.board.cutouts=contours[1:]
    else:raise ValueError('No supported closed Edge.Cuts contour; curved outlines require a qualified adapter')
    for i,t in enumerate(children(root,'segment')):
        code=int(val(t,'net',0))
        if code not in net_ids or not code:loss.append({'kind':'unassigned_track','severity':'error','message':'Track without real net not imported'});continue
        doc.board.tracks.append(Track(id='track_'+str(i),net_id=net_ids[code],layer=str(val(t,'layer')),points=[point(t,'start'),point(t,'end')],width=float(val(t,'width')),locked=child(t,'locked') is not None))
    for i,v in enumerate(children(root,'via')):
        code=int(val(v,'net',0));ls=child(v,'layers')[1:3]
        if not code or code not in net_ids:loss.append({'kind':'unassigned_via','severity':'error','message':'Via without net not imported'});continue
        technology='microvia' if 'micro' in v else ('through' if ls==['F.Cu','B.Cu'] else 'blind' if 'F.Cu' in ls or 'B.Cu' in ls else 'buried')
        doc.board.vias.append(Via(id='via_'+str(i),net_id=net_ids[code],at=point(v,'at'),diameter=float(val(v,'size')),drill=float(val(v,'drill')),start_layer=ls[0],end_layer=ls[1],technology=technology))
    for i,z in enumerate(children(root,'zone')):
        polygon=child(z,'polygon');pts=child(polygon or [],'pts')
        if not pts:loss.append({'kind':'zone','severity':'error','message':'Zone lacks supported polygon'});continue
        code=int(val(z,'net',0));ls=child(z,'layers')
        layer=str(val(z,'layer',ls[1] if ls else 'F.Cu'))
        doc.board.zones.append(Zone(id='zone_'+str(i),net_id=net_ids.get(code) if code else None,layer=layer,polygon=[tuple(map(float,p[1:3])) for p in pts[1:]],kind='keepout' if child(z,'keepout') else 'copper'))
        if child(z,'filled_polygon'):loss.append({'kind':'zone_fill','severity':'warning','message':'Existing fill omitted; independent refill required'})
    for kind in ('arc','gr_arc','gr_circle','group','rule_area','dimension','target'):
        if children(root,kind):loss.append({'kind':kind,'severity':'error' if kind in ('arc','gr_arc','gr_circle') else 'warning','count':len(children(root,kind)),'message':'Unsupported construct retained in immutable original source, not editable in translated model'})
    loss.append({'kind':'schematic','severity':'warning','message':'Board import reconstructs pin/net membership; original schematic graphics, hierarchy and electrical pin types are unavailable'})
    loss.append({'kind':'design_rules','severity':'warning','message':'Project/custom rules and manufacturer constraints are not imported; review translated process defaults'})
    loss.append({'kind':'identity','severity':'warning','message':'External net, track and dielectric-layer IDs are translated; use Oxygen JSON source closure for a lossless identity round trip'})
    doc=Document.model_validate(doc.model_dump())
    return doc,{'source_format':'kicad_pcb','source_version':val(root,'version'),'findings':loss,'requires_review':bool(loss),'counts':{'components':len(doc.components),'pins':sum(len(p.pins) for p in doc.library),'nets':len(doc.nets),'tracks':len(doc.board.tracks),'vias':len(doc.board.vias),'cutouts':len(doc.board.cutouts)}}
