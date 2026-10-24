import { useState } from "react";
import { Button, Input, SelectControl } from "../../components/ui";
import type { DomainPanelProps, JsonObject } from "../contracts";
import "./interop.css";

type Tab = "exchange" | "surfaces" | "meshes" | "construction" | "form";
type Tool = { command: string; label: string; params: JsonObject; hint: string };
const surfaceTools: Tool[] = [
  { command: "surface.patch", label: "Patch sketch region", params: { sketch: "Outline", body_name: "Sheet" }, hint: "Create a planar patch from a closed sketch. Body-edge boundaries also support G0/G1/G2 constraints." },
  { command: "surface.extrude", label: "Extrude surface", params: { sketch: "Outline", distance: "10 mm", body_name: "Walls" }, hint: "Extrude sketch curves into open walls with no end caps." },
  { command: "surface.revolve", label: "Revolve surface", params: { sketch: "Outline", axis: "Y", angle: "360 deg", body_name: "Revolved" }, hint: "Revolve a curve chain about a world, sketch or construction axis." },
  { command: "surface.sweep", label: "Sweep surface", params: { sketch: "Outline", path_sketch: "Path", path: ["l1"], body_name: "Swept" }, hint: "Sweep an open or closed chain; the native engine validates the path and orientation." },
  { command: "surface.loft", label: "Loft surface", params: { sections: [{ sketch: "Section A" }, { sketch: "Section B" }], body_name: "Lofted" }, hint: "Loft two or more section chains. Advanced end continuity and guide rails are native parameters." },
  { command: "surface.offset", label: "Offset faces", params: { body: "Body", faces: [[5, 5, 10]], distance: "1 mm" }, hint: "Pick faces by point. Analytic planes/cylinders offset exactly; other surfaces may be fitted." },
  { command: "surface.ruled", label: "Ruled surface", params: { body: "Body", edges: [[5, 0, 10]], distance: "5 mm", mode: "tangent" }, hint: "Create a strip from a boundary chain with normal, tangent, angle or direction control." },
  { command: "surface.blend", label: "Blend boundaries", params: { from: { body: "First", edges: [[5, 0, 0]] }, to: { body: "Second", edges: [[5, 10, 5]] }, continuity: ["g1", "g1"] }, hint: "Connect two boundary chains. The engine diagnoses invalid continuity or references." },
  { command: "surface.trim", label: "Trim surface", params: { body: "Sheet", plane: { origin: [5, 0, 0], normal: [1, 0, 0] }, keep: [8, 5, 0] }, hint: "Keep the region containing the supplied point. Supports planes, faces, sketch curves and mutual trim." },
  { command: "surface.stitch", label: "Stitch surfaces", params: { bodies: ["Walls", "Bottom", "Top"], tolerance: "0.01 mm" }, hint: "Sew surfaces into the first body. A closed shell becomes a solid; gaps remain explicit." },
  { command: "surface.thicken", label: "Thicken surface", params: { bodies: ["Sheet"], thickness: "2 mm", operation: "new", body_name: "Thickened" }, hint: "Turn a surface into a solid. Negative thickness follows the opposite normal." },
  { command: "surface.extend", label: "Extend boundary", params: { body: "Sheet", edges: [[10, 5, 0]], distance: "5 mm", mode: "natural" }, hint: "Extend a boundary naturally, tangentially or with curvature continuity." },
  { command: "surface.untrim", label: "Untrim face", params: { face: { body: "Sheet", point: [5, 5, 0] }, holes: "all", outer: true, in_place: true }, hint: "Restore a face's natural surface bounds; source face identity is retained." },
  { command: "surface.unstitch", label: "Unstitch body", params: { body: "Body" }, hint: "Separate faces into editable surface bodies." },
  { command: "surface.reverse_normal", label: "Reverse normals", params: { body: "Sheet" }, hint: "Reverse surface face normals before stitching or thickening." },
  { command: "surface.fair", label: "Fair B-spline surface", params: { body: "Sheet", tolerance: "0.1 mm", iterations: 3, keep_edges: true }, hint: "Smooth interior B-spline control vertices within the supplied movement tolerance." },
];
const constructionTools: Tool[] = [
  { command: "construct.plane.offset", label: "Offset construction plane", params: { base: "XY", offset: "10 mm", name: "Offset plane" }, hint: "A named, parametric plane that downstream sketches can reference." },
  { command: "construct.plane.at_angle", label: "Construction plane at angle", params: { base: "XY", axis: "X", angle: "30 deg", name: "Angled plane" }, hint: "Rotate a reference plane about a world or named construction axis." },
  { command: "construct.plane.midplane", label: "Midplane", params: { a: { plane: "XY" }, b: { plane: "Offset plane" }, name: "Midplane" }, hint: "Reference two planes or planar faces. Native persistent references survive document reload." },
  { command: "construct.plane.three_points", label: "Plane through three points", params: { a: { point: [0, 0, 0] }, b: { point: [10, 0, 0] }, c: { point: [0, 10, 0] }, name: "Three point plane" }, hint: "Collinear or coincident references are rejected." },
  { command: "construct.plane.tangent", label: "Tangent plane", params: { face: { face: [10, 0, 5] }, name: "Tangent plane" }, hint: "Use a point on a cylindrical, conical, spherical or toroidal face." },
  { command: "construct.plane.along_path", label: "Plane along path", params: { path: { sketch: "Path", curve: "l1" }, t: 0.5, name: "Path plane" }, hint: "Create a plane normal to a curve at a fractional path position." },
  { command: "construct.plane.two_edges", label: "Plane through two edges", params: { a: { edge: [5, 0, 0] }, b: { edge: [0, 5, 0] }, name: "Edge plane" }, hint: "The edge references must define a common plane." },
  { command: "construct.plane.perpendicular", label: "Perpendicular plane", params: { plane: { plane: "XY" }, line: { axis: "X" }, name: "Perpendicular plane" }, hint: "A plane holding the referenced line and perpendicular to the reference plane." },
  { command: "construct.axis.through_cylinder", label: "Axis through cylinder", params: { face: { face: [10, 0, 5] }, name: "Cylinder axis" }, hint: "Reference a cylindrical/conical/toroidal face or circular edge." },
  { command: "construct.axis.two_points", label: "Axis through two points", params: { a: { point: [0, 0, 0] }, b: { point: [0, 0, 10] }, name: "Two point axis" }, hint: "The axis retains its defining references." },
  { command: "construct.axis.two_planes", label: "Axis through two planes", params: { a: { plane: "XY" }, b: { plane: "YZ" }, name: "Intersection axis" }, hint: "Reference two intersecting planes or planar faces." },
  { command: "construct.axis.normal_to_face", label: "Axis normal to face", params: { face: { face: [5, 5, 10] }, point: { point: [5, 5, 10] }, name: "Normal axis" }, hint: "Use a point and a face reference to define the perpendicular axis." },
  { command: "construct.axis.edge", label: "Axis along edge", params: { edge: { edge: [5, 0, 0] }, name: "Edge axis" }, hint: "The selected edge must be straight." },
  { command: "construct.point.vertex", label: "Point at vertex", params: { point: { vertex: [0, 0, 0] }, name: "Vertex point" }, hint: "Create a named point tied to a vertex." },
  { command: "construct.point.three_planes", label: "Point through three planes", params: { a: { plane: "XY" }, b: { plane: "YZ" }, c: { plane: "XZ" }, name: "Origin point" }, hint: "The point is the common intersection of the three referenced planes." },
  { command: "construct.point.two_edges", label: "Point through two edges", params: { a: { edge: [5, 0, 0] }, b: { edge: [0, 5, 0] }, name: "Intersection point" }, hint: "Create a point where the two edge references meet or come closest." },
  { command: "construct.point.center", label: "Point at center", params: { of: { edge: [10, 0, 0] }, name: "Center point" }, hint: "Use a circular edge, sketch circle/arc, sphere or torus reference." },
  { command: "construct.point.edge_plane", label: "Point at edge and plane", params: { edge: { edge: [0, 0, 5] }, plane: { plane: "XY" }, name: "Edge plane point" }, hint: "Find the intersection of a referenced line and plane." },
  { command: "construct.point.along_path", label: "Point along path", params: { path: { sketch: "Path", curve: "l1" }, t: 0.5, name: "Path point" }, hint: "A named point at a fractional curve position." },
];

function NativeToolForm({ tool, busy, onApply, onError }: { tool: Tool; busy: boolean; onApply: (params: JsonObject) => void; onError: (message: string) => void }) {
  const [fields, setFields] = useState<Record<string, string>>(() => Object.fromEntries(Object.entries(tool.params).map(([key, value]) => [key, typeof value === "object" ? JSON.stringify(value) : String(value)])));
  const [additional, setAdditional] = useState("{}");
  const submit = () => {
    try {
      const payload: JsonObject = {};
      for (const [key, original] of Object.entries(tool.params)) {
        const value = fields[key];
        payload[key] = typeof original === "object" ? JSON.parse(value) : typeof original === "boolean" ? value === "true" : typeof original === "number" && Number.isFinite(Number(value)) ? Number(value) : value;
      }
      const extra: unknown = JSON.parse(additional);
      if (!extra || Array.isArray(extra) || typeof extra !== "object") throw new Error("Additional parameters must be a JSON object");
      onApply({ ...payload, ...extra });
    } catch (error) { onError(String(error)); }
  };
  return <div className="interop-native-fields">
    {Object.entries(tool.params).map(([key, original]) => <label key={key}>{key.replaceAll("_", " ")}
      {typeof original === "boolean" ? <SelectControl aria-label={key} value={fields[key]} onChange={e => setFields({ ...fields, [key]: e.target.value })}><option value="true">Yes</option><option value="false">No</option></SelectControl>
        : typeof original === "object" ? <textarea aria-label={key} rows={2} value={fields[key]} onChange={e => setFields({ ...fields, [key]: e.target.value })} spellCheck={false} />
        : <Input aria-label={key} value={fields[key]} onChange={e => setFields({ ...fields, [key]: e.target.value })} />}
    </label>)}
    <details><summary>Additional native parameters</summary><textarea aria-label="Additional native parameters" rows={4} value={additional} onChange={e => setAdditional(e.target.value)} spellCheck={false} /></details>
    <Button disabled={busy} onClick={submit}>Apply {tool.label.toLowerCase()}</Button>
  </div>;
}

export default function InteropPanel(props: DomainPanelProps) {
  const [tab, setTab] = useState<Tab>("exchange");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<JsonObject | null>(null);
  const [units, setUnits] = useState("file");
  const [assetId, setAssetId] = useState("");
  const [format, setFormat] = useState("step");
  const [sketch, setSketch] = useState("");
  const [selected, setSelected] = useState(0);
  const [meshBody, setMeshBody] = useState("");
  const [cellSize, setCellSize] = useState("1");
  const [tolerance, setTolerance] = useState("0.0001");
  const [translation, setTranslation] = useState("0, 0, 0");
  const [scale, setScale] = useState("1");
  const [planeOrigin, setPlaneOrigin] = useState("0, 0, 0");
  const [planeNormal, setPlaneNormal] = useState("0, 0, 1");
  const assets = (props.domainData.assets ?? []) as Array<{ id: string; name: string; format: string; source_units: string; source_asset?: string; diagnostics?: JsonObject }>;
  const chosen = assets.find(a => a.id === assetId);
  const run = async (operation: string, input: JsonObject = {}) => {
    setBusy(true);
    try { const value = await props.runDomain("interop", operation, input); setResult(value.result && typeof value.result === "object" ? value.result as JsonObject : value); await props.refresh(); }
    catch (error) { props.setError(String(error)); }
    finally { setBusy(false); }
  };
  const native = async (command: string, input: JsonObject) => {
    setBusy(true);
    try { const value = await props.native(command, input); setResult({ result: value.result }); }
    catch (error) { props.setError(String(error)); }
    finally { setBusy(false); }
  };
  const selectTab = (next: Tab) => {
    setTab(next); setSelected(0);
  };
  const upload = async (file: File) => {
    if (file.size > 50 * 1024 * 1024) { props.setError("Choose a file of 50 MiB or less"); return; }
    const bytes = new Uint8Array(await file.arrayBuffer());
    let binary = "";
    for (let offset = 0; offset < bytes.length; offset += 8192) binary += String.fromCharCode(...bytes.subarray(offset, offset + 8192));
    await run("import", { name: file.name, content_base64: btoa(binary), units });
  };
  const tools = tab === "construction" ? constructionTools : surfaceTools;
  const selectedTool = tools[selected] ?? tools[0];


  return <section className="interop-panel" aria-label="Exchange, surfaces and meshes">
    <div className="interop-tabs" role="tablist" aria-label="Model preparation">
      {([ ["exchange", "Exchange"], ["surfaces", "Surfaces"], ["meshes", "Meshes"], ["construction", "Construction"], ["form", "Form"] ] as Array<[Tab, string]>).map(([id, label]) =>
        <Button role="tab" aria-selected={tab === id} key={id} onClick={() => selectTab(id)}>{label}</Button>)}
    </div>
    {tab === "exchange" && <>
      <h3>Import editable geometry</h3>
      <p>STEP becomes native B-rep bodies. STL and OBJ become mesh bodies. DXF and SVG become editable sketch curves. Source files remain immutable.</p>
      <label>Source units<SelectControl aria-label="Source units" value={units} onChange={e => setUnits(e.target.value)}>
        <option value="file">Declared file units (STEP / DXF / SVG)</option><option value="mm">Millimetres</option><option value="cm">Centimetres</option><option value="m">Metres</option><option value="in">Inches</option><option value="ft">Feet</option>
      </SelectControl></label>
      <label>Import file<Input aria-label="Import exchange file" type="file" accept=".step,.stp,.stl,.obj,.dxf,.svg" disabled={busy} onChange={e => { const file = e.target.files?.[0]; if (file) void upload(file); e.target.value = ""; }} /></label>
      <h3>Export current native document</h3>
      <label>Format<SelectControl aria-label="Export format" value={format} onChange={e => setFormat(e.target.value)}>
        <option value="step">STEP</option><option value="stl">STL</option><option value="obj">OBJ</option><option value="dxf">Sketch DXF</option><option value="json">Native JSON</option>
      </SelectControl></label>
      {format === "dxf" && <label>Sketch name or ID<Input value={sketch} onChange={e => setSketch(e.target.value)} placeholder="Outline" /></label>}
      <Button disabled={busy} onClick={() => void run("export", { format, ...(sketch && format === "dxf" ? { sketch } : {}) })}>Export document</Button>
    </>}
    {(tab === "surfaces" || tab === "construction") && <>
      <h3>{tab === "surfaces" ? "Surface modeling" : "Parametric construction references"}</h3>
      <label>Tool<SelectControl aria-label="Native preparation tool" value={selected} onChange={e => setSelected(Number(e.target.value))}>
        {tools.map((tool, index) => <option key={tool.command} value={index}>{tool.label}</option>)}
      </SelectControl></label>
      <p>{selectedTool.hint}</p>
      <NativeToolForm key={selectedTool.command} tool={selectedTool} busy={busy} onApply={input => void native(selectedTool.command, input)} onError={props.setError} />
      {tab === "surfaces" && <p className="interop-limitation">Surface fillet is unavailable: the pinned upstream handler is planned and does not produce geometry. Solid edge fillets remain in Modeling.</p>}
    </>}
    {tab === "meshes" && <>
      <h3>Mesh preparation</h3>
      <p>Analyze immutable STL / OBJ sources, then insert a derived editable mesh. The source and the current native bodies remain individually traceable.</p>
      <label>Source mesh<SelectControl aria-label="Source mesh" value={assetId} onChange={e => setAssetId(e.target.value)}><option value="">Choose a source</option>{assets.filter(a => ["stl", "obj"].includes(a.format)).map(a => <option key={a.id} value={a.id}>{a.name}</option>)}</SelectControl></label>
      <div className="interop-actions">
        <Button disabled={busy || !chosen} onClick={() => void run("mesh.analyze", { asset_id: assetId })}>Analyze topology</Button>
        <Button disabled={busy || !chosen} onClick={() => void run("mesh.repair", { asset_id: assetId, fill_small_holes: true })}>Repair small holes and normals</Button>
        <Button disabled={busy || !chosen} onClick={() => void run("mesh.subdivide", { asset_id: assetId })}>Subdivide triangles</Button>
        <Button disabled={busy || !chosen} onClick={() => void run("mesh.smooth", { asset_id: assetId, strength: 0.2, iterations: 3 })}>Smooth mesh (3 passes)</Button>
      </div>
      <label>Scale<Input type="number" min="0.000001" step="any" value={scale} onChange={e => setScale(e.target.value)} /></label>
      <label>Translation XYZ (mm)<Input value={translation} onChange={e => setTranslation(e.target.value)} /></label>
      <Button disabled={busy || !chosen} onClick={() => void run("mesh.transform", { asset_id: assetId, scale: Number(scale), translate: translation.split(",").map(Number) })}>Insert transformed mesh</Button>
      <label>Clustering cell size (mm)<Input type="number" min="0.000001" step="any" value={cellSize} onChange={e => setCellSize(e.target.value)} /></label>
      <Button disabled={busy || !chosen} onClick={() => void run("mesh.cluster", { asset_id: assetId, cell_size_mm: Number(cellSize) })}>Reduce with vertex clustering</Button>
      <p className="interop-limitation">Clustering can change topology; inspect the measured result. Repair fills triangle and quad holes only. General remeshing and self-intersection repair are unavailable.</p>
      <label>Plane origin XYZ (mm)<Input value={planeOrigin} onChange={e => setPlaneOrigin(e.target.value)} /></label>
      <label>Plane normal XYZ<Input value={planeNormal} onChange={e => setPlaneNormal(e.target.value)} /></label>
      <div className="interop-actions">
        <Button disabled={busy || !chosen} onClick={() => void run("mesh.section", { asset_id: assetId, origin: planeOrigin.split(",").map(Number), normal: planeNormal.split(",").map(Number) })}>Export plane section as DXF</Button>
        <Button disabled={busy || !chosen} onClick={() => void run("mesh.cut", { asset_id: assetId, origin: planeOrigin.split(",").map(Number), normal: planeNormal.split(",").map(Number) })}>Cut mesh (open boundary)</Button>
      </div>
      <h3>Reconstruct mechanical mesh</h3>
      <label>Native mesh body name<Input value={meshBody} onChange={e => setMeshBody(e.target.value)} placeholder="Imported mesh body" /></label>
      <label>Reconstruction tolerance (mm)<Input type="number" min="0.000001" step="any" value={tolerance} onChange={e => setTolerance(e.target.value)} /></label>
      <Button disabled={busy || !meshBody} onClick={() => void native("mesh.convert", { body: meshBody, tolerance: `${tolerance} mm` })}>Convert to editable B-rep</Button>
      <p className="interop-limitation">Reconstruction supports closed constant-section mechanical meshes. Arbitrary scans and organic forms require another reconstruction engine.</p>
    </>}
    {tab === "form" && <>
      <h3>Form editor unavailable</h3>
      <p>The pinned upstream includes T-spline mathematics, but no persisted control-cage editor or general T-spline-to-B-rep workflow. No Form operation is enabled.</p>
      <p>Use Surface tools for patch, loft, blend and bounded B-spline fairing. These commands preserve their native surface features.</p>
    </>}
    {tab === "exchange" && assets.length > 0 && <>
      <h3>Source provenance</h3>
      <label>Source asset<SelectControl aria-label="Source asset" value={assetId} onChange={e => setAssetId(e.target.value)}><option value="">Choose an asset</option>{assets.map(a => <option key={a.id} value={a.id}>{a.name} · {a.source_units}</option>)}</SelectControl></label>
      {chosen && <p className="interop-provenance">{chosen.id}{chosen.source_asset ? ` · derived from ${chosen.source_asset}` : " · immutable original"}</p>}
      {chosen && ["step", "stp"].includes(chosen.format) && <div className="interop-actions">
        <Button disabled={busy} onClick={() => void run("step.analyze", { asset_id: assetId })}>Inspect B-rep topology</Button>
        <Button disabled={busy} onClick={() => void run("step.heal", { asset_id: assetId, tolerance_mm: 0.0001 })}>Heal and insert a copy</Button>
      </div>}
      {chosen && <Button disabled={busy} onClick={() => void run("insert_asset", { asset_id: assetId })}>Insert source again</Button>}
    </>}
    {busy && <p role="status">Running geometry operation…</p>}
    {result && <div className="interop-result" aria-live="polite">
      {typeof result.artifact === "string" && <a href={`/api/artifacts/${result.artifact}`} download>Download {String(result.format ?? "result").toUpperCase()}</a>}
      <details open><summary>Measured result</summary><pre>{JSON.stringify(result, null, 2)}</pre></details>
    </div>}
  </section>;
}
