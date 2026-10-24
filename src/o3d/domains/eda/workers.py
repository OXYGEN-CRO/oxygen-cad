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
from .checks import erc, drc, mcad, flattened
from .kicad import export_board


def cancelled(cancel):return bool(cancel and (cancel() if callable(cancel) else cancel.is_set()))


def run(argv,cwd,cancel=None,timeout=120,allowed=(0,)):
    """No shell; process output stays bounded on disk and is exposed as an artifact."""
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
    return [{'name':p.name,'path':str(p.relative_to(store.cache)),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size} for p in sorted(path.rglob('*')) if p.is_file()]


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


def write_sources(doc,path,variant=None):
    (path/'source.oxygen-eda.json').write_text(doc.model_dump_json(indent=2)+'\n')
    (path/'board.kicad_pcb').write_text(export_board(doc))
    a=assembly(doc,variant)
    (path/'bom.csv').write_text(csv_text(a['bom']));(path/'positions.csv').write_text(csv_text(a['positions']))
    (path/'assembly.json').write_text(json.dumps(a,indent=2)+'\n')
    (path/'mcad.json').write_text(json.dumps(mcad(doc),indent=2)+'\n')
    for kind,fn in [('erc',erc),('drc',drc)]: (path/(kind+'.json')).write_text(json.dumps(fn(doc),indent=2)+'\n')
    return a


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
    output=path/'kicad-drc.json';run([tool,'pcb','drc','--format','json','--output',output,source],path,cancel)
    checked=json.loads(output.read_text());local_erc=erc(doc);local_drc=drc(doc)
    violations=checked.get('violations',[])+checked.get('unconnected_items',[])
    gate=not violations and local_erc['state']=='passed' and local_drc['state']=='passed'
    manifest={'schema_version':1,'source_hash':digest(doc),'variant':variant or 'base','tool':{'name':'kicad-cli','version':version,'licence':'GPL-3.0-or-later; external process, no libraries bundled'},'layers':layers,'units':'mm','origin':doc.board.origin,'contours':1+len(doc.board.cutouts),'review_state':'checks_passed' if gate else 'blocked_by_findings','limitations':['Supplier DFM, physical assembly/bring-up and unresolved component-model licences are outside this software gate.'],'files':artifacts(store,path)}
    (path/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    if operation=='release':
        # Reproducible ZIP timestamps/order/permissions; no volatile tool logs in release.
        bundle=path/'release.zip'
        with zipfile.ZipFile(bundle,'w',compression=zipfile.ZIP_DEFLATED) as z:
            for file in sorted(path.rglob('*')):
                if not file.is_file() or file==bundle or file.name.startswith('tool-'):continue
                info=zipfile.ZipInfo(str(file.relative_to(path)),date_time=(2020,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o644<<16;z.writestr(info,file.read_bytes())
    return {'state':'exported' if operation=='fabrication' else 'ready' if gate else 'blocked','source_hash':digest(doc),'tool_version':version,'manifest':manifest,'artifacts':artifacts(store,path)}


VALUE = re.compile(r'^[+\-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+\-]?\d+)?(?:[a-zA-Z]+)?$')
NAME = re.compile(r'^[A-Za-z][A-Za-z0-9_]*$')


def spice_deck(doc,payload):
    aliases,pins=__import__('o3d.domains.eda.checks',fromlist=['net_map']).net_map(doc)
    nets=flattened(doc);nodes={n['id']:('0' if any(name.strip().upper() in ('0','GND') for name in n['name'].split('/')) else 'n'+str(i+1)) for i,n in enumerate(nets)}
    if '0' not in nodes.values():raise ValueError('Simulation requires an explicit GND/0 net')
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
