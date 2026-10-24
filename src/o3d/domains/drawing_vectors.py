"""Original vector writers: physical-mm SVG/DXF and embedded-font PDF 1.7.

No screen capture is used. Curved HLR linework is a bounded-deflection polyline;
annotation text remains searchable in PDF and editable text in SVG/DXF.
"""
from __future__ import annotations

import base64
import html
import io
import json
import math
import os
from pathlib import Path
import zlib

STYLES = {"visible": (.35, None), "hidden": (.18, "3 1.5"), "tangent": (.18, None),
          "hatch": (.13, None), "center": (.18, "6 1 1 1"), "dimension": (.18, None)}


def font_for(pages):
    """Use an explicit installed font and validate every requested glyph.

    FreeSerif carries the GNU font exception permitting document embedding. No
    font is copied into application source; desktop packaging supplies this asset.
    """
    try:
        from fontTools.ttLib import TTFont
        from fontTools import subset
    except ImportError as e:
        raise RuntimeError("Drawing publication requires fonttools and an embeddable Unicode TTF font") from e
    candidates = [os.environ.get("O3D_DRAWING_FONT", ""), "/usr/share/fonts/truetype/freefont/FreeSerif.ttf",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    path=next((Path(p) for p in candidates if p and Path(p).is_file()),None)
    if path is None:
        raise RuntimeError("No drawing font installed. Set O3D_DRAWING_FONT to an embeddable Unicode TrueType font")
    font=TTFont(path)
    chars={ord(c) for p in pages for t in p["texts"] for c in t["text"]}
    cmap=font.getBestCmap()
    missing=chars-set(cmap)
    if missing:
        raise ValueError("Drawing font is missing glyphs: "+" ".join(f"U+{c:04X}" for c in sorted(missing)))
    if any(c>65535 for c in chars):
        raise ValueError("Drawing PDF supports BMP Unicode text; choose BMP symbols")
    options=subset.Options()
    options.name_IDs=[0,1,2,4,6,13,14]
    sub=subset.Subsetter(options=options);sub.populate(unicodes=chars);sub.subset(font)
    output=io.BytesIO();font.save(output)
    units=font["head"].unitsPerEm
    cmap=font.getBestCmap()
    glyphs=font.getGlyphOrder()
    metrics={c:(glyphs.index(cmap[c]),round(font["hmtx"].metrics[cmap[c]][0]*1000/units)) for c in chars}
    bbox=[round(getattr(font["head"],n)*1000/units) for n in ("xMin","yMin","xMax","yMax")]
    ascent=round(font["hhea"].ascent*1000/units);descent=round(font["hhea"].descent*1000/units)
    return output.getvalue(),metrics,bbox,ascent,descent


def svg(page):
    font,*_=font_for([page])
    w,h=page["width_mm"],page["height_mm"]
    parts=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}mm" height="{h}mm" viewBox="0 0 {w} {h}">',
           '<metadata>'+html.escape(json.dumps({"source_geometry_key":page["source_geometry_key"],"source_revision":page["source_revision"],"issued_revision":page["issued_revision"],"curve_deflection_mm":.02}))+ '</metadata>',
           '<defs><style>@font-face{font-family:OxygenDrawing;src:url(data:font/ttf;base64,'+base64.b64encode(font).decode()+') format("truetype")}</style></defs>',
           '<rect width="100%" height="100%" fill="white"/>']
    for path in page["paths"]:
        width,dash=STYLES[path["style"]]
        pts=" ".join(f"{p[0]:.6f},{p[1]:.6f}" for p in path["points"])
        parts.append(f'<polyline points="{pts}" fill="none" stroke="black" stroke-width="{width}"'+(f' stroke-dasharray="{dash}"' if dash else '')+'/>')
    for circle in page["circles"]:
        parts.append(f'<circle cx="{circle["center"][0]}" cy="{circle["center"][1]}" r="{circle["radius"]}" fill="none" stroke="black" stroke-width="0.18"/>')
    for text in page["texts"]:
        parts.append(f'<text x="{text["position"][0]}" y="{text["position"][1]}" font-family="OxygenDrawing" font-size="{text["height"]}" fill="black">{html.escape(text["text"])}</text>')
    return "\n".join(parts+["</svg>"])


def write_pdf(path,pages):
    font,metrics,bbox,ascent,descent=font_for(pages)
    objects=[]
    def obj(value):
        objects.append(value.encode() if isinstance(value,str) else value)
        return len(objects)
    def stream(data,extra=""):
        compressed=zlib.compress(data)
        return obj(f"<< /Length {len(compressed)} /Filter /FlateDecode {extra} >>\nstream\n".encode()+compressed+b"\nendstream")
    catalog=obj("");pagetree=obj("")
    subject="Oxygen drawing "+json.dumps([{k:page[k] for k in ("document","id","source_revision","issued_revision","source_geometry_key")} for page in pages],separators=(",",":"))
    info=obj("<< /Producer (Oxygen vector drawing writer) /Subject <FEFF"+subject.encode("utf-16-be").hex()+"> >>")
    fontfile=stream(font,f"/Length1 {len(font)}")
    descriptor=obj(f"<< /Type /FontDescriptor /FontName /OxygenDrawing /Flags 32 /FontBBox [{' '.join(map(str,bbox))}] /ItalicAngle 0 /Ascent {ascent} /Descent {descent} /CapHeight {ascent} /StemV 80 /FontFile2 {fontfile} 0 R >>")
    cmap=bytearray((max(metrics,default=0)+1)*2)
    for c,(glyph,_) in metrics.items():cmap[c*2:c*2+2]=glyph.to_bytes(2,"big")
    cidmap=stream(bytes(cmap))
    widths=" ".join(f"{c} [{width}]" for c,(_,width) in sorted(metrics.items()))
    cidfont=obj(f"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /OxygenDrawing /CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> /FontDescriptor {descriptor} 0 R /CIDToGIDMap {cidmap} 0 R /W [{widths}] >>")
    mapping="/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n/CMapName /OxygenUnicode def\n/CMapType 2 def\n1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
    codes=sorted(metrics)
    for start in range(0,len(codes),100):
        chunk=codes[start:start+100]
        mapping+=f"{len(chunk)} beginbfchar\n"+"\n".join(f"<{c:04X}> <{c:04X}>" for c in chunk)+"\nendbfchar\n"
    mapping+="endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend"
    unicode_map=stream(mapping.encode())
    type0=obj(f"<< /Type /Font /Subtype /Type0 /BaseFont /OxygenDrawing /Encoding /Identity-H /DescendantFonts [{cidfont} 0 R] /ToUnicode {unicode_map} 0 R >>")
    page_ids=[];mm=72/25.4
    for page in pages:
        h=page["height_mm"]
        content=["0 G 0 g"]
        def point(p):return f"{p[0]*mm:.5f} {(h-p[1])*mm:.5f}"
        for line in page["paths"]:
            width,dash=STYLES[line["style"]]
            dash_values=" ".join(f"{float(n)*mm:.4f}" for n in dash.split()) if dash else ""
            content.append(f"{width*mm:.4f} w [{dash_values}] 0 d")
            content.append(point(line["points"][0])+" m")
            content.extend(point(p)+" l" for p in line["points"][1:]);content.append("S")
        for circle in page["circles"]:
            cx,cy=circle["center"];r=circle["radius"]
            points=[[cx+r*math.cos(i*math.tau/96),cy+r*math.sin(i*math.tau/96)] for i in range(97)]
            content.append(f"{.18*mm:.4f} w [] 0 d "+point(points[0])+" m")
            content.extend(point(p)+" l" for p in points[1:]);content.append("S")
        for t in page["texts"]:
            encoded=t["text"].encode("utf-16-be").hex().upper()
            content.append(f"BT /F1 {t['height']*mm:.5f} Tf 1 0 0 1 {point(t['position'])} Tm <{encoded}> Tj ET")
        data=stream("\n".join(content).encode())
        page_ids.append(obj(f"<< /Type /Page /Parent {pagetree} 0 R /MediaBox [0 0 {page['width_mm']*mm:.5f} {h*mm:.5f}] /Resources << /Font << /F1 {type0} 0 R >> >> /Contents {data} 0 R >>"))
    objects[catalog-1]=f"<< /Type /Catalog /Pages {pagetree} 0 R >>".encode()
    objects[pagetree-1]=f"<< /Type /Pages /Count {len(pages)} /Kids [{' '.join(f'{p} 0 R' for p in page_ids)}] >>".encode()
    output=bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n");offsets=[0]
    for i,value in enumerate(objects,1):
        offsets.append(len(output));output+=f"{i} 0 obj\n".encode()+value+b"\nendobj\n"
    xref=len(output)
    output+=f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode()
    output+="".join(f"{offset:010d} 00000 n \n" for offset in offsets[1:]).encode()
    output+=f"trailer\n<< /Size {len(objects)+1} /Root {catalog} 0 R /Info {info} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(output)


def write_dxf(path,page):
    """DXF R2010 paper space coordinates in mm; dimension graphics and real TEXT.

    Annotations are intentionally exported as editable text/vector entities,
    rather than claiming proprietary associative DIMENSION/GD&T semantics.
    """
    pairs=[]
    def add(*items):
        pairs.extend(str(item) for item in items)
    add(999,"OXYGEN "+json.dumps({k:page[k] for k in ("document","source_revision","issued_revision","source_geometry_key")},separators=(",",":")))
    add(0,"SECTION",2,"HEADER",9,"$ACADVER",1,"AC1024",9,"$INSUNITS",70,4,9,"$MEASUREMENT",70,1,0,"ENDSEC")
    add(0,"SECTION",2,"TABLES",0,"TABLE",2,"LTYPE",70,3)
    for name,pattern in [("CONTINUOUS",[]),("HIDDEN",[3,-1.5]),("CENTER",[6,-1,1,-1])]:
        add(0,"LTYPE",2,name,70,0,3,name,72,65,73,len(pattern),40,sum(abs(p) for p in pattern))
        for length in pattern:add(49,length,74,0)
    add(0,"ENDTAB",0,"ENDSEC",0,"SECTION",2,"ENTITIES")
    h=page["height_mm"]
    for line in page["paths"]:
        layer=line["style"].upper()
        line_type="HIDDEN" if line["style"]=="hidden" else "CENTER" if line["style"]=="center" else "CONTINUOUS"
        width=round(STYLES[line["style"]][0]*100)
        for a,b in zip(line["points"],line["points"][1:]):
            add(0,"LINE",100,"AcDbEntity",8,layer,6,line_type,370,width,100,"AcDbLine",10,a[0],20,h-a[1],30,0,11,b[0],21,h-b[1],31,0)
    for c in page["circles"]:
        add(0,"CIRCLE",100,"AcDbEntity",8,"DIMENSION",100,"AcDbCircle",10,c["center"][0],20,h-c["center"][1],30,0,40,c["radius"])
    for t in page["texts"]:
        # DXF 2007+ uses UTF-8; preserve text directly for independent readers.
        text=t["text"]
        add(0,"TEXT",100,"AcDbEntity",8,"ANNOTATION",100,"AcDbText",10,t["position"][0],20,h-t["position"][1],30,0,40,t["height"],1,text,50,0,100,"AcDbText")
    add(0,"ENDSEC",0,"EOF")
    path.write_text("\n".join(pairs)+"\n",encoding="utf-8")
