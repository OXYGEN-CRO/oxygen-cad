"""Create a nominal overall-dimension PDF from exact STEP orthographic projections."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys


def drawing(source, output, title, revision, notes=()):
    import build123d as bd
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import mm
    output = Path(output)
    if output.exists(): raise ValueError('Output already exists')
    if output.suffix.lower() != '.pdf': raise ValueError('Output must end in .pdf')
    part = bd.import_step(source)
    if not part.is_valid or not len(part.solids()): raise ValueError('STEP must contain valid solids')
    box = part.bounding_box(); center = box.center(); dimensions = tuple(box.size)
    # A3 landscape; projections are located in independent third-angle panels.
    views = [('FRONT', (0,-1,0), (0,0,1), (35,45,150,100)),
             ('TOP', (0,0,1), (0,1,0), (35,165,150,100)),
             ('RIGHT', (1,0,0), (0,0,1), (235,45,150,100))]
    panels = []; maxx = maxy = 0
    for name, axis, up, rectangle in views:
        eye = center + bd.Vector(axis) * max(100, math.sqrt(sum(v*v for v in dimensions)) * 4)
        visible, hidden = part.project_to_viewport(eye, viewport_up=up, look_at=center)
        paths = []
        for dashed, edges in [(False, visible), (True, hidden)]:
            for edge in edges:
                points = edge.positions(deflection=.03)
                paths.append((dashed, [(p.X,p.Y) for p in points]))
        points = [p for _,line in paths for p in line]
        if not points: raise ValueError('Projection produced no edges')
        lo = [min(p[i] for p in points) for i in (0,1)]; hi = [max(p[i] for p in points) for i in (0,1)]
        width,height = hi[0]-lo[0],hi[1]-lo[1]
        maxx=max(maxx,width);maxy=max(maxy,height)
        panels.append((name,rectangle,paths,lo,hi))
    scale = min(1.0, 130/maxx, 75/maxy)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Render in memory so failures cannot leave a plausible partial PDF behind.
    import io
    buffer = io.BytesIO(); c = canvas.Canvas(buffer, pagesize=(420*mm,297*mm), invariant=1)
    c.setTitle(title);c.setAuthor('Oxygen CAD')
    c.rect(10*mm,10*mm,400*mm,277*mm)
    c.setFont('Helvetica',8)
    for name,(x,y,w,h),paths,lo,hi in panels:
        ox=(x+(w-(hi[0]-lo[0])*scale)/2)*mm;oy=(y+(h-(hi[1]-lo[1])*scale)/2)*mm
        for dashed,line in paths:
            c.setDash([2,2] if dashed else []);c.setLineWidth(.35 if dashed else .7)
            pen=c.beginPath()
            for i,(px,py) in enumerate(line):
                getattr(pen,'moveTo' if i==0 else 'lineTo')(ox+(px-lo[0])*scale*mm,oy+(py-lo[1])*scale*mm)
            c.drawPath(pen)
        c.setDash();c.setLineWidth(.3)
        left,right=ox,ox+(hi[0]-lo[0])*scale*mm;bottom,top=oy,oy+(hi[1]-lo[1])*scale*mm
        c.line(left,bottom-6*mm,right,bottom-6*mm)
        c.line(left,bottom-8*mm,left,bottom);c.line(right,bottom-8*mm,right,bottom)
        c.drawCentredString((left+right)/2,bottom-10*mm,f'{hi[0]-lo[0]:.3f} mm')
        c.line(left-6*mm,bottom,left-6*mm,top)
        c.line(left-8*mm,bottom,left,bottom);c.line(left-8*mm,top,left,top)
        c.saveState();c.translate(left-9*mm,(bottom+top)/2);c.rotate(90)
        c.drawCentredString(0,0,f'{hi[1]-lo[1]:.3f} mm');c.restoreState()
        c.drawCentredString((x+w/2)*mm,(y+h+4)*mm,name)
    c.setFont('Helvetica-Bold',10);c.drawString(215*mm,253*mm,title[:70])
    c.setFont('Helvetica',8)
    info=[f'Revision: {revision} | Units: mm | Third-angle | Scale: {scale:.5g}:1',
          'Nominal overall dimensions only; no tolerances are implied.',
          'No section/detail views, GD&T or automatic hole callouts.',
          'STEP SHA256: '+hashlib.sha256(Path(source).read_bytes()).hexdigest()]
    info+=list(notes)
    for i,line in enumerate(info):
        if len(line)>100 or i>14: raise ValueError('Notes exceed the available drawing panel; shorten them')
        c.drawString(215*mm,(244-i*5)*mm,line)
    c.showPage();c.save()
    with output.open('xb') as stream: stream.write(buffer.getvalue())
    return {'output':str(output.resolve()),'source':str(Path(source).resolve()),'revision':revision,
            'dimensions_mm':dimensions,'scale':scale,'views':['front','top','right'],
            'limitations':['nominal overall dimensions only','no hole callouts, tolerances, sections or GD&T']}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('step',type=Path);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--title',required=True);p.add_argument('--revision',required=True);p.add_argument('--note',action='append',default=[])
    a=p.parse_args()
    try: print(json.dumps(drawing(a.step,a.out,a.title,a.revision,a.note),indent=2,allow_nan=False))
    except (ImportError,OSError,ValueError) as e:
        print(json.dumps({'error':str(e)}),file=sys.stderr);return 1
    return 0


if __name__=='__main__': raise SystemExit(main())
