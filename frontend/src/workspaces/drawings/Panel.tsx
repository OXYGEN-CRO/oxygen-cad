import { useEffect, useState } from "react";
import { Button, Input, SelectControl } from "../../components/ui";
import { artifact } from "../../api";
import type { DomainPanelProps, JsonObject } from "../contracts";
import "./drawings.css";

type Ref = { kind: string; target?: string; point?: number[]; column?: number };
type DrawingView = { id: string; kind: string; orientation: string; parent?: string; position: number[]; scale: number; hidden: boolean; [key: string]: unknown };
type Annotation = { id: string; view: string; kind: string; references: Ref[]; position: number[]; text?: string; [key: string]: unknown };
type Sheet = { id: string; name: string; size: string; views: DrawingView[]; annotations: Annotation[]; tables: unknown[] };
type DrawingDocument = { id: string; title: string; number: string; standard: string; projection: string; units: string; source_revision: number; source_geometry_key: string; sheets: Sheet[]; [key: string]: unknown };
type DrawingData = { schema_version: 1; documents: DrawingDocument[]; templates: unknown[]; pmi: unknown[] };
type DrawingTemplate = { id: string; name: string; standard: string; projection: string; units: string; sheet: Sheet; title_block: Record<string,string> };
type Publication = { documents?: { document: string; pdf?: string; artifact?: string; artifacts?: { sheet: string; svg: string; dxf: string }[] }[]; [key: string]: unknown };

const empty: DrawingData = { schema_version: 1, documents: [], templates: [], pmi: [] };
const copy = <T,>(value: T): T => structuredClone(value);
const unique = (prefix: string) => `${prefix}${Date.now().toString(36)}`;

export default function DrawingsPanel(props: DomainPanelProps) {
  const [data, setData] = useState<DrawingData>({ ...empty, ...props.domainData } as DrawingData);
  const [documentId, setDocumentId] = useState("");
  const [sheetId, setSheetId] = useState("");
  const [viewId, setViewId] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [result, setResult] = useState<Publication | null>(null);
  const [raw, setRaw] = useState("");
  const [note, setNote] = useState("Deburr all edges.");
  const [hole, setHole] = useState("");
  const [dimensionKind, setDimensionKind] = useState("diameter");
  const [tol, setTol] = useState("0.02");
  const [unitPrecision, setUnitPrecision] = useState("2");
  const [annotationX, setAnnotationX] = useState("25");
  const [annotationY, setAnnotationY] = useState("-20");
  const [templateId,setTemplateId]=useState("");
  const [bom,setBom]=useState<{key:string;name:string;quantity:number}[]>([]);
  const [itemKey,setItemKey]=useState("");
  // Polling refreshes the project envelope; preserve the user's unsaved draft
  // until the persisted domain content actually changes.
  const persisted=JSON.stringify(props.domainData);
  useEffect(() => { setData({ ...empty, ...JSON.parse(persisted) } as DrawingData); }, [persisted]);
  const doc = data.documents.find(d => d.id === documentId) ?? data.documents[0];
  const sheet = doc?.sheets.find(s => s.id === sheetId) ?? doc?.sheets[0];
  const view = sheet?.views.find(v => v.id === viewId) ?? sheet?.views[0];
  const stale = doc && doc.source_geometry_key !== props.state.geometry_key;
  const canonical=!!(props.project as unknown as {native_document?:unknown}).native_document;
  const holes = canonical ? [] : props.project.features.filter(f => f.type === "hole" && !f.suppressed);
  function edit(mutate: (value: DrawingData) => void) { const next = copy(data); mutate(next); setData(next); setResult(null); setStatus("Unsaved drawing changes"); }
  function changeDoc(values: Partial<DrawingDocument>) { edit(next => Object.assign(next.documents.find(d => d.id === doc?.id)!, values)); }
  function changeSheet(values: Partial<Sheet>) { edit(next => Object.assign(next.documents.find(d => d.id === doc?.id)!.sheets.find(s => s.id === sheet?.id)!, values)); }
  function changeView(values: Partial<DrawingView>) { changeSheet({ views: sheet.views.map(v => v.id === view.id ? { ...v, ...values } : v) }); }
  async function act(action: () => Promise<void>) {
    setBusy(true);
    try { await action(); } catch (error) { props.setError(error instanceof Error ? error.message : String(error)); }
    finally { setBusy(false); }
  }
  async function save() { await props.saveDomain("drawings", data as unknown as JsonObject); setStatus("Drawing intent saved"); }
  async function run(operation: string) {
    // Save is revision checked by the same shared domain transaction as CLI edits.
    await save();
    const reply = await props.runDomain("drawings", operation, { document: doc.id });
    setResult(reply as Publication); setStatus(operation === "publish" ? "Controlled drawing published" : "Exact drawing preview ready");
  }
  function addDocument() {
    const id = unique("drawing");
    const template=(data.templates as DrawingTemplate[]).find(t => t.id===templateId);
    edit(next => next.documents.push({ id, title: props.project.name, number: "", standard: template?.standard ?? "ISO", projection: template?.projection ?? "first", units: template?.units ?? "mm", source_revision: props.project.revision, source_geometry_key: props.state.geometry_key,
      ...(template ? {template:template.id,title_block:copy(template.title_block)} : {}),
      sheets: template ? [copy(template.sheet)] : [{ id: "sheet1", name: "Sheet 1", size: "A4", views: [{ id: "front", kind: "base", orientation: "front", position: [110, 95], scale: 1, hidden: true }], annotations: [], tables: [] }] }));
    setDocumentId(id);setSheetId("sheet1");setViewId("front");
  }
  function addView(kind: string) {
    const id = unique("view");
    const item: DrawingView = { id, kind, orientation: kind === "projected" ? "top" : (view?.orientation ?? "front"), position: [view.position[0] + (kind === "projected" ? 0 : 70), view.position[1] + (kind === "projected" ? 60 : 0)], scale: kind === "detail" ? 2 : view.scale, hidden: true };
    if (kind !== "base") item.parent = view.id;
    if (kind === "section") { item.section_origin = [0, 0, 0]; item.section_normal = [0, 1, 0]; item.label = "A-A"; }
    if (kind === "detail") { item.detail_center = [0, 0]; item.detail_radius = 10; }
    changeSheet({ views: [...sheet.views, item] });setViewId(id);
  }
  function addAnnotation(kind: string) {
    const target = hole || holes[0]?.id;
    const references: Ref[] = kind === "note" ? [] : target ? [{ kind: "hole", target }] : props.selection ? [{ kind: "face", target: props.selection.id }] : [];
    const a: Annotation = { id: unique("annotation"), view: view.id, kind, references, position: [Number(annotationX), Number(annotationY)], text: note, precision: Number(unitPrecision) };
    if (kind === "linear") { if (!target) throw new Error("Choose a hole pattern for the pitch dimension"); a.references = [{kind:"hole",target},{kind:"hole",target,column:1}]; }
    if (kind === "datum") a.datum = "A";
    if (kind === "gdt") { a.characteristic = "position"; a.tolerance_mm = Number(tol); a.datums = sheet.annotations.some(item => item.kind === "datum") ? ["A"] : []; a.material_condition = "RFS"; }
    if (kind === "balloon") {a.item=itemKey || bom[0]?.key;if (!a.item)throw new Error("Load the associative BOM and choose an item first");}
    if (["diameter", "linear"].includes(kind)) a.tolerance = { kind: "symmetric", upper_mm: Number(tol), lower_mm: -Number(tol) };
    if (kind !== "note" && !references.length) throw new Error("Choose a hole feature or select a face to attach the annotation");
    changeSheet({ annotations: [...sheet.annotations, a] });
  }
  const published = result?.documents?.[0];
  const preview = published?.artifacts?.find(a => a.sheet === sheet?.id) ?? published?.artifacts?.[0];

  return <section className="drawings-panel" aria-label="Drawing workspace">
    <div className="drawing-toolbar"><strong>Drawings</strong>{data.templates.length>0 && <SelectControl aria-label="Drawing template" value={templateId} onChange={e => setTemplateId(e.target.value)}><option value="">Blank template</option>{(data.templates as DrawingTemplate[]).map(t => <option key={t.id} value={t.id}>{t.name}</option>)}</SelectControl>}<Button onClick={addDocument} disabled={busy}>New document</Button><span className={stale ? "drawing-stale" : "drawing-status"}>{stale ? "Source changed · refresh required" : status}</span></div>
    {!doc ? <p className="drawing-empty">Create a controlled drawing from the current exact model. Sheets retain source revision, view relationships and attached annotations.</p> : <>
      <div className="drawing-toolbar">
        <SelectControl aria-label="Drawing document" value={doc.id} onChange={e => setDocumentId(e.target.value)}>{data.documents.map(d => <option value={d.id} key={d.id}>{d.title}</option>)}</SelectControl>
        <Button disabled={busy} onClick={() => act(save)}>Save</Button>
        <Button disabled={busy} onClick={() => act(async () => { await save(); const reply = await props.runDomain("drawings", "refresh", {document:doc.id}); if (reply.data) { await props.saveDomain("drawings", reply.data as JsonObject); setData(reply.data as unknown as DrawingData); } setStatus(JSON.stringify(reply.documents)); })}>Refresh associations</Button>
        <Button disabled={busy || !!stale} onClick={() => act(() => run("preview"))}>Preview</Button>
        <Button variant="primary" disabled={busy || !!stale} onClick={() => act(() => run("publish"))}>Publish PDF / DXF</Button>
      </div>
      <div className="drawing-fields">
        <label>Title<Input value={doc.title} onChange={e => changeDoc({title:e.target.value})}/></label>
        <label>Drawing number<Input value={doc.number} onChange={e => changeDoc({number:e.target.value})}/></label>
        <label>Standard<SelectControl value={doc.standard} onChange={e => changeDoc({standard:e.target.value})}><option>ISO</option><option>ASME</option></SelectControl></label>
        <label>Units<SelectControl value={doc.units} onChange={e => changeDoc({units:e.target.value})}><option value="mm">Millimetres</option><option value="inch">Inches</option></SelectControl></label>
        <label>Projection<SelectControl value={doc.projection} onChange={e => changeDoc({projection:e.target.value})}><option value="first">First angle</option><option value="third">Third angle</option></SelectControl></label>
      </div>
      <div className="drawing-toolbar"><SelectControl aria-label="Sheet" value={sheet.id} onChange={e => setSheetId(e.target.value)}>{doc.sheets.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</SelectControl>
        <Button onClick={() => { const s: Sheet = {id:unique("sheet"),name:`Sheet ${doc.sheets.length+1}`,size:"A4",views:[{id:"front",kind:"base",orientation:"front",position:[100,90],scale:1,hidden:true}],annotations:[],tables:[]};changeDoc({sheets:[...doc.sheets,s]});setSheetId(s.id); }}>Add sheet</Button>
        <SelectControl aria-label="Paper size" value={sheet.size} onChange={e => changeSheet({size:e.target.value})}>{["A4","A3","A2","Letter","B"].map(s => <option key={s}>{s}</option>)}</SelectControl>
        <Button onClick={() => edit(next => next.templates.push({id:unique("template"),name:doc.title,standard:doc.standard,projection:doc.projection,units:doc.units,sheet:copy(sheet),title_block:doc.title_block ?? {}}))}>Save as template</Button>
      </div>
      <div className="drawing-fields">
        <label>View<SelectControl value={view?.id ?? ""} onChange={e => setViewId(e.target.value)}>{sheet.views.map(v => <option key={v.id} value={v.id}>{v.id} · {v.kind}</option>)}</SelectControl></label>
        {view && <><label>Orientation<SelectControl aria-label="Orientation" value={view.orientation} onChange={e => changeView({orientation:e.target.value})}>{["front","back","top","bottom","right","left","isometric"].map(v => <option key={v}>{v}</option>)}</SelectControl></label>
        <label>X mm<Input type="number" value={view.position[0]} onChange={e => changeView({position:[Number(e.target.value),view.position[1]]})}/></label>
        <label>Y mm<Input type="number" value={view.position[1]} onChange={e => changeView({position:[view.position[0],Number(e.target.value)]})}/></label>
        <label>Scale<Input type="number" min="0.001" step="0.1" value={view.scale} onChange={e => changeView({scale:Number(e.target.value)})}/></label>
        <label className="drawing-checkbox"><input type="checkbox" checked={view.hidden} onChange={e => changeView({hidden:e.target.checked})}/> Hidden edges</label></>}
      </div>
      {view && <div className="drawing-toolbar"><Button onClick={() => addView("projected")}>Projected view</Button><Button onClick={() => addView("section")}>Section view</Button><Button onClick={() => addView("detail")}>Detail view</Button></div>}
      {view?.kind==="section" && <div className="drawing-fields"><label>Cut axis<SelectControl value={(view.section_normal as number[] ?? [0,1,0]).findIndex(n => n!==0)} onChange={e => changeView({section_normal:[0,1,2].map(i => i===Number(e.target.value) ? 1 : 0)})}><option value="0">X</option><option value="1">Y</option><option value="2">Z</option></SelectControl></label>{["X","Y","Z"].map((axis,i) => <label key={axis}>Cut {axis} mm<Input type="number" value={(view.section_origin as number[] ?? [0,0,0])[i]} onChange={e => changeView({section_origin:(view.section_origin as number[] ?? [0,0,0]).map((v,index) => index===i ? Number(e.target.value) : v)})}/></label>)}</div>}
      {view?.kind==="detail" && <div className="drawing-fields">{["X","Y"].map((axis,i) => <label key={axis}>Detail centre {axis} mm<Input type="number" value={(view.detail_center as number[] ?? [0,0])[i]} onChange={e => changeView({detail_center:(view.detail_center as number[] ?? [0,0]).map((v,index) => index===i ? Number(e.target.value) : v)})}/></label>)}<label>Detail radius mm<Input type="number" min="0.01" value={Number(view.detail_radius ?? 10)} onChange={e => changeView({detail_radius:Number(e.target.value)})}/></label></div>}
      {view && <details className="drawing-details"><summary>Dimensions and manufacturing annotations</summary>
        <div className="drawing-fields"><label>Hole attachment<SelectControl value={hole || holes[0]?.id || ""} onChange={e => setHole(e.target.value)}>{holes.map(h => <option key={h.id} value={h.id}>{h.name || h.id}</option>)}</SelectControl></label>
          <label>Annotation<SelectControl aria-label="Annotation" value={dimensionKind} onChange={e => setDimensionKind(e.target.value)}>{["diameter","radius","linear","hole_note","thread_note","datum","gdt","center","note","surface","weld","edge","balloon"].map(k => <option key={k}>{k}</option>)}</SelectControl></label>
          <label>Tolerance mm<Input type="number" step="0.001" min="0" value={tol} onChange={e => setTol(e.target.value)}/></label>
          <label>Decimals<Input type="number" min="0" max="6" value={unitPrecision} onChange={e => setUnitPrecision(e.target.value)}/></label>
          <label>Label X mm<Input type="number" value={annotationX} onChange={e => setAnnotationX(e.target.value)}/></label><label>Label Y mm<Input type="number" value={annotationY} onChange={e => setAnnotationY(e.target.value)}/></label>
          <label className="drawing-wide">Note / explicit thread designation<Input value={note} onChange={e => setNote(e.target.value)}/></label>
        </div><Button onClick={() => {try {addAnnotation(dimensionKind);} catch(e) {props.setError(String(e));}}}>Add attached annotation</Button>
        {canonical && <p>Select a current exact face to attach annotations. Legacy feature dimensions require explicit repair after native editing.</p>}
        <ul className="drawing-annotations">{sheet.annotations.map(a => <li key={a.id}><span>{a.kind} · {a.references.map(r => r.target || "point").join(", ") || a.text}</span><Button onClick={() => changeSheet({annotations:sheet.annotations.filter(item => item.id !== a.id)})}>Remove</Button></li>)}</ul>
        <div className="drawing-toolbar"><Button onClick={() => changeSheet({tables:[...sheet.tables,{id:unique("parts"),kind:"parts",position:[10,20]}]})}>Parts list</Button><Button onClick={() => changeSheet({tables:[...sheet.tables,{id:unique("revisions"),kind:"revision",position:[10,45]}]})}>Revision table</Button>
        <Button onClick={() => act(async () => {await save();const reply=await props.runDomain("drawings","inspect",{document:doc.id});setBom((reply.bom ?? []) as {key:string;name:string;quantity:number}[]);setStatus("Associative parts loaded");})}>Load BOM items</Button>
        {bom.length>0 && <SelectControl aria-label="Balloon item" value={itemKey || bom[0].key} onChange={e => setItemKey(e.target.value)}>{bom.map(item => <option value={item.key} key={item.key}>{item.name} × {item.quantity}</option>)}</SelectControl>}
        <Button onClick={() => changeDoc({revisions:[...((doc.revisions as unknown[]) ?? []),{code:String.fromCharCode(65+((doc.revisions as unknown[])?.length ?? 0)),date:new Date().toISOString().slice(0,10),description:note,author:""}]})}>Record revision</Button></div>
      </details>}
      {published && <div className="drawing-toolbar">{published.pdf && <a href={artifact(published.pdf)} target="_blank" rel="noreferrer">Open PDF</a>}{published.artifact && <a href={artifact(published.artifact)}>Drawing package</a>}{preview && <a href={artifact(preview.dxf)}>Sheet DXF</a>}</div>}
      {preview && <object className="drawing-preview" type="image/svg+xml" data={artifact(preview.svg)} aria-label="Exact drawing preview"/>}
    </>}
    <details className="drawing-details"><summary>Drawing / MBD document editor</summary><p>Advanced sheet, template, tolerance, symbol, balloon and 3D annotation fields use the versioned document schema.</p>
      <Button onClick={() => setRaw(JSON.stringify(data,null,2))}>Load current document</Button>
      <textarea aria-label="Drawing document JSON" value={raw} onChange={e => setRaw(e.target.value)} spellCheck={false}/>
      <Button disabled={busy} onClick={() => act(async () => { const next = JSON.parse(raw) as JsonObject; await props.saveDomain("drawings",next); setData(next as unknown as DrawingData); setStatus("Document validated and saved"); })}>Validate and save JSON</Button>
      <Button disabled={busy} onClick={() => act(async () => { await save(); const reply = await props.runDomain("drawings","export_mbd",{});setResult(reply);setStatus("Semantic 3D annotations exported"); })}>Export 3D annotations</Button>
      {typeof result?.artifact === "string" && <a href={artifact(result.artifact)}>Download annotations</a>}
    </details>
    <p className="drawing-provenance">{doc ? `Exact source revision ${doc.source_revision}. ` : ""}PDF, SVG and DXF contain vector geometry and searchable text. AP242 PMI inventories retain source entities; automatic semantic mapping and DWG publication are unavailable.</p>
  </section>;
}
