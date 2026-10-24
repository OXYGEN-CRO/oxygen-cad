"""Bounded external EDA workers and reproducible electrical release artifacts."""
from __future__ import annotations
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from .schema import digest
from .checks import erc, drc, mcad, flattened, net_map, pad_polygon, pad_world, rotate
from .kicad import export_board


def cancelled(cancel):return bool(cancel and (cancel() if callable(cancel) else cancel.is_set()))


def run(argv,cwd,cancel=None,timeout=120,allowed=(0,)):
    """No shell; process output stays bounded on disk and is exposed as an artifact."""
    if cancelled(cancel):raise RuntimeError('Electronics worker cancelled before starting')
    log=Path(cwd)/('tool-'+str(time.time_ns())+'.log')
    with log.open('w') as output:
        proc=subprocess.Popen([str(a) for a in argv],cwd=cwd,stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
        start=time.monotonic()
        try:
            while proc.poll() is None:
                if cancelled(cancel):raise RuntimeError('Electronics worker cancelled')
                if time.monotonic()-start>timeout:raise RuntimeError(f'EDA worker exceeded {timeout}s')
                if log.stat().st_size>20_000_000:raise RuntimeError('EDA worker output exceeded 20 MB')
                time.sleep(0.05)
        except BaseException:
            import signal
            os.killpg(proc.pid,signal.SIGKILL);proc.wait();raise
    text=log.read_text(errors='replace')
    if cancelled(cancel):raise RuntimeError('Electronics worker cancelled')
    if proc.returncode not in allowed:raise RuntimeError(f'{Path(argv[0]).name} exited {proc.returncode}: {text[-3000:]}')
    return text


def binary(name):
    path=shutil.which(name)
    if not path:raise RuntimeError(f'{name} is unavailable; install the independent tool. No EDA result was produced.')
    return path


def folder(store,doc,operation):
    root=store.cache/'electronics'/digest(doc);root.mkdir(parents=True,exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=operation+'-',dir=root))


def artifacts(store,path):
    return [{'name':str(p.relative_to(path)) if 'external-tool-originals' in p.parts else p.name,'path':str(p.relative_to(store.cache)),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size} for p in sorted(path.rglob('*')) if p.is_file()]


def assembly(doc,variant_id=None):
    variant=next((v for v in doc.variants if v.id==variant_id),None)
    if variant_id and variant is None:raise ValueError('Unknown assembly variant')
    dnp=set(variant.dnp if variant else []);values=variant.values if variant else {};parts={(p.id,p.version):p for p in doc.library};comps={c.id:c for c in doc.components};groups={};positions=[]
    for c in doc.components:
        if c.id in dnp:continue
        part=parts[c.library_id,c.library_version];value=values.get(c.id,c.value)
        key=(part.manufacturer,part.mpn,part.id,part.version,value)
        groups.setdefault(key,[]).append(c)
    bom=[]
    for (manufacturer,mpn,lib,version,value),cs in sorted(groups.items()):
        bom.append({'references':' '.join(sorted(c.reference for c in cs)),'component_ids':' '.join(sorted(c.id for c in cs)),'quantity':len(cs),'manufacturer':manufacturer,'mpn':mpn,'value':value,'footprint':lib,'library_version':version,'missing_mpn':not bool(mpn)})
    for p in doc.board.placements:
        if p.component_id in dnp:continue
        c=comps[p.component_id];part=parts[c.library_id,c.library_version]
        positions.append({'reference':c.reference,'component_id':c.id,'x_mm':p.at[0]-doc.board.origin[0],'y_mm':p.at[1]-doc.board.origin[1],'rotation_deg':p.rotation%360,'side':p.side,'footprint':part.id,'value':values.get(c.id,c.value)})
    return {'source_hash':digest(doc),'variant':variant_id or 'base','bom':bom,'positions':positions,'dnp':sorted(dnp),'convention':'Board XY mm minus declared origin; rotation CCW degrees; bottom reflects local X before rotation; no implicit bottom X negation','limitations':['Assembler-specific rotation/origin profile must be agreed before production.','Missing MPN remains explicit.']}


def csv_text(rows):
    if not rows:return ''
    stream=io.StringIO();writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows);return stream.getvalue()


def drawing(doc,side,variant=None):
    """Original dimensioned 1:1 SVG assembly/drill drawing, in source XY mm."""
    from xml.sax.saxutils import escape
    board=doc.board;xs=[p[0] for p in board.outline];ys=[p[1] for p in board.outline]
    xmin,xmax,ymin,ymax=min(xs),max(xs),min(ys),max(ys)
    width,height=xmax-xmin+20,ymax-ymin+24
    out=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}mm" height="{height}mm" viewBox="{xmin-10} {ymin-10} {width} {height}">', '<g fill="none" stroke="black" stroke-width="0.1">']
    for contour in [board.outline]+board.cutouts:out.append('<polygon points="'+' '.join(f'{x},{y}' for x,y in contour)+'"/>')
    for h in board.holes:
        if h.slot:out.append(f'<line x1="{h.at[0]}" y1="{h.at[1]}" x2="{h.at[0]+h.slot[0]}" y2="{h.at[1]+h.slot[1]}" stroke-width="{h.diameter}" stroke-linecap="round"/>')
        else:out.append(f'<circle cx="{h.at[0]}" cy="{h.at[1]}" r="{h.diameter/2}"/>')
    populated={p['component_id'] for p in assembly(doc,variant)['positions']};comps={c.id:c for c in doc.components};parts={(p.id,p.version):p for p in doc.library}
    for place in board.placements:
        if place.component_id not in populated or (side!='drill' and place.side!=side):continue
        c=comps[place.component_id];part=parts[c.library_id,c.library_version]
        for pad in part.pads:
            if side=='drill' and pad.kind=='smd':continue
            if side=='drill':
                center=pad_world(pad,place)
                if pad.slot:
                    span=rotate(pad.slot,pad.rotation);span=(span[0]*(-1 if place.side=='bottom' else 1),span[1]);span=rotate(span,place.rotation)
                    out.append(f'<line x1="{center[0]-span[0]/2}" y1="{center[1]-span[1]/2}" x2="{center[0]+span[0]/2}" y2="{center[1]+span[1]/2}" stroke-width="{pad.drill}" stroke-linecap="round"/>')
                else:out.append(f'<circle cx="{center[0]}" cy="{center[1]}" r="{pad.drill/2}"/>')
            else:
                poly=pad_polygon(pad,place)
                out.append('<polygon points="'+' '.join(f'{x:.6f},{y:.6f}' for x,y in poly)+'"/>')
        out.append(f'<text x="{place.at[0]}" y="{place.at[1]-2}" stroke="none" fill="black" font-size="1.5" text-anchor="middle">{escape(c.reference)}</text>')
    title=f'{doc.name} | {side} | {variant or "base"} | {xmax-xmin:g} x {ymax-ymin:g} mm | {sum(l.thickness for l in board.stackup):g} mm thick'
    out += ['</g>',f'<text x="{xmin}" y="{ymax+6}" font-size="1.3">{escape(title)}</text>',f'<text x="{xmin}" y="{ymax+9}" font-size="1.1">Source {digest(doc)[:16]} | Source XY; bottom assembly viewed from top. Scale 1:1 at physical SVG size.</text>','</svg>']
    return '\n'.join(out)+'\n'


def write_sources(doc,path,variant=None):
    (path/'source.oxygen-eda.json').write_text(doc.model_dump_json(indent=2)+'\n')
    (path/'board.kicad_pcb').write_text(export_board(doc))
    a=assembly(doc,variant)
    (path/'bom.csv').write_text(csv_text(a['bom']));(path/'positions.csv').write_text(csv_text(a['positions']))
    (path/'assembly.json').write_text(json.dumps(a,indent=2)+'\n')
    (path/'mcad.json').write_text(json.dumps(mcad(doc),indent=2)+'\n')
    for side in ('top','bottom','drill'):(path/(side+'-drawing.svg')).write_text(drawing(doc,side,variant))
    for kind,fn in [('erc',erc),('drc',drc)]: (path/(kind+'.json')).write_text(json.dumps(fn(doc),indent=2)+'\n')
    return a


def verify_fabrication(out,copper,path):
    """Check actual X2 functions; do not trust generated job metadata alone."""
    functions={};normalizations=[]
    for layer in copper:
        file=out/('board-'+layer.replace('.','_')+'.gbr')
        if not file.is_file():raise RuntimeError(f'Missing copper output {layer}')
        match=re.search(r'%TF.FileFunction,([^*]+)\*%',file.read_text())
        expected=f'Copper,L{copper.index(layer)+1},'+('Top' if layer=='F.Cu' else 'Bot' if layer=='B.Cu' else 'Inr')
        if not match or match.group(1)!=expected:raise RuntimeError(f'Gerber X2 layer mapping mismatch for {layer}: expected {expected}')
        functions[file.name]=expected
    job=out/'board-job.gbrjob'
    if job.exists():
        data=json.loads(job.read_text())
        for entry in data.get('FilesAttributes',[]):
            name=entry.get('Path');expected=functions.get(name)
            if expected and entry.get('FileFunction')!=expected:
                normalizations.append({'file':job.name,'entry':name,'field':'FileFunction','before':entry.get('FileFunction'),'after':expected,'reason':'Normalized to independently checked actual Gerber X2 layer header'})
                entry['FileFunction']=expected
        if normalizations:
            original=path/'external-tool-originals';original.mkdir(exist_ok=True);shutil.copy2(job,original/job.name)
            job.write_text(json.dumps(data,indent=2,sort_keys=True)+'\n')
    return {'copper_file_functions':functions,'normalizations':normalizations}


def normalized_metadata(file):
    """Normalize timestamps only, never geometry or electrical values."""
    text=file.read_text();epoch='2020-01-01T00:00:00+00:00'
    if file.name=='kicad-drc.json' or file.suffix=='.gbrjob':
        data=json.loads(text)
        if file.name=='kicad-drc.json':data['date']=epoch
        else:data.get('Header',{})['CreationDate']=epoch
        return (json.dumps(data,sort_keys=True,indent=2)+'\n').encode()
    if file.suffix in ('.gbr','.drl'):
        text=re.sub(r'(TF\.CreationDate,)[^*\r\n]+',lambda m:m.group(1)+epoch,text)
        text=re.sub(r'(?<=date )\d{4}-\d\d-\d\d[T ][0-9:+-]+','2020-01-01T00:00:00+00:00',text)
    return text.encode()


def kicad(store,doc,operation,payload,cancel=None):
    tool=binary('kicad-cli');path=folder(store,doc,operation);version=run([tool,'version'],path,cancel).strip()
    source=path/'board.kicad_pcb';source.write_text(export_board(doc))
    if operation=='kicad_drc':
        output=path/'kicad-drc.json'
        run([tool,'pcb','drc','--format','json','--output',output,source],path,cancel)
        parsed=json.loads(output.read_text());violations=parsed.get('violations',[])+parsed.get('unconnected_items',[])+parsed.get('schematic_parity',[])
        return {'source_hash':digest(doc),'state':'failed' if violations else 'passed','tool':'kicad-cli','tool_version':version,'report':parsed,'artifacts':artifacts(store,path),'limitations':['Independent board DRC; generated board does not contain original hierarchical schematic.']}
    if operation=='export_step':
        output=path/'board.step';run([tool,'pcb','export','step','--output',output,source],path,cancel)
        if not output.exists() or not output.read_text(errors='ignore').lstrip().startswith('ISO-10303-21;'):raise RuntimeError('KiCad did not produce a STEP exchange file')
        return {'state':'exported','source_hash':digest(doc),'tool_version':version,'artifacts':artifacts(store,path),'limitations':['Missing component 3D models are not synthesized. Refer to MCAD identity report.']}
    if operation not in ('fabrication','release'):raise ValueError('Unknown KiCad worker operation')
    variant=payload.get('variant');write_sources(doc,path,variant)
    copper=[l.id for l in doc.board.stackup if l.kind=='copper'];layers=copper+['F.Mask','B.Mask','F.Paste','B.Paste','Edge.Cuts']
    out=path/'fabrication';out.mkdir()
    run([tool,'pcb','export','gerbers','--layers',','.join(layers),'--no-protel-ext','--disable-aperture-macros','--output',str(out)+'/',source],path,cancel)
    run([tool,'pcb','export','drill','--excellon-units','mm','--excellon-zeros-format','decimal','--excellon-oval-format','route','--excellon-separate-th','--output',str(out)+'/',source],path,cancel)
    # Verify actual output inventory and coordinate units before calling export complete.
    gerbers=list(out.glob('*.gbr'));drills=list(out.glob('*.drl'))
    if len(gerbers)!=len(layers) or not drills:raise RuntimeError('KiCad fabrication output inventory does not match requested layer set')
    if any('%MOMM*%' not in p.read_text() for p in gerbers):raise RuntimeError('Unexpected Gerber unit declaration')
    if any('METRIC' not in p.read_text() for p in drills):raise RuntimeError('Unexpected Excellon unit declaration')
    verification=verify_fabrication(out,copper,path)
    output=path/'kicad-drc.json';run([tool,'pcb','drc','--format','json','--output',output,source],path,cancel)
    checked=json.loads(output.read_text());local_erc=erc(doc);local_drc=drc(doc)
    violations=checked.get('violations',[])+checked.get('unconnected_items',[])
    gate=not violations and local_erc['state']=='passed' and local_drc['state']=='passed'
    files=[p for p in sorted(path.rglob('*')) if p.is_file() and not p.name.startswith('tool-') and 'external-tool-originals' not in p.parts]
    if operation=='release':
        original=path/'external-tool-originals';original.mkdir(exist_ok=True)
        for file in files:
            content=normalized_metadata(file)
            if content!=file.read_bytes():
                target=original/file.relative_to(path);target.parent.mkdir(parents=True,exist_ok=True)
                if not target.exists():shutil.copy2(file,target)
                file.write_bytes(content)
    records=[{'path':str(p.relative_to(path)),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size} for p in files]
    manifest={'schema_version':1,'source_hash':digest(doc),'variant':variant or 'base','tool':{'name':'kicad-cli','version':version,'licence':'GPL-3.0-or-later; external process, no libraries bundled'},'layers':layers,'units':'mm','origin':doc.board.origin,'contours':1+len(doc.board.cutouts),'verification':verification,'timestamp_policy':'2020-01-01 UTC metadata epoch for reproducible release; raw tool timestamps retained outside ZIP' if operation=='release' else 'actual tool timestamps','review_state':'checks_passed' if gate else 'blocked_by_findings','limitations':['Supplier DFM, physical assembly/bring-up and unresolved component-model licences are outside this software gate.'],'files':records}
    (path/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    if operation=='release':
        # Reproducible ZIP timestamps/order/permissions; no volatile tool logs in release.
        bundle=path/'release.zip'
        with zipfile.ZipFile(bundle,'w',compression=zipfile.ZIP_DEFLATED) as z:
            for file in sorted(path.rglob('*')):
                if not file.is_file() or file==bundle or file.name.startswith('tool-') or 'external-tool-originals' in file.parts:continue
                info=zipfile.ZipInfo(str(file.relative_to(path)),date_time=(2020,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o644<<16;z.writestr(info,file.read_bytes())
    return {'state':'exported' if operation=='fabrication' else 'ready' if gate else 'blocked','source_hash':digest(doc),'tool_version':version,'manifest':manifest,'artifacts':artifacts(store,path)}


VALUE = re.compile(r'^[+\-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+\-]?\d+)?(?:[a-zA-Z]+)?$')
NAME = re.compile(r'^[A-Za-z][A-Za-z0-9_]*$')


def spice_deck(doc,payload):
    aliases,pins=net_map(doc)
    nets=flattened(doc)
    candidates=[n['id'] for n in nets if any(name.strip().upper() in ('0','GND') for name in n['name'].split('/'))]
    if payload.get('ground_net'):
        if payload['ground_net'] not in aliases:raise ValueError('Simulation ground_net must reference an existing net')
        ground=aliases[payload['ground_net']]
    elif len(candidates)==1:ground=candidates[0]
    else:raise ValueError('Simulation requires one unambiguous GND/0 net or an explicit ground_net ID; scoped grounds are not silently merged')
    nodes={n['id']:('0' if n['id']==ground else 'n'+str(i+1)) for i,n in enumerate(nets)}
    lookup={n['id']:nodes[n['id']] for n in nets}
    for n in nets:
        for original in n['source_net_ids']:lookup[original]=nodes[n['id']]
    parts={(p.id,p.version):p for p in doc.library};lines=['Oxygen EDA reproducible circuit'];models={};sources={}
    for c in doc.components:
        part=parts[c.library_id,c.library_version];model=part.spice
        if model is None:raise ValueError(f'{c.reference}: missing simulation model; no substitute was used')
        ordered=[]
        for pid in model.pin_order:
            if f'{c.id}/{pid}' not in pins:raise ValueError(f'{c.reference}/{pid}: SPICE pin is unconnected')
            ordered.append(nodes[pins[f'{c.id}/{pid}']])
        name=model.kind+c.reference
        if not NAME.fullmatch(name):raise ValueError('Unsafe SPICE instance identifier')
        if model.kind in ('R','C','L','V','I'):
            if len(ordered)!=2:raise ValueError('Primitive circuit element requires exactly two mapped pins')
            value=c.value or model.value
            if not VALUE.fullmatch(value):raise ValueError(f'{c.reference}: primitive requires a scalar SPICE value with units')
            if model.kind in ('V','I'):
                # Explicit AC source amplitude; DC value remains the component scalar.
                ac=payload.get('ac_sources',{}).get(c.id)
                if ac is not None:
                    if not VALUE.fullmatch(str(ac)):raise ValueError('Invalid AC source amplitude')
                    value+=' AC '+str(ac)
                pulse=payload.get('pulses',{}).get(c.id)
                if pulse is not None:
                    if len(pulse)!=7 or not all(VALUE.fullmatch(str(v)) for v in pulse):raise ValueError('PULSE requires seven scalar values')
                    value='PULSE('+ ' '.join(map(str,pulse))+')'
                sources[c.id]=name
            lines.append(name+' '+' '.join(ordered)+' '+value)
        else:
            expected={'D':2,'Q':3,'M':4}.get(model.kind)
            if expected and len(ordered)!=expected:raise ValueError('Device pin mapping does not match SPICE primitive')
            roles={'D':['A','K'],'Q':['C','B','E'],'M':['D','G','S','B']}.get(model.kind)
            if roles and [model.terminal_roles.get(p) for p in model.pin_order]!=roles:
                raise ValueError('Explicit semiconductor terminal roles do not match SPICE pin order')
            if not NAME.fullmatch(model.model_name) or not model.model_text or not model.source:raise ValueError('Semiconductor/subcircuit requires named, attributed inline model closure')
            text=model.model_text
            # External file loading, shell/control commands and arbitrary interpreter directives are forbidden.
            if any(ch in text for ch in ('!','$',';','\\')):raise ValueError('Unsafe model syntax')
            for line in text.splitlines():
                line=line.strip()
                if line.startswith('.') and not re.match(r'^\.(model|subckt|ends)\b',line,re.I):raise ValueError('Only inline .model/.subckt/.ends model directives are allowed')
            actual=hashlib.sha256(text.encode()).hexdigest()
            if model.sha256!=actual:raise ValueError('Simulation model digest does not match inline content')
            if model.model_name in models and models[model.model_name]!=text:raise ValueError('Conflicting model definitions')
            models[model.model_name]=text;lines.append(name+' '+' '.join(ordered)+' '+model.model_name)
    lines.extend(models.values());analysis=payload.get('analysis','op');probes=payload.get('probes',[])
    if not probes or len(probes)>32 or any(p not in lookup for p in probes):raise ValueError('Select 1–32 existing net IDs as probes')
    if analysis=='op':command='op'
    elif analysis=='dc':
        source=payload.get('source');start=float(payload['start']);stop=float(payload['stop']);step=float(payload['step'])
        if source not in sources or not all(math.isfinite(v) for v in (start,stop,step)) or step==0 or (stop-start)/step<0 or abs((stop-start)/step)>100000:raise ValueError('Invalid bounded DC sweep')
        command=f'dc {sources[source]} {start} {stop} {step}'
    elif analysis=='ac':
        start=float(payload['start']);stop=float(payload['stop']);points=int(payload.get('points',20))
        if not 0<start<stop<1e12 or not 1<=points<=1000:raise ValueError('Invalid AC frequency sweep')
        command=f'ac dec {points} {start} {stop}'
    elif analysis=='tran':
        step=float(payload['step']);stop=float(payload['stop'])
        if not 0<step<stop or stop/step>100000:raise ValueError('Invalid transient duration/step')
        command=f'tran {step} {stop}'
    else:raise ValueError('Supported analyses: op, dc, ac, tran')
    lines += ['.control','set wr_vecnames','set wr_singlescale',command,'wrdata traces.tsv '+' '.join('v('+lookup[p]+')' for p in probes),'quit','.endc','.end']
    return '\n'.join(lines)+'\n',{'analysis':analysis,'probes':probes,'nodes':lookup,'models':{k:hashlib.sha256(v.encode()).hexdigest() for k,v in models.items()}}


def simulate(store,doc,payload,cancel=None):
    tool=binary('ngspice');deck,meta=spice_deck(doc,payload);path=folder(store,doc,'spice');source=path/'circuit.cir';source.write_text(deck)
    version=run([tool,'--version'],path,cancel);log=run([tool,'-b',source],path,cancel)
    data=path/'traces.tsv'
    if not data.exists() or re.search(r'(timestep too small|no convergence|fatal error|Error:)',log,re.I):raise RuntimeError('ngspice did not converge or did not produce probe data: '+log[-3000:])
    if data.stat().st_size>50_000_000:raise RuntimeError('ngspice trace output exceeds 50 MB; reduce sweep size or probes')
    rows=data.read_text().splitlines()
    if len(rows)<2:raise RuntimeError('ngspice produced no solved data rows')
    headers=rows[0].split();values=[]
    for line in rows[1:]:
        row=[float(v) for v in line.split()]
        if len(row)!=len(headers) or not all(math.isfinite(v) for v in row):raise RuntimeError('ngspice produced malformed/nonfinite trace data')
        values.append(row)
    result={'state':'solved','source_hash':digest(doc),'deck_sha256':hashlib.sha256(deck.encode()).hexdigest(),'tool':'ngspice','tool_version':next((l.strip('* ') for l in version.splitlines() if 'ngspice-' in l),version[:200]),**meta,'columns':headers,'rows':values,'artifacts':artifacts(store,path),'limitations':['Electrical SPICE model result only; not EMC, safety, thermal or hardware validation.']}
    (path/'result.json').write_text(json.dumps(result,indent=2)+'\n');return result


import math
