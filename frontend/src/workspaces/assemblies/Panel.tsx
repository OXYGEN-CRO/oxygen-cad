import { useEffect, useState } from "react";
import { Button, Input, SelectControl } from "../../components/ui";
import type { DomainPanelProps, JsonObject } from "../contracts";
import { operations } from "./operations";
import "./assemblies.css";

type Occurrence = { id: number; component: number; parent: number; name: string; grounded: boolean; transform: number[][] };
type Component = { id: number; name: string; parent: number | null; bodies: string[]; occurrences: Occurrence[] };
type JointValue = { name: string; value: number; unit: string; limits?: [number, number] | null };
type Joint = { id: number; name: string; type: string; suppressed: boolean; values: JointValue[] };
type Summary = { revision?: number; native_key?: string; components?: { components?: Component[]; active?: number; pending_moves?: number[] }; joints?: { joints?: Joint[]; dof?: unknown; conflicts?: unknown[]; errors?: unknown[]; floating?: unknown[] }; motion?: { studies?: { id: number; name: string; steps: number }[] }; explode?: { views?: { id: number; name: string }[] }; config?: { rows?: { name: string }[]; active?: string } };
type Report = JsonObject & { result?: JsonObject; native_key?: string; status?: string; artifact?: string; report_artifact?: string };
const jointTypes = ["rigid", "revolute", "slider", "cylindrical", "pin_slot", "planar", "ball"];
const parseVector = (text: string) => {
  const values = text.split(/[,\s]+/).filter(Boolean).map(Number);
  if (values.length !== 3 || values.some(v => !Number.isFinite(v))) throw new Error("Enter three finite coordinates in mm");
  return values;
};
const artifactUrl = (path: string) => `/api/artifacts/${path.split("/").map(encodeURIComponent).join("/")}`;
const json = (value: unknown) => JSON.stringify(value, null, 2);

export default function AssembliesPanel(props: DomainPanelProps) {
  const [summary, setSummary] = useState<Summary>({});
  const [tab, setTab] = useState("Components");
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<number[]>([]);
  const [jointId, setJointId] = useState<number>(Number(props.domainData.selected_joint || 0));
  const [name, setName] = useState("Component");
  const [vector, setVector] = useState("0, 0, 10");
  const [pending, setPending] = useState(false);
  const [kind, setKind] = useState("revolute");
  const [asBuilt, setAsBuilt] = useState(true);
  const [originA, setOriginA] = useState("0, 0, 0");
  const [originB, setOriginB] = useState("0, 0, 0");
  const [values, setValues] = useState<number[]>([]);
  const [minimum, setMinimum] = useState("-90");
  const [maximum, setMaximum] = useState("90");
  const [valueIndex, setValueIndex] = useState(0);
  const [studyName, setStudyName] = useState("Cycle");
  const [study, setStudy] = useState("");
  const [step, setStep] = useState(0);
  const [travelFrom, setTravelFrom] = useState("0");
  const [travelTo, setTravelTo] = useState("90");
  const [partNumber, setPartNumber] = useState("");
  const [partRevision, setPartRevision] = useState("");
  const [description, setDescription] = useState("");
  const [report, setReport] = useState<Report | null>(null);
  const [advanced, setAdvanced] = useState(0);
  const [parameters, setParameters] = useState(json(operations[0].params));
  const [stack, setStack] = useState(json({ name: "Hole / shaft diametral clearance", dimensions: [{ name: "Hole", nominal: 10, lower: 0, upper: 0.015, sign: 1 }, { name: "Shaft", nominal: 10, lower: -0.014, upper: -0.005, sign: -1 }], required_min: 0.005, required_max: 0.03, assumptions: "Explicit example deviations in mm; verify applicable manufacturing standard." }));
  const components = summary.components?.components || [];
  const occurrences = components.flatMap(c => c.occurrences || []);
  const occurrence = occurrences.find(o => o.id === selected[0]);
  const component = components.find(c => c.id === occurrence?.component);
  const joints = summary.joints?.joints || [];
  const joint = joints.find(j => j.id === jointId);
  const studies = summary.motion?.studies || [];
  const selectedStudy = studies.find(s => String(s.id) === study || s.name === study);
  const poses = (props.domainData.poses || []) as { name: string }[];
  const stale = report?.native_key && summary.native_key && report.native_key !== summary.native_key;

  async function inspect() {
    const next = await props.runDomain("assemblies", "inspect");
    setSummary(next as Summary);
  }
  useEffect(() => {
    let cancelled = false;
    props.runDomain("assemblies", "inspect").then(next => { if (!cancelled) setSummary(next as Summary); }).catch(error => { if (!cancelled) props.setError(String(error)); });
    return () => { cancelled = true; };
  }, [props.project.revision]);
  useEffect(() => { setValues(joint?.values.map(v => v.value) || []); }, [jointId, json(joint?.values)]);

  async function work(action: () => Promise<unknown>) {
    setBusy(true);
    try { await action(); await props.refresh(); await inspect(); }
    catch (error) { props.setError(error instanceof Error ? error.message : String(error)); }
    finally { setBusy(false); }
  }
  async function native(command: string, params: JsonObject = {}) {
    const reply = await props.native(command, params);
    if (reply.result && typeof reply.result === "object") setReport({ result: reply.result as JsonObject });
    return reply;
  }
  async function query(command: string, params: JsonObject = {}, save = false) {
    const next = await props.runDomain("assemblies", save ? "report" : "query", { command, params });
    setReport(next as Report);
    return next;
  }
  async function settings(patch: JsonObject) { await props.saveDomain("assemblies", { schema_version: 1, ...props.domainData, ...patch }); }
  function selectionControl() {
    return <div className="assembly-tree" aria-label="Assembly occurrences">{components.filter(c => c.id !== 0).map(c => <div className="assembly-definition" key={c.id}>
      <span className="assembly-subtle">{c.parent ? `↳ ${components.find(p => p.id === c.parent)?.name || c.parent} / ` : ""}{c.name} · definition {c.id}</span>
      {(c.occurrences || []).map(o => <label className="assembly-occurrence" key={o.id}>
        <input type="checkbox" checked={selected.includes(o.id)} onChange={e => setSelected(e.target.checked ? [...selected, o.id] : selected.filter(id => id !== o.id))} />
        <span>{o.name}<small>Occurrence {o.id}{o.grounded ? " · grounded" : ""}</small></span>
      </label>)}
    </div>)}{occurrences.length === 0 && <p className="assembly-subtle">Create a component, then author its geometry in the active definition.</p>}</div>;
  }
  function jointControl() {
    return <label>Joint<SelectControl aria-label="Joint" value={jointId} onChange={e => setJointId(Number(e.target.value))}><option value={0}>Select joint</option>{joints.map(j => <option key={j.id} value={j.id}>{j.name} · {j.type}</option>)}</SelectControl></label>;
  }

  return <section className="assembly-panel" aria-label="Assemblies workspace" aria-busy={busy}>
    <div className="assembly-heading"><div><h2>Assembly</h2><span className="assembly-subtle">{occurrences.length} occurrences · {joints.length} joints</span></div><Button disabled={busy} onClick={() => void work(inspect)}>Refresh</Button></div>
    <nav className="assembly-tabs" aria-label="Assembly tools">{["Components", "Joints", "Motion", "Fit", "BOM", "Advanced"].map(t => <Button aria-pressed={tab === t} key={t} onClick={() => setTab(t)}>{t}</Button>)}</nav>
    <fieldset disabled={busy}>
      {(["Components", "Joints", "Motion", "BOM"].includes(tab)) && selectionControl()}
      {tab === "Components" && <>
        <label>Name<Input value={name} onChange={e => setName(e.target.value)} /></label>
        <div className="assembly-actions"><Button variant="primary" onClick={() => void work(() => native("component.create", { name, parent: occurrence?.component || 0, activate: true }))}>New {occurrence ? "child component" : "component"}</Button><Button onClick={() => void work(() => native("component.activate", { component: 0 }))}>Activate root</Button></div>
        <p className="assembly-subtle">Active: {components.find(c => c.id === summary.components?.active)?.name || "Root"}. New modeling features belong to this definition.</p>
        {occurrence && <>
          <div className="assembly-actions"><Button onClick={() => void work(() => native("component.activate", { component: occurrence.component, occurrence: occurrence.id }))}>Edit definition</Button><Button onClick={() => void work(() => native("occurrence.copy", { occurrence: occurrence.id, component: occurrence.component, translate: parseVector(vector) }))}>Instance</Button><Button onClick={() => void work(() => native("component.paste_new", { component: occurrence.component, occurrence: occurrence.id, translate: parseVector(vector) }))}>Independent copy</Button></div>
          <label>Translation / instance offset (mm)<Input value={vector} onChange={e => setVector(e.target.value)} /></label>
          <label className="assembly-inline"><input type="checkbox" checked={pending} onChange={e => setPending(e.target.checked)} />Keep move pending</label>
          <div className="assembly-actions"><Button onClick={() => void work(() => native("occurrence.move", { occurrence: occurrence.id, translate: parseVector(vector), capture: !pending }))}>Move in parent</Button><Button onClick={() => void work(() => native("occurrence.ground", { occurrence: occurrence.id, grounded: !occurrence.grounded }))}>{occurrence.grounded ? "Unground" : "Ground to parent"}</Button></div>
          <details><summary>Placement matrix</summary><pre>{json(occurrence.transform)}</pre></details>
        </>}
        <div className="assembly-actions"><Button onClick={() => void work(() => native("component.capture_position"))}>Capture positions</Button><Button onClick={() => void work(() => native("component.revert_position"))}>Revert pending</Button></div>
        <p className="assembly-subtle">{summary.components?.pending_moves?.length || 0} pending placements. Nested definitions remain shared. Grounding is relative to the parent; world pinning requires a root relation.</p>
      </>}
      {tab === "Joints" && <>
        <label>Joint type<SelectControl value={kind} onChange={e => setKind(e.target.value)}>{jointTypes.map(type => <option key={type}>{type}</option>)}</SelectControl></label>
        <label className="assembly-inline"><input type="checkbox" checked={asBuilt} onChange={e => setAsBuilt(e.target.checked)} />As-built · preserve current placements</label>
        {!asBuilt && <><label>Origin A · name or point (mm)<Input value={originA} onChange={e => setOriginA(e.target.value)} /></label><label>Origin B · name or point (mm)<Input value={originB} onChange={e => setOriginB(e.target.value)} /></label></>}
        <Button variant="primary" disabled={selected.length !== 2} onClick={() => void work(() => {
          const origin = (value: string, occurrenceId: number) => /,|\s/.test(value.trim()) ? { occurrence: occurrenceId, point: parseVector(value) } : value;
          return native(asBuilt ? "joint.as_built" : "joint.create", { type: kind, a: asBuilt ? selected[0] : origin(originA, selected[0]), b: asBuilt ? selected[1] : origin(originB, selected[1]), name: `${kind} ${joints.length + 1}` });
        })}>Join selected pair</Button>
        <p className="assembly-subtle">Select two occurrences in order. Ground an anchor to drive the mechanism. Persistent face/circle/edge origins are available in Advanced.</p>
        {jointControl()}
        {joint && <>
          {joint.values.map((v, i) => <label key={i}>{v.name} ({v.unit})<Input type="number" step="any" value={values[i] ?? v.value} onChange={e => setValues(values.map((x, index) => index === i ? Number(e.target.value) : x))} /><small>{v.limits ? `Limits ${v.limits.join(" … ")}` : "Unlimited"}</small></label>)}
          <div className="assembly-actions"><Button disabled={!joint.values.length} onClick={() => void work(() => native("joint.drive", { joint: joint.id, values }))}>Drive</Button><Button onClick={() => void work(() => native("joint.edit", { joint: joint.id, suppressed: !joint.suppressed }))}>{joint.suppressed ? "Restore joint" : "Suppress joint"}</Button><Button onClick={() => void work(() => query("joint.inspect", { joint: joint.id }))}>Inspect references</Button></div>
          {joint.values.length > 0 && <><label>Limit coordinate<SelectControl value={valueIndex} onChange={e => setValueIndex(Number(e.target.value))}>{joint.values.map((v, i) => <option key={i} value={i}>{v.name} ({v.unit})</option>)}</SelectControl></label><div className="assembly-grid"><label>Minimum<Input type="number" value={minimum} onChange={e => setMinimum(e.target.value)} /></label><label>Maximum<Input type="number" value={maximum} onChange={e => setMaximum(e.target.value)} /></label></div><div className="assembly-actions"><Button onClick={() => void work(() => native("joint.limits", { joint: joint.id, index: valueIndex, min: Number(minimum), max: Number(maximum) }))}>Set limits</Button><Button onClick={() => void work(() => native("joint.limits", { joint: joint.id, index: valueIndex }))}>Clear limits</Button><Button onClick={() => void work(async () => { await props.runDomain("assemblies", (props.domainData.locks as JsonObject | undefined)?.[String(joint.id)] ? "joint.unlock" : "joint.lock", { joint: joint.id }); })}>{(props.domainData.locks as JsonObject | undefined)?.[String(joint.id)] ? "Unlock" : "Lock current pose"}</Button></div></>}
        </>}
        <div className="assembly-actions"><Button onClick={() => void work(() => native("joint.solve"))}>Solve / diagnose</Button><Button onClick={() => void work(() => props.runDomain("assemblies", "pose.capture", { name: "Home" }))}>Save home pose</Button>{poses.map(p => <Button key={p.name} onClick={() => void work(() => props.runDomain("assemblies", "pose.restore", { name: p.name }))}>Restore {p.name}</Button>)}</div>
        {(summary.joints?.floating?.length || summary.joints?.conflicts?.length || summary.joints?.errors?.length) ? <details open><summary>Mechanism diagnostics</summary><pre>{json({ floating: summary.joints?.floating, conflicts: summary.joints?.conflicts, errors: summary.joints?.errors, dof: summary.joints?.dof })}</pre></details> : <details><summary>Degrees of freedom</summary><pre>{json(summary.joints?.dof)}</pre></details>}
      </>}
      {tab === "Motion" && <>
        {jointControl()}
        <div className="assembly-actions"><Button disabled={selected.length < 2} onClick={() => void work(() => native("contact.create", { name: "Contact set", occurrences: selected }))}>Contact set from selection</Button><Button onClick={() => void work(() => native("contact.enable_all"))}>All contact</Button><Button onClick={() => void work(() => native("contact.disable_all"))}>Disable contact</Button><Button onClick={() => void work(() => query("contact.list"))}>Inspect sets</Button></div>
        <label>Study name<Input value={studyName} onChange={e => setStudyName(e.target.value)} /></label>
        <div className="assembly-grid"><label>From<Input type="number" value={travelFrom} onChange={e => setTravelFrom(e.target.value)} /></label><label>To<Input type="number" value={travelTo} onChange={e => setTravelTo(e.target.value)} /></label></div>
        <Button disabled={!joint?.values.length} onClick={() => void work(() => native("motion.study", { name: studyName, steps: 100, keys: [{ joint: jointId, index: valueIndex, points: [[0, Number(travelFrom)], [100, Number(travelTo)]] }] }))}>Create keyed study</Button>
        <label>Study<SelectControl value={study} onChange={e => setStudy(e.target.value)}><option value="">Select study</option>{studies.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</SelectControl></label>
        {selectedStudy && <><label>Step {step}<input aria-label="Motion step" type="range" min={0} max={selectedStudy.steps} value={step} onChange={e => setStep(Number(e.target.value))} /></label><Button onClick={() => void work(() => native("motion.play", { study: selectedStudy.id, step }))}>Apply pose</Button><div className="assembly-grid"><label>Samples<Input type="number" min={3} max={500} value={Number(props.domainData.samples || 21)} onChange={e => void work(() => settings({ samples: Number(e.target.value) }))} /></label><label>Duration (s)<Input type="number" min={0.001} value={Number(props.domainData.duration_s || 1)} onChange={e => void work(() => settings({ duration_s: Number(e.target.value) }))} /></label></div>
          <div className="assembly-actions"><Button onClick={() => void work(() => query("motion.plot", { study: selectedStudy.id, samples: props.domainData.samples || 21, duration: props.domainData.duration_s || 1 }, true))}>Kinematics report</Button><Button onClick={() => void work(() => query("motion.interference", { study: selectedStudy.id, samples: props.domainData.samples || 21 }, true))}>Sampled interference</Button><Button onClick={() => void work(async () => setReport(await props.runDomain("assemblies", "export_motion", { params: { study: selectedStudy.id, samples: props.domainData.samples || 21 } }) as Report))}>Export poses</Button></div>
        </>}
        <p className="assembly-subtle">Kinematics reports positions, finite-difference velocities and accelerations. Sampled interference can miss collisions between samples. Enabled contact sets use swept collision during drive and study playback. No force, friction or impact solution is claimed.</p>
        <div className="assembly-actions"><Button onClick={() => void work(() => native("explode.create", { name: "Exploded", scale: 1 }))}>Create exploded view</Button><Button onClick={() => void work(() => native("explode.show", { view: "Exploded" }))}>Show exploded</Button><Button onClick={() => void work(() => native("explode.show", { view: "none" }))}>Assembled display</Button></div>
      </>}
      {tab === "Fit" && <>
        <label>Minimum clearance (mm)<Input type="number" min={0} step="any" value={Number(props.domainData.clearance_mm ?? 0.1)} onChange={e => void work(() => settings({ clearance_mm: Number(e.target.value) }))} /></label>
        <div className="assembly-actions"><Button variant="primary" onClick={() => void work(() => query("inspect.clearance", { clearance: props.domainData.clearance_mm ?? 0.1, nearest: 1, skip_within: null }, true))}>All-pairs clearance</Button><Button onClick={() => void work(() => query("inspect.interference", { budget_s: props.domainData.interference_budget_s || 30 }, true))}>Exact interference</Button></div>
        <p className="assembly-subtle">All native bodies are checked in their engineering pose. Unchecked pairs and exhausted budgets remain unresolved. Exploded display offsets do not change the fit model.</p>
        <label>Worst-case dimensional stack<textarea aria-label="Dimensional stack" rows={12} value={stack} onChange={e => setStack(e.target.value)} /></label>
        <div className="assembly-actions"><Button onClick={() => void work(async () => setReport(await props.runDomain("assemblies", "fit_stack", { stack: JSON.parse(stack) }) as Report))}>Evaluate stack</Button><Button onClick={() => void work(async () => { const next = JSON.parse(stack); const stacks = (props.domainData.stacks || []) as JsonObject[]; await settings({ stacks: [...stacks.filter(s => s.name !== next.name), next] }); })}>Save stack</Button></div>
        {((props.domainData.stacks || []) as JsonObject[]).map(s => <Button key={String(s.name)} onClick={() => setStack(json(s))}>{String(s.name)}</Button>)}
        <p className="assembly-subtle">Enter signed dimensions for hole/shaft clearance or bolt length minus grip, washers and nut. Explicit deviations and acceptance limits are required; this arithmetic does not infer an ISO fit class or establish thread strength.</p>
      </>}
      {tab === "BOM" && <>
        {component && <><p>Definition: {component.name}</p><label>Part number<Input value={partNumber} onChange={e => setPartNumber(e.target.value)} /></label><label>Revision<Input value={partRevision} onChange={e => setPartRevision(e.target.value)} /></label><label>Description<Input value={description} onChange={e => setDescription(e.target.value)} /></label><Button onClick={() => void work(() => native("component.metadata.set", { component: component.id, part_number: partNumber, revision: partRevision, description }))}>Save part properties</Button></>}
        <div className="assembly-grid"><label>Levels<SelectControl value={String(props.domainData.bom_levels || "flat")} onChange={e => void work(() => settings({ bom_levels: e.target.value }))}><option value="flat">All levels</option><option value="top">Top level</option></SelectControl></label><label>Group by<SelectControl value={String(props.domainData.bom_group_by || "part_number")} onChange={e => void work(() => settings({ bom_group_by: e.target.value }))}><option value="part_number">Part number</option><option value="definition">Definition</option></SelectControl></label></div>
        <label className="assembly-inline"><input type="checkbox" checked={Boolean(props.domainData.bom_group_children)} onChange={e => void work(() => settings({ bom_group_children: e.target.checked }))} />Keep kits and children together</label>
        <div className="assembly-actions"><Button variant="primary" onClick={() => void work(() => query("assembly.bom", { levels: props.domainData.bom_levels || "flat", group_by: [props.domainData.bom_group_by || "part_number"], group_children: Boolean(props.domainData.bom_group_children) }))}>Reconcile BOM</Button><Button onClick={() => void work(async () => setReport(await props.runDomain("assemblies", "export_bom", { format: "csv", params: { levels: props.domainData.bom_levels || "flat", group_by: [props.domainData.bom_group_by || "part_number"], group_children: Boolean(props.domainData.bom_group_children) } }) as Report))}>Export CSV</Button></div>
        <label>Configuration<SelectControl value={summary.config?.active || ""} onChange={e => void work(() => native("config.activate", { row: e.target.value }))}><option value="">Select configuration</option>{summary.config?.rows?.map(row => <option key={row.name}>{row.name}</option>)}</SelectControl></label>
        <p className="assembly-subtle">Counts follow native shared occurrences and the active configuration. Numbered BOM items are generated for each report; drawing balloon associations require drawing metadata.</p>
      </>}
      {tab === "Advanced" && <>
        <label>Operation<SelectControl value={advanced} onChange={e => { const index = Number(e.target.value); setAdvanced(index); setParameters(json(operations[index].params)); }}>{operations.map((op, i) => <option key={op.label} value={i}>{op.label}</option>)}</SelectControl></label>
        <p className="assembly-subtle">{operations[advanced].note}</p>
        <label>Native parameters<textarea aria-label="Native parameters" rows={12} value={parameters} onChange={e => setParameters(e.target.value)} /></label>
        <Button variant="primary" onClick={() => void work(() => operations[advanced].read ? query(operations[advanced].command, JSON.parse(parameters)) : native(operations[advanced].command, JSON.parse(parameters)))}>Apply {operations[advanced].label.toLowerCase()}</Button>
        <p className="assembly-subtle">Use stable occurrence IDs from Components and joint IDs from Inspect. The native engine validates geometry references and returns unresolved attachments explicitly.</p>
      </>}
    </fieldset>
    {report && <section className="assembly-report" aria-label="Assembly result"><div className="assembly-heading"><strong>{stale ? "Stale result · model changed" : report.status || "Native result"}</strong><Button onClick={() => setReport(null)}>Dismiss</Button></div>
      {report.artifact && <a href={artifactUrl(report.artifact)} download>Download artifact</a>}{report.report_artifact && <a href={artifactUrl(report.report_artifact)} download>Download report</a>}
      {Array.isArray(report.result?.rows) && <table><thead><tr><th>Item</th><th>Part</th><th>Rev</th><th>Qty</th></tr></thead><tbody>{(report.result.rows as JsonObject[]).map(row => <tr key={String(row.item)}><td>{String(row.item)}</td><td>{String(row.part_number || row.definition || "")}</td><td>{String(row.revision || "")}</td><td>{String(row.count)}</td></tr>)}</tbody></table>}
      <details open={!Array.isArray(report.result?.rows)}><summary>Details</summary><pre>{json(report.result || report)}</pre></details>
    </section>}
  </section>;
}
