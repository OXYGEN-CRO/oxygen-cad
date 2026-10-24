"""Electrical graph, conservative planar copper checks and MCAD identity transforms.

The local checker covers its enumerated rule set. KiCad DRC is available as an
independent worker; neither is a manufacturer qualification or SI/EM analysis.
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from .schema import Document, digest

EPS = 1e-7


class Union:
    def __init__(self, keys): self.p = {k: k for k in keys}
    def find(self, x):
        if self.p[x] != x: self.p[x] = self.find(self.p[x])
        return self.p[x]
    def join(self, a, b): self.p[self.find(a)] = self.find(b)


def flattened(doc):
    u = Union([n.id for n in doc.nets]); global_names = {}
    for net in doc.nets:
        if net.global_name:
            if net.name in global_names: u.join(net.id, global_names[net.name])
            global_names[net.name] = net.id
    for sheet in doc.sheets:
        for port in sheet.ports: u.join(port.net_id, port.parent_net_id)
    groups = {}
    for n in doc.nets:
        groups.setdefault(u.find(n.id), []).append(n)
    out = []
    for nets in groups.values():
        key = min(n.id for n in nets)
        out.append({'id': key, 'name': '/'.join(sorted({n.name for n in nets})), 'source_net_ids': sorted(n.id for n in nets),
                    'members': [m.model_dump() for n in nets for m in n.members]})
    return sorted(out, key=lambda n: n['id'])


def net_map(doc):
    aliases, pins = {}, {}
    for net in flattened(doc):
        for source in net['source_net_ids']: aliases[source] = net['id']
        for m in net['members']: pins[f"{m['component_id']}/{m['pin_id']}"] = net['id']
    return aliases, pins


def finding(code, objects, message, severity='error', **extra):
    objects = sorted(set(objects))
    import hashlib
    key = hashlib.sha256((code + '|' + '|'.join(objects)).encode()).hexdigest()[:16]
    return {'id': f'{code}:{key}', 'code': code, 'severity': severity, 'objects': objects, 'message': message, **extra}


def report(doc, findings, kind):
    key = digest(doc)
    waivers = {w.finding_id: w for w in doc.waivers if w.source_hash == key}
    for f in findings:
        f['waived'] = f['id'] in waivers
        if f['waived']: f['waiver'] = waivers[f['id']].model_dump()
    return {'kind': kind, 'source_hash': key, 'state': 'failed' if any(f['severity'] == 'error' and not f['waived'] for f in findings) else 'passed',
            'findings': findings, 'limitations': ['Local enumerated rules only; independent KiCad checking and supplier DFM remain separate.']}


def erc(doc):
    aliases, pins = net_map(doc); parts = {(p.id, p.version): p for p in doc.library}
    nc = {r.key() for r in doc.no_connect}; findings = []; pin_types = {}
    for c in doc.components:
        part = parts[c.library_id, c.library_version]
        for pin in part.pins:
            key = f'{c.id}/{pin.id}'; pin_types[key] = pin.electrical
            if pin.electrical == 'no_connect' and key in pins:
                findings.append(finding('erc.forbidden_connection', [key], 'A no-connect library pin is wired'))
            elif pin.electrical not in ('passive', 'no_connect') and key not in pins and key not in nc:
                findings.append(finding('erc.unconnected', [key], f'{c.reference} pin {pin.number} has no net or explicit no-connect'))
    for net in flattened(doc):
        members = [f"{p['component_id']}/{p['pin_id']}" for p in net['members']]
        outputs = [p for p in members if pin_types[p] in ('output', 'power_out')]
        if len(outputs) > 1:
            findings.append(finding('erc.output_conflict', outputs, f'Conflicting driven outputs on {net["name"]}'))
        power = [p for p in members if pin_types[p] == 'power_in']
        if power and not any(pin_types[p] == 'power_out' for p in members):
            findings.append(finding('erc.unpowered', power, f'Power input net {net["name"]} has no power source'))
        if len(members) == 1:
            findings.append(finding('erc.single_pin_net', members, f'{net["name"]} connects only one pin', 'warning'))
    for wire in doc.wires:
        if wire.start is None or wire.end is None:
            findings.append(finding('erc.graphical_endpoint', [wire.id], 'Wire has a graphical endpoint without an explicit pin terminal; visual contact is not connectivity', 'warning'))
    placed = {p.component_id for p in doc.board.placements}
    for c in doc.components:
        if c.id not in placed: findings.append(finding('consistency.missing_footprint', [c.id], 'Schematic component has no board placement', 'warning'))
    for mate in doc.mates:
        for a, b in mate.pin_map.items():
            na, nb = pins.get(f'{mate.a}/{a}'), pins.get(f'{mate.b}/{b}')
            if na and nb and na != nb:
                findings.append(finding('connector.net_mismatch', [mate.id, f'{mate.a}/{a}', f'{mate.b}/{b}'], 'Mating connector pins have different electrical nets'))
    return report(doc, findings, 'erc')


def rotate(point, angle):
    a = math.radians(angle); x, y = point
    return (x*math.cos(a)-y*math.sin(a), x*math.sin(a)+y*math.cos(a))


def pad_world(pad, placement):
    p = (pad.at[0] * (-1 if placement.side == 'bottom' else 1), pad.at[1])
    x, y = rotate(p, placement.rotation)
    return (placement.at[0]+x, placement.at[1]+y)


def placed_layers(pad, placement, layers):
    if pad.kind == 'npth': return []
    if '*.Cu' in pad.layers or pad.kind == 'pth': return layers
    return [('B.Cu' if l == 'F.Cu' else 'F.Cu' if l == 'B.Cu' else l) if placement.side == 'bottom' else l for l in pad.layers]


def pad_polygon(pad, placement, margin=0):
    sx, sy = pad.size[0]+margin*2, pad.size[1]+margin*2
    if min(sx, sy) <= 0: return []
    if pad.shape == 'circle':
        points = [(sx/2*math.cos(i*math.pi/16), sy/2*math.sin(i*math.pi/16)) for i in range(32)]
    elif pad.shape == 'oval':
        # Capsule, not an ellipse: straight sides and semicircular ends.
        radius = min(sx, sy)/2; half = (max(sx, sy)-min(sx, sy))/2
        points = []
        for side in (-1, 1):
            for i in range(17):
                a = (-math.pi/2+i*math.pi/16) if side == 1 else (math.pi/2+i*math.pi/16)
                p = (side*half + radius*math.cos(a), radius*math.sin(a))
                points.append(p if sx >= sy else (p[1], p[0]))
    else: points = [(-sx/2,-sy/2),(sx/2,-sy/2),(sx/2,sy/2),(-sx/2,sy/2)]
    center = pad_world(pad, placement); mirror = -1 if placement.side == 'bottom' else 1
    result = []
    for p in points:
        p = rotate(p, pad.rotation); p = (p[0]*mirror, p[1]); p = rotate(p, placement.rotation)
        result.append((center[0]+p[0], center[1]+p[1]))
    return result


def edges(poly): return zip(poly, poly[1:]+poly[:1])

def point_segment(p, a, b):
    dx, dy = b[0]-a[0], b[1]-a[1]
    t = max(0, min(1, ((p[0]-a[0])*dx+(p[1]-a[1])*dy)/(dx*dx+dy*dy))) if dx or dy else 0
    return math.hypot(p[0]-a[0]-t*dx, p[1]-a[1]-t*dy)


def cross(a,b,c): return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])

def intersects(a,b,c,d):
    c1,c2,c3,c4 = cross(a,b,c),cross(a,b,d),cross(c,d,a),cross(c,d,b)
    if c1*c2 < -EPS and c3*c4 < -EPS: return True
    return any(abs(v) <= EPS and point_segment(p,x,y) <= EPS for v,p,x,y in [(c1,c,a,b),(c2,d,a,b),(c3,a,c,d),(c4,b,c,d)])


def segment_distance(a,b,c,d):
    if intersects(a,b,c,d): return 0
    return min(point_segment(a,c,d),point_segment(b,c,d),point_segment(c,a,b),point_segment(d,a,b))


def inside(p, poly):
    if any(point_segment(p,a,b) < EPS for a,b in edges(poly)): return True
    x,y=p; odd=False
    for a,b in edges(poly):
        if (a[1]>y) != (b[1]>y) and x < (b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]: odd=not odd
    return odd


def poly_distance(a,b):
    if any(inside(p,b) for p in a) or any(inside(p,a) for p in b): return 0
    return min(segment_distance(x,y,u,v) for x,y in edges(a) for u,v in edges(b))


def capsule(a,b,width):
    angle = math.atan2(b[1]-a[1],b[0]-a[0]); r=width/2
    return [(p[0]+r*math.cos(t),p[1]+r*math.sin(t)) for p,start in [(b,angle-math.pi/2),(a,angle+math.pi/2)] for t in [start+i*math.pi/16 for i in range(17)]]


@dataclass
class Copper:
    id: str
    net: str | None
    layers: list[str]
    polygon: list
    terminal: str | None = None


def copper(doc):
    layers = [l.id for l in doc.board.stackup if l.kind == 'copper']; parts = {(p.id,p.version):p for p in doc.library}
    components = {c.id:c for c in doc.components}; aliases,pins = net_map(doc); result=[]
    for place in doc.board.placements:
        c = components[place.component_id]
        for pad in parts[c.library_id,c.library_version].pads:
            ls = placed_layers(pad,place,layers)
            if ls: result.append(Copper(f'{place.id}/{pad.id}',pins.get(f'{c.id}/{pad.pin_id}'),ls,pad_polygon(pad,place),f'{c.id}/{pad.pin_id}'))
    for track in doc.board.tracks:
        for i,(a,b) in enumerate(zip(track.points,track.points[1:])):
            result.append(Copper(f'{track.id}:{i}',aliases[track.net_id],[track.layer],capsule(a,b,track.width)))
    for via in doc.board.vias:
        poly=[(via.at[0]+via.diameter/2*math.cos(i*math.pi/16),via.at[1]+via.diameter/2*math.sin(i*math.pi/16)) for i in range(32)]
        result.append(Copper(via.id,aliases[via.net_id],layers[layers.index(via.start_layer):layers.index(via.end_layer)+1],poly))
    for hole in doc.board.holes:
        if hole.plated:
            end=(hole.at[0]+(hole.slot[0] if hole.slot else 0),hole.at[1]+(hole.slot[1] if hole.slot else 0))
            result.append(Copper(hole.id,None,layers,capsule(hole.at,end,hole.diameter+2*hole.annular_ring)))
    return result


def effective(doc, net, layer):
    values={'width':doc.process.min_width,'clearance':doc.process.clearance,'drill':doc.process.min_drill,'annular_ring':doc.process.annular_ring,'edge_clearance':doc.process.edge_clearance}
    aliases,_=net_map(doc)
    for rule in sorted(doc.rules,key=lambda r:(r.priority,r.id)):
        if rule.net_ids and net not in {aliases.get(n,n) for n in rule.net_ids}: continue
        if rule.layers and layer not in rule.layers: continue
        for k in values:
            if getattr(rule,k) is not None: values[k]=getattr(rule,k)
    return values


def drc(doc):
    findings=[]; items=copper(doc); aliases,pins=net_map(doc); board=doc.board
    for name,poly in [('outline',board.outline)]+[(f'cutout:{i}',p) for i,p in enumerate(board.cutouts)]:
        if len(poly)<3 or abs(sum(a[0]*b[1]-a[1]*b[0] for a,b in edges(poly))) < EPS:
            findings.append(finding('drc.invalid_contour',[name],'Contour must enclose nonzero area')); continue
        es=list(edges(poly))
        for i,(a,b) in enumerate(es):
            for j,(c,d) in enumerate(es):
                if j>i+1 and (i,j)!=(0,len(es)-1) and intersects(a,b,c,d): findings.append(finding('drc.self_intersection',[name],'Board contour intersects itself'))
    for i,cutout in enumerate(board.cutouts):
        if not all(inside(p,board.outline) for p in cutout): findings.append(finding('drc.cutout_outside',[f'cutout:{i}'],'Interior cutout extends outside the board'))
    for track in board.tracks:
        rule=effective(doc,aliases[track.net_id],track.layer)
        if track.width < rule['width']-EPS: findings.append(finding('drc.width',[track.id],'Trace is narrower than the applicable scoped rule',value=track.width,limit=rule['width']))
    for i,a in enumerate(items):
        rule_values=[effective(doc,a.net,l) for l in a.layers]
        edge=max(r['edge_clearance'] for r in rule_values)
        outside=any(not inside(p,board.outline) or any(inside(p,c) for c in board.cutouts) for p in a.polygon)
        gap=min(segment_distance(x,y,u,v) for x,y in edges(a.polygon) for contour in [board.outline]+board.cutouts for u,v in edges(contour))
        if outside or gap < edge-EPS: findings.append(finding('drc.board_edge',[a.id],'Copper crosses a board boundary or violates edge clearance',value=0 if outside else gap,limit=edge))
        for zone in board.zones:
            if zone.kind=='keepout' and zone.layer in a.layers and poly_distance(a.polygon,zone.polygon)<zone.clearance-EPS:
                findings.append(finding('drc.keepout',[a.id,zone.id],'Copper enters a restricted area'))
        for b in items[i+1:]:
            common=set(a.layers)&set(b.layers)
            if not common or (a.net is not None and a.net==b.net): continue
            limit=max(max(effective(doc,x.net,l)['clearance'] for x in (a,b)) for l in common)
            distance=poly_distance(a.polygon,b.polygon)
            if distance < limit-EPS or distance<=EPS:
                findings.append(finding('drc.short' if distance<=EPS else 'drc.clearance',[a.id,b.id],'Different-net copper violates clearance',value=distance,limit=limit))
    for via in board.vias:
        rule=effective(doc,aliases[via.net_id],via.start_layer)
        if via.drill < rule['drill']-EPS: findings.append(finding('drc.drill',[via.id],'Via drill is too small',value=via.drill,limit=rule['drill']))
        ring=(via.diameter-via.drill)/2
        if ring < rule['annular_ring']-EPS: findings.append(finding('drc.annular_ring',[via.id],'Via annular ring is too small',value=ring,limit=rule['annular_ring']))
    parts={(p.id,p.version):p for p in doc.library}; components={c.id:c for c in doc.components}; openings=[]
    for place in board.placements:
        c=components[place.component_id]
        for pad in parts[c.library_id,c.library_version].pads:
            pid=f'{place.id}/{pad.id}'
            if pad.kind!='smd':
                if pad.drill<doc.process.min_drill-EPS: findings.append(finding('drc.drill',[pid],'Pad drill is too small'))
                ring=min((pad.size[i]-pad.drill-(abs(pad.slot[i]) if pad.slot else 0))/2 for i in range(2))
                if pad.kind=='pth' and ring<doc.process.annular_ring-EPS: findings.append(finding('drc.annular_ring',[pid],'Pad/slot annular ring is too small'))
            if pad.kind!='npth': openings.append((pid,place.side,pad_polygon(pad,place,pad.mask_margin)))
    for i,(aid,side,a) in enumerate(openings):
        for bid,bs,b in openings[i+1:]:
            gap=poly_distance(a,b)
            if side==bs and 0<gap<doc.process.mask_web-EPS: findings.append(finding('dfm.mask_web',[aid,bid],'Solder-mask web is narrower than process constraint',value=gap,limit=doc.process.mask_web))
    for hole in board.holes:
        if hole.diameter<doc.process.min_drill-EPS: findings.append(finding('drc.drill',[hole.id],'Board drill is too small'))
        if hole.plated and hole.annular_ring<doc.process.annular_ring-EPS:findings.append(finding('drc.annular_ring',[hole.id],'Plated board-hole annular ring is too small'))
        if not hole.plated:
            end=(hole.at[0]+(hole.slot[0] if hole.slot else 0),hole.at[1]+(hole.slot[1] if hole.slot else 0));poly=capsule(hole.at,end,hole.diameter)
            for item in items:
                gap=poly_distance(poly,item.polygon)
                if gap<doc.process.clearance-EPS:findings.append(finding('drc.hole_clearance',[hole.id,item.id],'Copper violates non-plated hole/slot clearance',value=gap,limit=doc.process.clearance))
        if not inside(hole.at,board.outline) or any(inside(hole.at,c) for c in board.cutouts): findings.append(finding('drc.hole_outside',[hole.id],'Hole centre lies outside board substrate'))
    # Union physically touching same-net copper. Layer transitions only happen through vias/PTH pads.
    u=Union([str(i) for i in range(len(items))])
    for i,a in enumerate(items):
        for j in range(i+1,len(items)):
            b=items[j]
            if a.net is not None and a.net==b.net and set(a.layers)&set(b.layers) and poly_distance(a.polygon,b.polygon)<=EPS: u.join(str(i),str(j))
    for net in flattened(doc):
        terminal_ids={f"{m['component_id']}/{m['pin_id']}" for m in net['members']}
        terminal_groups={u.find(str(i)) for i,a in enumerate(items) if a.net==net['id'] and a.terminal in terminal_ids}
        actual={a.terminal for a in items if a.net==net['id'] and a.terminal}
        missing=terminal_ids-actual
        if len(terminal_groups)>1 or missing:
            findings.append(finding('drc.unrouted',[net['id']]+sorted(terminal_ids),'Net has disconnected physical terminals',groups=len(terminal_groups),missing=sorted(missing)))
    lengths={n.id:sum(math.dist(a,b) for t in board.tracks if aliases[t.net_id]==aliases[n.id] for a,b in zip(t.points,t.points[1:])) for n in doc.nets}
    for pair in doc.differential_pairs:
        a,b=lengths[pair.positive],lengths[pair.negative]
        if abs(a-b)>pair.max_skew+EPS: findings.append(finding('drc.pair_skew',[pair.id],'Differential pair exceeds maximum planar route-length skew',value=abs(a-b),limit=pair.max_skew))
        for t in board.tracks:
            if aliases[t.net_id] in (aliases[pair.positive],aliases[pair.negative]) and abs(t.width-pair.width)>EPS: findings.append(finding('drc.pair_width',[pair.id,t.id],'Differential pair width differs from its rule'))
        if pair.max_length and max(a,b)>pair.max_length+EPS: findings.append(finding('drc.pair_length',[pair.id],'Differential pair exceeds maximum planar trace length'))
        positive=[t for t in board.tracks if aliases[t.net_id]==aliases[pair.positive]]; negative=[t for t in board.tracks if aliases[t.net_id]==aliases[pair.negative]]
        for ta in positive:
            for tb in negative:
                if ta.layer!=tb.layer: continue
                gap=min(segment_distance(x,y,u,v) for x,y in zip(ta.points,ta.points[1:]) for u,v in zip(tb.points,tb.points[1:]))-(ta.width+tb.width)/2
                if gap<pair.gap-EPS: findings.append(finding('drc.pair_gap',[pair.id,ta.id,tb.id],'Differential pair gap is too small',value=gap,limit=pair.gap))
    if any(z.kind=='copper' for z in board.zones): findings.append(finding('drc.unfilled_zones',[z.id for z in board.zones if z.kind=='copper'],'Copper zones require independent KiCad refill and connectivity checking'))
    result=report(doc,findings,'drc'); result.update(trace_lengths_mm=lengths,stack_thickness_mm=sum(l.thickness for l in board.stackup),checked_copper_items=len(items))
    result['limitations'] += ['Curved geometry is conservatively polygonized; no silk, impedance, SI or thermal certification.', 'Pair lengths are planar track sums; via barrel lengths are not included.']
    return result


def transform3(p, transform):
    x,y,z=p; tx,ty,tz,rx,ry,rz=transform
    a,b,c=map(math.radians,(rx,ry,rz))
    y,z=y*math.cos(a)-z*math.sin(a),y*math.sin(a)+z*math.cos(a)
    x,z=x*math.cos(b)+z*math.sin(b),-x*math.sin(b)+z*math.cos(b)
    x,y=x*math.cos(c)-y*math.sin(c),x*math.sin(c)+y*math.cos(c)
    return [x+tx,y+ty,z+tz]


def mcad(doc):
    parts={(p.id,p.version):p for p in doc.library}; components={c.id:c for c in doc.components}; thickness=sum(l.thickness for l in doc.board.stackup); occurrences=[]
    for place in doc.board.placements:
        c=components[place.component_id]; part=parts[c.library_id,c.library_version]
        z=thickness if place.side=='top' else 0
        pads=[]
        for pad in part.pads:
            x,y=pad_world(pad,place)
            pads.append({'pad_id':pad.id,'pin_id':pad.pin_id,'number':pad.number,'board_mm':[x,y,z],'world_mm':transform3((x,y,z),doc.board.transform)})
        model=part.model3d
        bounds=None
        if model:
            corners=[]
            for x in (-model.size[0]/2,model.size[0]/2):
                for y in (-model.size[1]/2,model.size[1]/2):
                    for h in (0,model.size[2]):
                        local=transform3((x,y,h),(*model.offset,*model.rotation))
                        lx,ly=rotate((local[0]*(-1 if place.side=='bottom' else 1),local[1]),place.rotation)
                        corners.append(transform3((place.at[0]+lx,place.at[1]+ly,z+local[2]*(1 if place.side=='top' else -1)),doc.board.transform))
            bounds={'min':[min(p[i] for p in corners) for i in range(3)],'max':[max(p[i] for p in corners) for i in range(3)]}
        occurrences.append({'id':place.id,'component_id':c.id,'reference':c.reference,'side':place.side,'rotation_deg':place.rotation,'pads':pads,'model_state':'declared_unresolved' if model else 'missing','asset':model.asset if model else None,'bounds_mm':bounds})
    return {'source_hash':digest(doc),'board_id':doc.board.id,'mechanical_occurrence_id':doc.board.mechanical_occurrence_id,'origin_convention':'XY mm; top +Z; bottom reflects local X before CCW rotation; board Euler XYZ degrees','thickness_mm':thickness,'outline':doc.board.outline,'cutouts':doc.board.cutouts,'holes':[h.model_dump() for h in doc.board.holes],'transform':doc.board.transform,'occurrences':occurrences,'state':'incomplete' if occurrences else 'empty','limitations':['Identity/transform bridge only; canonical CAD geometry is owned by the native document.','Model bounds are user declarations; external model asset closure is not resolved by this report.']}


def harness_report(doc):
    entries=[]; findings=[]
    for wire in doc.harness:
        length=sum(math.dist(a,b) for a,b in zip(wire.points,wire.points[1:])); radii=[]
        for a,b,c in zip(wire.points,wire.points[1:],wire.points[2:]):
            x=math.dist(a,b);y=math.dist(b,c);z=math.dist(a,c)
            area2=math.sqrt(max(0,4*x*x*y*y-(x*x+y*y-z*z)**2))/2
            radius=x*y*z/(2*area2) if area2>EPS else math.inf
            radii.append(radius)
            if radius<wire.min_bend_radius-EPS: findings.append(finding('harness.bend_radius',[wire.id],'Route bend violates minimum circumcircle radius',value=radius,limit=wire.min_bend_radius))
        entries.append({'id':wire.id,'start':wire.start.model_dump(),'end':wire.end.model_dump(),'route_length_mm':length,'cut_length_mm':length+wire.slack,'gauge_awg':wire.gauge_awg,'insulation':wire.insulation,'bundle':wire.bundle})
    r=report(doc,findings,'harness'); r['cut_list']=entries;r['limitations']=['Polyline length and bend-radius checks only; no obstacle-aware harness routing or terminal/crimp qualification.'];return r
