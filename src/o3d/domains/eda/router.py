"""Deterministic bounded single-layer walkaround router (no shove claims)."""
import heapq
import math
from .checks import copper, net_map, pad_world, placed_layers, effective, capsule, poly_distance, inside, edges, segment_distance
from .schema import Track


def route(doc, payload, cancel=None):
    if cancel and (cancel() if callable(cancel) else cancel.is_set()):raise RuntimeError('Electronics routing cancelled before starting')
    net_id=payload['net_id']; layer=payload.get('layer','F.Cu'); aliases,pins=net_map(doc)
    if net_id not in aliases: raise ValueError('Unknown route net')
    layers=[l.id for l in doc.board.stackup if l.kind=='copper']
    if layer not in layers: raise ValueError('Route layer is not copper')
    net=aliases[net_id]; width=float(payload.get('width',effective(doc,net,layer)['width'])); grid=float(payload.get('grid',0.5))
    if width<effective(doc,net,layer)['width'] or not 0.1<=grid<=5: raise ValueError('Route width violates rule or grid is outside 0.1–5 mm')
    parts={(p.id,p.version):p for p in doc.library}; comps={c.id:c for c in doc.components}; placed={p.component_id:p for p in doc.board.placements}
    def endpoint(ref):
        cid,pid=ref['component_id'],ref['pin_id']
        if pins.get(f'{cid}/{pid}')!=net or cid not in placed: raise ValueError('Route endpoint must be a placed pin on the selected net')
        c=comps[cid]; pad=next((p for p in parts[c.library_id,c.library_version].pads if p.pin_id==pid and layer in placed_layers(p,placed[cid],layers)),None)
        if pad is None: raise ValueError('Endpoint pin has no pad on the selected copper layer; add an explicit layer transition')
        return pad_world(pad,placed[cid])
    start,end=endpoint(payload['start']),endpoint(payload['end'])
    obstacles=[]
    for item in copper(doc):
        if layer in item.layers and item.net!=net:
            gap=max(effective(doc,net,layer)['clearance'],effective(doc,item.net,layer)['clearance'])
            obstacles.append((item.polygon,gap))
    for zone in doc.board.zones:
        if zone.kind=='keepout' and zone.layer==layer: obstacles.append((zone.polygon,zone.clearance))
    for hole in doc.board.holes:
        obstacles.append(([(hole.at[0]+hole.diameter/2*math.cos(i*math.pi/16),hole.at[1]+hole.diameter/2*math.sin(i*math.pi/16)) for i in range(32)],effective(doc,net,layer)['clearance']))
    edge=effective(doc,net,layer)['edge_clearance']; bounds=doc.board.outline
    def allowed(a,b):
        poly=capsule(a,b,width)
        if any(not inside(p,bounds) or any(inside(p,c) for c in doc.board.cutouts) for p in poly): return False
        if min(segment_distance(x,y,u,v) for x,y in edges(poly) for contour in [bounds]+doc.board.cutouts for u,v in edges(contour))<edge-1e-7: return False
        return all(poly_distance(poly,obstacle)>=clearance-1e-7 for obstacle,clearance in obstacles)
    if allowed(start,end): points=[start,end]; expanded=0
    else:
        a=(round(start[0]/grid),round(start[1]/grid)); b=(round(end[0]/grid),round(end[1]/grid))
        def xy(k):return (k[0]*grid,k[1]*grid)
        if not allowed(start,xy(a)) or not allowed(xy(b),end): raise ValueError('Grid cannot escape the selected pad; use a smaller grid')
        queue=[(0,0,a)]; cost={a:0}; previous={}; expanded=0; found=False
        while queue and expanded<20000:
            if cancel and (cancel() if callable(cancel) else cancel.is_set()): raise RuntimeError('Electronics routing cancelled')
            _,g,node=heapq.heappop(queue)
            if g>cost[node]:continue
            if node==b:found=True;break
            expanded+=1
            for dx,dy in ((1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1)):
                nxt=(node[0]+dx,node[1]+dy); ng=g+math.hypot(dx,dy)
                if ng>=cost.get(nxt,math.inf) or not allowed(xy(node),xy(nxt)):continue
                previous[nxt]=node;cost[nxt]=ng;heapq.heappush(queue,(ng+math.dist(nxt,b),ng,nxt))
        if not found:raise ValueError(f'No clearance-safe route found within 20000 expansions ({expanded} expanded); existing tracks retained')
        path=[b]
        while path[-1]!=a:path.append(previous[path[-1]])
        points=[start]+[xy(p) for p in reversed(path)]+[end]
        points=[p for i,p in enumerate(points) if not i or math.dist(p,points[i-1])>1e-7]
        # Greedy line-of-sight simplification keeps the same exact clearance predicate.
        simplified=[points[0]];i=0
        while i<len(points)-1:
            j=len(points)-1
            while j>i+1 and not allowed(points[i],points[j]):j-=1
            simplified.append(points[j]);i=j
        points=simplified
    track=Track(id=payload['id'],net_id=net_id,layer=layer,points=points,width=width)
    if any(t.id==track.id for t in doc.board.tracks):raise ValueError('Route ID already exists; remove/ripup explicitly before replacing')
    doc=doc.model_copy(deep=True);doc.board.tracks.append(track)
    return doc,{'algorithm':'bounded single-layer A* with exact polygon clearance predicate','expanded':expanded,'length_mm':sum(math.dist(a,b) for a,b in zip(points,points[1:])),'track_id':track.id,'limitations':['No shove, BGA fanout or global autorouting; one terminal pair per request.']}
