import { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity,
  ArrowDownToLine,
  ArrowLeft,
  ArrowRight,
  Box,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Code2,
  Copy,
  Eye,
  EyeOff,
  FileBox,
  Focus,
  History,
  Layers3,
  Loader2,
  Lock,
  Maximize2,
  MousePointer2,
  Play,
  Plus,
  Ruler,
  Save,
  Scissors,
  Settings2,
  ShieldCheck,
  Square,
  Terminal,
  Trash2,
  Unlock,
  Upload,
  X,
  AlertTriangle,
  RotateCcw,
} from "lucide-react";
import {
  api,
  artifact,
  post,
  type Feature,
  type Project,
  type Selection,
  type State,
} from "./api";
import { Viewport } from "./Viewport";

const clone = <T,>(value: T): T => structuredClone(value);
const number = (value: unknown) =>
  typeof value === "number" ? Number(value.toFixed(4)) : value;
const scalar = (value: string) =>
  value.startsWith("$") ? value : Number(value);
const pretty = (value: unknown) => JSON.stringify(value, null, 2);

function Field({ label, value, onChange, type = "text", unit, step }: any) {
  return (
    <label className="field">
      <span>{label}</span>
      <div>
        <input
          aria-label={label}
          type={type}
          step={step ?? "any"}
          value={value ?? ""}
          onChange={(e) =>
            onChange(
              type === "number" ? Number(e.target.value) : e.target.value,
            )
          }
        />
        {unit && <small>{unit}</small>}
      </div>
    </label>
  );
}
function VectorField({
  label,
  value,
  onChange,
  unit = "mm",
}: {
  label: string;
  value: number[];
  onChange: (v: number[]) => void;
  unit?: string;
}) {
  return (
    <div className="vector-field">
      <label>
        {label}
        <small>{unit}</small>
      </label>
      <div>
        {value.map((v, i) => (
          <label key={i}>
            <span>{"XYZ"[i]}</span>
            <input
              type="number"
              step="any"
              aria-label={`${label} ${"XYZ"[i]}`}
              value={number(v) as number}
              onChange={(e) =>
                onChange(
                  value.map((n, j) => (j === i ? Number(e.target.value) : n)),
                )
              }
            />
          </label>
        ))}
      </div>
    </div>
  );
}
function Select({ label, value, options, onChange }: any) {
  return (
    <label className="field">
      <span>{label}</span>
      <select
        aria-label={label}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        {options.map((v: any) =>
          typeof v === "string" ? (
            <option key={v}>{v}</option>
          ) : (
            <option key={v.value} value={v.value}>
              {v.label}
            </option>
          ),
        )}
      </select>
    </label>
  );
}

export default function App() {
  const [state, setState] = useState<State | null>(null),
    [draft, setDraft] = useState<Project | null>(null),
    [dirty, setDirty] = useState(false),
    [tab, setTab] = useState("design"),
    [active, setActive] = useState(""),
    [selection, setSelection] = useState<Selection | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [section, setSection] = useState(false),
    [fit, setFit] = useState(0),
    [measureMode, setMeasureMode] = useState(false),
    [isolate, setIsolate] = useState(false),
    [measure, setMeasure] = useState<number[][]>([]),
    [field, setField] = useState<"solid" | "stress" | "displacement">("solid"),
    [deformation, setDeformation] = useState(0),
    [addOpen, setAddOpen] = useState(false),
    [checks, setChecks] = useState<any>(null),
    [source, setSource] = useState(""),
    [showAgent, setShowAgent] = useState(false),
    [showBottom, setShowBottom] = useState(true);
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  const upload = useRef<HTMLInputElement>(null);
  const previousJobs = useRef("");
  const refresh = useCallback(async () => {
    try {
      const result = await api("state");
      setState((previous) =>
        previous &&
        previous.project.revision === result.project.revision &&
        previous.geometry_state === result.geometry_state &&
        previous.analysis_state === result.analysis_state &&
        JSON.stringify(previous.jobs) === JSON.stringify(result.jobs) &&
        previous.checks?.input_key === result.checks?.input_key
          ? previous
          : result,
      );
      if (!dirtyRef.current) {
        setDraft((previous) =>
          previous?.revision === result.project.revision
            ? previous
            : result.project,
        );
        setSource(pretty(result.project));
      }
      return result;
    } catch (e) {
      setError(String(e));
    }
  }, []);
  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 1600);
    return () => clearInterval(interval);
  }, [refresh]);
  useEffect(() => {
    if (state) setChecks(state.checks);
  }, [state]);
  function edit(fn: (p: Project) => void) {
    dirtyRef.current = true;
    setDraft((p) => {
      if (!p) return p;
      const next = clone(p);
      fn(next);
      return next;
    });
    setDirty(true);
  }
  function patchFeature(values: Record<string, unknown>) {
    edit((p) => {
      const feature = p.features.find((f) => f.id === active);
      if (feature) Object.assign(feature, values);
    });
  }
  async function save(project = draft) {
    if (!project || !state) return;
    setBusy(true);
    setError("");
    try {
      await post("commands", {
        expected_revision: project.revision,
        actor: "human",
        action: "replace",
        project,
        summary: "Manual studio edit",
      });
      dirtyRef.current = false;
      setDirty(false);
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  async function run(kind: string) {
    setBusy(true);
    setError("");
    try {
      if (dirty) throw new Error("Save your edits before running an operation");
      await post(`jobs/${kind}`, {});
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  async function history(action: string, revision?: number) {
    if (!state) return;
    try {
      await post("commands", {
        expected_revision: state.project.revision,
        actor: "human",
        action,
        restore_revision: revision,
        summary: `${action} from studio`,
      });
      dirtyRef.current = false;
      setDirty(false);
      await refresh();
    } catch (e) {
      setError(String(e));
    }
  }
  function select(s: Selection) {
    setSelection(s);
    if (measureMode)
      setMeasure((old) => (old.length === 1 ? [old[0], s.point] : [s.point]));
  }
  useEffect(() => {
    if (!state) return;
    const context = {
      revision: state.project.revision,
      geometry_key: state.geometry_key,
      geometry_state: state.geometry_state,
      active_feature: active || null,
      tab,
      selection: selection
        ? {
            id: selection.id,
            kind: selection.kind,
            reference: selection.reference,
            point: selection.point,
            face: {
              kind: selection.face.kind,
              area_mm2: selection.face.area_mm2,
              center: selection.face.center,
              normal: selection.face.normal,
            },
          }
        : null,
    };
    post("context", context).catch(() => {});
  }, [active, selection, tab, state?.project.revision, state?.geometry_state]);
  function add(type: string) {
    if (!draft) return;
    let n = draft.features.length + 1;
    while (draft.features.some((f) => f.id === `${type}_${n}`)) n++;
    const id = `${type}_${n}`;
    let feature: any = {
      id,
      type,
      name: type[0].toUpperCase() + type.slice(1),
      suppressed: false,
    };
    if (type === "profile")
      Object.assign(feature, {
        shape: "rectangle",
        width: 20,
        height: 20,
        radius: 5,
        points: [],
        plane: "XY",
        origin: [0, 0, 0],
        rotation: [0, 0, 0],
      });
    if (type === "extrude")
      Object.assign(feature, {
        profile: draft.features.find((f) => f.type === "profile")?.id ?? "",
        distance: 4,
        operation: "add",
      });
    if (type === "hole")
      Object.assign(feature, {
        center: selection?.point ?? [0, 0, 0],
        axis: "Z",
        diameter: 3.2,
        through: true,
        depth: 5,
        reverse: true,
        rows: 1,
        columns: 1,
        spacing: [10, 10],
      });
    if (type === "fillet" || type === "chamfer")
      Object.assign(feature, { selection: "part:vertical_edges", size: 1 });
    edit((p) => p.features.push(feature));
    setActive(id);
    setTab("design");
    setAddOpen(false);
  }
  async function importFile(file: File) {
    if (!state) return;
    setBusy(true);
    try {
      const response = await fetch(
        `/api/import?expected_revision=${state.project.revision}`,
        { method: "POST", headers: { "x-filename": file.name }, body: file },
      );
      if (!response.ok) throw new Error((await response.json()).detail);
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  const current = draft;
  const geometry = state?.build ?? state?.last_build;
  const feature = current?.features.find((f) => f.id === active);
  const reference = current?.references.find((r) => r.id === active);
  const pending = state?.jobs.some((j) =>
    ["queued", "running"].includes(j.state),
  );
  const lastExport = state?.jobs.find(
    (j) => j.kind === "export" && j.state === "succeeded",
  );
  const compare = state?.history.length
    ? state.history[state.history.length - 2]
    : null;
  if (!current || !state)
    return (
      <div className="loading-screen">
        <div className="brand-symbol">
          O<span />
        </div>
        <h1>Oxygen CAD</h1>
        <p>{error || "Opening your engineering workspace…"}</p>
        <Loader2 className="spin" />
      </div>
    );
  const fixed = (id: string) =>
    edit((p) => {
      if (!p.study.fixed_faces.includes(id)) p.study.fixed_faces.push(id);
    });
  const force = (id: string) =>
    edit((p) => {
      if (!p.study.forces.some((f) => f.face === id))
        p.study.forces.push({ face: id, vector_n: [0, 0, -10] });
      p.study.assumptions_confirmed = false;
    });
  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <div className="brand-symbol">
            O<span />
          </div>
          <strong>
            oxygen<span>CAD</span>
          </strong>
          <span className="alpha">OPEN SOURCE</span>
        </div>
        <div className="project-title">
          <span className="divider" />
          <FileBox size={16} />
          <input
            aria-label="Project name"
            value={current.name}
            onChange={(e) => edit((p) => (p.name = e.target.value))}
          />
          <span className="revision">r{state.project.revision}</span>
          {dirty && <span className="unsaved">Unsaved edits</span>}
        </div>
        <div className="top-actions">
          <button onClick={() => history("undo")} title="Undo">
            <ArrowLeft size={15} />
          </button>
          <button onClick={() => history("redo")} title="Redo">
            <ArrowRight size={15} />
          </button>
          <button onClick={() => setShowAgent(!showAgent)}>
            <Terminal size={15} />
            Agent tools
          </button>
          <button
            className="primary"
            disabled={!dirty || busy}
            onClick={() => {
              try {
                save(tab === "source" ? JSON.parse(source) : draft);
              } catch (e) {
                setError(String(e));
              }
            }}
          >
            <Save size={15} />
            Save changes
          </button>
        </div>
      </header>
      <nav className="workspace-nav">
        <div className="nav-tabs">
          {[
            ["design", Box, "Design"],
            ["verify", ShieldCheck, "Verify"],
            ["history", History, "History"],
            ["source", Code2, "Source"],
          ].map(([id, Icon, label]: any) => (
            <button
              key={id}
              className={tab === id ? "active" : ""}
              onClick={() => setTab(id)}
            >
              <Icon size={16} />
              {label}
            </button>
          ))}
        </div>
        <span className="local-status">
          <i />
          Local workspace<span>·</span>Millimeters
        </span>
      </nav>
      {error && (
        <div role="alert" className="error-banner">
          <AlertTriangle size={16} />
          <span>{error.replace("Error: ", "")}</span>
          <button onClick={() => setError("")}>
            <X size={16} />
          </button>
        </div>
      )}
      {showAgent && (
        <div className="agent-banner">
          <Terminal size={18} />
          <div>
            <strong>Your agent works on the same project.</strong>
            <p>
              Inspect the model, write a proposal, then apply it with the
              current revision. Locked parameters are preserved.
            </p>
            <code>
              ./o3d inspect --json &nbsp; · &nbsp; ./o3d context --json &nbsp; ·
              &nbsp; ./o3d apply proposal.json --expected-revision{" "}
              {state.project.revision}
            </code>
          </div>
          <button
            onClick={() =>
              navigator.clipboard.writeText(
                `./o3d inspect --json\n./o3d context --json\n./o3d apply proposal.json --expected-revision ${state.project.revision}`,
              )
            }
          >
            <Copy size={16} />
          </button>
        </div>
      )}
      <main className="workspace">
        <aside className="left-panel">
          <div className="panel-heading">
            <span>PROJECT</span>
            <button
              onClick={() => setAddOpen(!addOpen)}
              aria-label="Add feature"
            >
              <Plus size={16} />
            </button>
          </div>
          <div className="tree-heading">
            <ChevronDown size={13} />
            <Layers3 size={14} />
            <span>Features</span>
            <small>{current.features.length}</small>
          </div>
          <div className="feature-list">
            {current.features.map((f, index) => (
              <button
                key={f.id}
                className={`tree-item ${active === f.id ? "selected" : ""} ${f.suppressed ? "suppressed" : ""}`}
                onClick={() => {
                  setActive(f.id);
                  setTab("design");
                }}
              >
                <span className="tree-indent" />
                {f.type === "profile" ? (
                  <Square size={15} />
                ) : f.type === "extrude" ? (
                  <Box size={15} />
                ) : (
                  <Settings2 size={15} />
                )}
                <span>{f.name || f.id}</span>
                <small>{String(index + 1).padStart(2, "0")}</small>
              </button>
            ))}
          </div>
          {addOpen && (
            <div className="add-menu">
              {["profile", "extrude", "hole", "fillet", "chamfer"].map(
                (type) => (
                  <button key={type} onClick={() => add(type)}>
                    <Plus size={14} />
                    Add {type}
                  </button>
                ),
              )}
            </div>
          )}
          <button className="add-feature" onClick={() => setAddOpen(!addOpen)}>
            <Plus size={14} />
            Add feature
          </button>
          <div className="tree-heading references-heading">
            <ChevronDown size={13} />
            <FileBox size={14} />
            <span>Reference parts</span>
            <small>{current.references.length}</small>
          </div>
          {current.references.map((ref) => (
            <div className="reference-row" key={ref.id}>
              <button
                className={`tree-item ${active === ref.id ? "selected" : ""}`}
                onClick={() => {
                  setActive(ref.id);
                  setTab("design");
                }}
              >
                <FileBox size={14} />
                <span>{ref.name}</span>
              </button>
              <button
                title="Toggle reference visibility"
                onClick={() =>
                  edit((p) => {
                    const r = p.references.find((v) => v.id === ref.id)!;
                    r.visible = !r.visible;
                  })
                }
              >
                {ref.visible ? <Eye size={13} /> : <EyeOff size={13} />}
              </button>
            </div>
          ))}
          <button
            className="add-feature"
            disabled={dirty}
            onClick={() => upload.current?.click()}
          >
            <Upload size={14} />
            Import STEP
          </button>
          <input
            ref={upload}
            type="file"
            accept=".step,.stp"
            hidden
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) importFile(file);
              e.target.value = "";
            }}
          />
          <div className="left-footer">
            <div className="mini-label">DESIGN INTENT</div>
            <p>
              Agent-authored.
              <br />
              Human-editable.
            </p>
            <span>One project. Every change traceable.</span>
          </div>
        </aside>
        <section className="canvas-panel">
          <div className="canvas-toolbar">
            <div>
              <button
                className={!measureMode ? "tool-active" : ""}
                title="Select"
                onClick={() => setMeasureMode(false)}
              >
                <MousePointer2 size={17} />
              </button>
              <button
                className={measureMode ? "tool-active" : ""}
                title="Measure between two surface points"
                onClick={() => {
                  setMeasureMode(!measureMode);
                  setMeasure([]);
                }}
              >
                <Ruler size={17} />
              </button>
              <span />
              <button
                className={section ? "tool-active" : ""}
                title="Section view"
                onClick={() => setSection(!section)}
              >
                <Scissors size={17} />
              </button>
              <button
                className={isolate ? "tool-active" : ""}
                title="Isolate manufactured part"
                onClick={() => setIsolate(!isolate)}
              >
                <Eye size={17} />
              </button>
            </div>
            <div>
              <select
                aria-label="Viewport display"
                value={field}
                onChange={(e) => setField(e.target.value as any)}
              >
                <option value="solid">Solid model</option>
                <option value="stress" disabled={!state.analysis}>
                  Stress field
                </option>
                <option value="displacement" disabled={!state.analysis}>
                  Displacement
                </option>
              </select>
              <button title="Fit model" onClick={() => setFit((v) => v + 1)}>
                <Focus size={17} />
              </button>
            </div>
          </div>
          <Viewport
            geometry={geometry ?? null}
            project={current}
            selected={selection}
            onSelect={select}
            section={section}
            field={field}
            analysis={state.analysis}
            fit={fit}
            deformation={deformation}
            measure={measure}
            showStudy={tab === "verify"}
            isolate={isolate}
          />
          {!geometry && (
            <div className="empty-canvas">
              <Box size={42} />
              <h2>Make your first build</h2>
              <p>Your features become exact, editable geometry.</p>
              <button className="primary" onClick={() => run("build")}>
                <Play size={15} />
                Build model
              </button>
            </div>
          )}
          {state.geometry_state === "stale" && (
            <div className="stale-label">
              <AlertTriangle size={14} />
              Showing previous geometry · rebuild required
            </div>
          )}
          <div className="viewport-corner">
            <div className="view-label">ORTHOGRAPHIC / ISOMETRIC</div>
            <span>
              Drag to orbit · Scroll to zoom · Click a face to inspect
            </span>
          </div>
          <div className="model-card">
            <span
              className={`status-dot ${state.geometry_state === "current" ? "good" : ""}`}
            />
            <strong>
              {state.geometry_state === "current"
                ? "Geometry current"
                : state.geometry_state === "stale"
                  ? "Build out of date"
                  : "Not built"}
            </strong>
            {geometry && (
              <small>
                {geometry.dimensions_mm.map((v) => v.toFixed(1)).join(" × ")} mm
              </small>
            )}
          </div>
          {measure.length > 0 && (
            <div className="measure-card">
              <Ruler size={15} />
              {measure.length === 1
                ? "Select a second point"
                : `${Math.hypot(...measure[0].map((v, i) => v - measure[1][i])).toFixed(3)} mm`}
              <button onClick={() => setMeasure([])}>
                <X size={13} />
              </button>
            </div>
          )}
          {field !== "solid" && state.analysis && (
            <div className="field-legend">
              <span>
                {field === "stress"
                  ? "von Mises stress · MPa"
                  : "Displacement · mm"}
              </span>
              <div />
              <small>
                0{" "}
                <b>
                  {(field === "stress"
                    ? state.analysis.max_stress_mpa
                    : state.analysis.max_displacement_mm
                  ).toPrecision(4)}
                </b>
              </small>
              <label>
                Deformation ×{deformation}
                <input
                  aria-label="Deformation scale"
                  type="range"
                  min="0"
                  max="100"
                  value={deformation}
                  onChange={(e) => setDeformation(Number(e.target.value))}
                />
              </label>
            </div>
          )}
          <div className="canvas-actions">
            <button
              className="build-button"
              disabled={busy || dirty || pending}
              onClick={() => run("build")}
            >
              {pending ? (
                <Loader2 size={15} className="spin" />
              ) : (
                <Play size={15} />
              )}
              Build model
            </button>
            <button
              disabled={busy || dirty || pending}
              onClick={() => run("check")}
            >
              <ShieldCheck size={15} />
              Check fit
            </button>
            <button
              disabled={
                busy || dirty || pending || state.geometry_state !== "current"
              }
              onClick={() => run("export")}
            >
              <ArrowDownToLine size={15} />
              Export
            </button>
            {lastExport && (
              <a
                className="download-link"
                href={artifact(lastExport.result.artifact)}
                download
              >
                Download r{lastExport.revision}
              </a>
            )}
          </div>
        </section>
        <aside className="right-panel">
          <div className="panel-heading">
            <span>
              {tab === "verify"
                ? "ENGINEERING STUDY"
                : tab === "history"
                  ? "REVISION HISTORY"
                  : tab === "source"
                    ? "PROJECT SOURCE"
                    : "PROPERTIES"}
            </span>
            <Settings2 size={14} />
          </div>
          <div className="properties">
            {tab === "design" && (
              <>
                {feature ? (
                  <>
                    <div className="inspector-title">
                      <div className="feature-glyph">
                        <Box size={18} />
                      </div>
                      <div>
                        <h2>{feature.name || feature.id}</h2>
                        <span>
                          {feature.type} · {feature.id}
                        </span>
                      </div>
                    </div>
                    <Field
                      label="Feature name"
                      value={feature.name}
                      onChange={(v: string) => patchFeature({ name: v })}
                    />
                    <label className="checkbox">
                      <input
                        type="checkbox"
                        checked={feature.suppressed}
                        onChange={(e) =>
                          patchFeature({ suppressed: e.target.checked })
                        }
                      />
                      Suppress feature
                    </label>
                    {feature.type === "profile" && (
                      <>
                        <Select
                          label="Profile shape"
                          value={feature.shape}
                          options={["rectangle", "circle", "polygon"]}
                          onChange={(v: string) =>
                            patchFeature({
                              shape: v,
                              ...(v === "polygon" && !feature.points.length
                                ? {
                                    points: [
                                      [-10, -10],
                                      [10, -10],
                                      [0, 10],
                                    ],
                                  }
                                : {}),
                            })
                          }
                        />
                        {feature.shape === "rectangle" && (
                          <>
                            <Field
                              label="Width"
                              value={feature.width}
                              unit="mm"
                              onChange={(v: string) =>
                                patchFeature({ width: scalar(v) })
                              }
                            />
                            <Field
                              label="Height"
                              value={feature.height}
                              unit="mm"
                              onChange={(v: string) =>
                                patchFeature({ height: scalar(v) })
                              }
                            />
                          </>
                        )}
                        {feature.shape === "circle" && (
                          <Field
                            label="Radius"
                            value={feature.radius}
                            unit="mm"
                            onChange={(v: string) =>
                              patchFeature({ radius: scalar(v) })
                            }
                          />
                        )}{" "}
                        {feature.shape === "polygon" && (
                          <label className="source-field">
                            Polygon vertices (JSON)
                            <textarea
                              aria-label="Polygon vertices"
                              defaultValue={pretty(feature.points)}
                              key={feature.id}
                              onBlur={(e) => {
                                try {
                                  patchFeature({
                                    points: JSON.parse(e.target.value),
                                  });
                                } catch {
                                  setError(
                                    "Polygon vertices must be a JSON array",
                                  );
                                }
                              }}
                            />
                          </label>
                        )}
                        <Select
                          label="Construction plane"
                          value={feature.plane}
                          options={["XY", "XZ", "YZ"]}
                          onChange={(v: string) => patchFeature({ plane: v })}
                        />
                        <VectorField
                          label="Origin"
                          value={feature.origin}
                          onChange={(v) => patchFeature({ origin: v })}
                        />
                        <VectorField
                          label="Rotation"
                          unit="deg"
                          value={feature.rotation}
                          onChange={(v) => patchFeature({ rotation: v })}
                        />
                      </>
                    )}
                    {feature.type === "extrude" && (
                      <>
                        <Select
                          label="Profile"
                          value={feature.profile}
                          options={current.features
                            .filter((f) => f.type === "profile")
                            .map((f) => ({
                              value: f.id,
                              label: f.name || f.id,
                            }))}
                          onChange={(v: string) => patchFeature({ profile: v })}
                        />
                        <Field
                          label="Extrusion distance"
                          value={feature.distance}
                          unit="mm"
                          onChange={(v: string) =>
                            patchFeature({ distance: scalar(v) })
                          }
                        />
                        <Select
                          label="Operation"
                          value={feature.operation}
                          options={["add", "cut"]}
                          onChange={(v: string) =>
                            patchFeature({ operation: v })
                          }
                        />
                      </>
                    )}
                    {feature.type === "hole" && (
                      <>
                        <Field
                          label="Hole diameter"
                          value={feature.diameter}
                          unit="mm"
                          onChange={(v: string) =>
                            patchFeature({ diameter: scalar(v) })
                          }
                        />
                        <VectorField
                          label="Hole center"
                          value={feature.center}
                          onChange={(v) => patchFeature({ center: v })}
                        />
                        <Select
                          label="Hole axis"
                          value={feature.axis}
                          options={["X", "Y", "Z"]}
                          onChange={(v: string) => patchFeature({ axis: v })}
                        />
                        <label className="checkbox">
                          <input
                            type="checkbox"
                            checked={feature.through}
                            onChange={(e) =>
                              patchFeature({ through: e.target.checked })
                            }
                          />
                          Through all
                        </label>
                        {!feature.through && (
                          <>
                            <Field
                              label="Hole depth"
                              value={feature.depth}
                              unit="mm"
                              onChange={(v: string) =>
                                patchFeature({ depth: scalar(v) })
                              }
                            />
                            <label className="checkbox">
                              <input
                                type="checkbox"
                                checked={feature.reverse}
                                onChange={(e) =>
                                  patchFeature({ reverse: e.target.checked })
                                }
                              />
                              Drill in negative axis direction
                            </label>
                          </>
                        )}
                        <div className="two-fields">
                          <Field
                            label="Columns"
                            type="number"
                            value={feature.columns}
                            onChange={(v: number) =>
                              patchFeature({ columns: v })
                            }
                          />
                          <Field
                            label="Rows"
                            type="number"
                            value={feature.rows}
                            onChange={(v: number) => patchFeature({ rows: v })}
                          />
                        </div>
                        <div className="two-fields">
                          {feature.spacing.map((v: number, i: number) => (
                            <Field
                              key={i}
                              label={`${i ? "Row" : "Column"} spacing`}
                              type="number"
                              value={v}
                              unit="mm"
                              onChange={(n: number) =>
                                patchFeature({
                                  spacing: feature.spacing.map(
                                    (x: number, j: number) => (j === i ? n : x),
                                  ),
                                })
                              }
                            />
                          ))}
                        </div>
                      </>
                    )}
                    {["fillet", "chamfer"].includes(feature.type) && (
                      <>
                        <Field
                          label="Edge selection"
                          value={feature.selection}
                          onChange={(v: string) =>
                            patchFeature({ selection: v })
                          }
                        />
                        <Select
                          label="Semantic edge group"
                          value={feature.selection}
                          options={[
                            "part:vertical_edges",
                            "part:top_edges",
                            "part:bottom_edges",
                            "part:all_edges",
                          ]}
                          onChange={(v: string) =>
                            patchFeature({ selection: v })
                          }
                        />
                        <Field
                          label="Edge size"
                          value={feature.size}
                          unit="mm"
                          onChange={(v: string) =>
                            patchFeature({ size: scalar(v) })
                          }
                        />
                      </>
                    )}
                    <div className="feature-order">
                      <button
                        disabled={current.features[0].id === feature.id}
                        onClick={() =>
                          edit((p) => {
                            const i = p.features.findIndex(
                              (f) => f.id === feature.id,
                            );
                            [p.features[i - 1], p.features[i]] = [
                              p.features[i],
                              p.features[i - 1],
                            ];
                          })
                        }
                      >
                        Move earlier
                      </button>
                      <button
                        disabled={current.features.at(-1)?.id === feature.id}
                        onClick={() =>
                          edit((p) => {
                            const i = p.features.findIndex(
                              (f) => f.id === feature.id,
                            );
                            [p.features[i], p.features[i + 1]] = [
                              p.features[i + 1],
                              p.features[i],
                            ];
                          })
                        }
                      >
                        Move later
                      </button>
                    </div>
                    <button
                      className="danger-link"
                      onClick={() => {
                        edit(
                          (p) =>
                            (p.features = p.features.filter(
                              (f) => f.id !== feature.id,
                            )),
                        );
                        setActive("");
                      }}
                    >
                      <Trash2 size={14} />
                      Delete feature
                    </button>
                  </>
                ) : reference ? (
                  <>
                    <h2>{reference.name}</h2>
                    <p className="muted">
                      Immutable STEP reference. Placement remains editable.
                    </p>
                    <VectorField
                      label="Reference translation"
                      value={reference.placement.translation}
                      onChange={(v) =>
                        edit(
                          (p) =>
                            (p.references.find(
                              (r) => r.id === active,
                            )!.placement.translation = v),
                        )
                      }
                    />
                    <VectorField
                      label="Reference rotation"
                      unit="deg"
                      value={reference.placement.rotation}
                      onChange={(v) =>
                        edit(
                          (p) =>
                            (p.references.find(
                              (r) => r.id === active,
                            )!.placement.rotation = v),
                        )
                      }
                    />
                    <label className="checkbox">
                      <input
                        type="checkbox"
                        checked={reference.check_interference}
                        onChange={(e) =>
                          edit(
                            (p) =>
                              (p.references.find(
                                (r) => r.id === active,
                              )!.check_interference = e.target.checked),
                          )
                        }
                      />
                      Check interference
                    </label>
                    <button
                      className="danger-link"
                      onClick={() => {
                        edit(
                          (p) =>
                            (p.references = p.references.filter(
                              (r) => r.id !== active,
                            )),
                        );
                        setActive("");
                      }}
                    >
                      <Trash2 size={14} />
                      Remove reference
                    </button>
                  </>
                ) : (
                  <>
                    <div className="inspector-title">
                      <div className="feature-glyph">
                        <Settings2 size={18} />
                      </div>
                      <div>
                        <h2>Project parameters</h2>
                        <span>Your shared design controls</span>
                      </div>
                    </div>
                    <p className="muted">
                      Use <code>$name</code> in a feature dimension to link it
                      to a parameter.
                    </p>
                  </>
                )}
                <div className="section-heading">
                  PARAMETERS
                  <button
                    title="Add parameter"
                    onClick={() => {
                      const name = prompt(
                        "Parameter name (letters, numbers, underscores)",
                      );
                      if (name)
                        edit(
                          (p) =>
                            (p.parameters[name] = {
                              value: 10,
                              unit: "mm",
                              locked: false,
                            }),
                        );
                    }}
                  >
                    <Plus size={14} />
                  </button>
                </div>
                {Object.entries(current.parameters).map(([name, p]) => (
                  <div className="parameter" key={name}>
                    <Field
                      label={name}
                      type="number"
                      value={p.value}
                      unit={p.unit}
                      onChange={(v: number) =>
                        edit((d) => (d.parameters[name].value = v))
                      }
                    />
                    <button
                      aria-label={`${p.locked ? "Unlock" : "Lock"} ${name}`}
                      className={p.locked ? "locked" : ""}
                      onClick={() =>
                        edit((d) => (d.parameters[name].locked = !p.locked))
                      }
                    >
                      {p.locked ? <Lock size={14} /> : <Unlock size={14} />}
                    </button>
                    <button
                      title={`Delete ${name}`}
                      onClick={() =>
                        edit((d) => {
                          delete d.parameters[name];
                        })
                      }
                    >
                      <X size={12} />
                    </button>
                  </div>
                ))}
                <details>
                  <summary>Part placement</summary>
                  <VectorField
                    label="Part translation"
                    value={current.placement.translation}
                    onChange={(v) => edit((p) => (p.placement.translation = v))}
                  />
                  <VectorField
                    label="Part rotation"
                    value={current.placement.rotation}
                    unit="deg"
                    onChange={(v) => edit((p) => (p.placement.rotation = v))}
                  />
                </details>
                <div className="section-heading">
                  REQUIREMENTS
                  <button
                    title="Add requirement"
                    onClick={() =>
                      edit((p) =>
                        p.requirements.push({
                          id: `limit_${p.requirements.length + 1}`,
                          kind: "max_dimension",
                          axis: "X",
                          value: 100,
                          enabled: true,
                        }),
                      )
                    }
                  >
                    <Plus size={14} />
                  </button>
                </div>
                {current.requirements.map((req, i) => (
                  <div className="requirement-card" key={req.id}>
                    <div>
                      <strong>{req.id}</strong>
                      <button
                        title="Delete requirement"
                        onClick={() => edit((p) => p.requirements.splice(i, 1))}
                      >
                        <X size={13} />
                      </button>
                    </div>
                    <Select
                      label="Requirement type"
                      value={req.kind}
                      options={[
                        "max_dimension",
                        "max_mass",
                        "min_clearance",
                        "max_displacement",
                      ]}
                      onChange={(v: string) =>
                        edit((p) => (p.requirements[i].kind = v))
                      }
                    />
                    <Field
                      label="Requirement limit"
                      type="number"
                      value={req.value}
                      onChange={(v: number) =>
                        edit((p) => (p.requirements[i].value = v))
                      }
                    />
                    {req.kind === "max_dimension" && (
                      <Select
                        label="Dimension axis"
                        value={req.axis}
                        options={["X", "Y", "Z"]}
                        onChange={(v: string) =>
                          edit((p) => (p.requirements[i].axis = v))
                        }
                      />
                    )}{" "}
                    {req.kind === "min_clearance" && (
                      <Select
                        label="Clearance reference"
                        value={req.reference ?? ""}
                        options={[
                          { value: "", label: "Select component" },
                          ...current.references.map((r) => ({
                            value: r.id,
                            label: r.name,
                          })),
                        ]}
                        onChange={(v: string) =>
                          edit((p) => (p.requirements[i].reference = v))
                        }
                      />
                    )}
                    <label className="checkbox">
                      <input
                        type="checkbox"
                        checked={req.enabled}
                        onChange={(e) =>
                          edit(
                            (p) =>
                              (p.requirements[i].enabled = e.target.checked),
                          )
                        }
                      />
                      Enabled
                    </label>
                  </div>
                ))}
              </>
            )}
            {tab === "verify" && (
              <>
                <div className="inspector-title">
                  <div className="feature-glyph">
                    <Activity size={18} />
                  </div>
                  <div>
                    <h2>Static stress</h2>
                    <span>One solid · Linear elastic</span>
                  </div>
                </div>
                <p className="muted">
                  Select planar faces in the viewport to define supports and
                  distributed forces.
                </p>
                {selection?.kind === "face" &&
                  selection.face.kind === "PLANE" &&
                  state.geometry_state === "current" && (
                    <div className="selection-card">
                      <code>{selection.id}</code>
                      <div>
                        <button onClick={() => fixed(selection.id)}>
                          <Lock size={13} />
                          Fix face
                        </button>
                        <button onClick={() => force(selection.id)}>
                          <Plus size={13} />
                          Apply force
                        </button>
                      </div>
                    </div>
                  )}
                <div className="section-heading">MATERIAL</div>
                {!current.material ? (
                  <button
                    className="wide-button"
                    onClick={() =>
                      edit(
                        (p) =>
                          (p.material = {
                            name: "User-defined material",
                            youngs_modulus_mpa: 0,
                            poisson_ratio: 0.3,
                            density_g_cm3: 0,
                            yield_strength_mpa: null,
                            source: "",
                          }),
                      )
                    }
                  >
                    <Plus size={14} />
                    Define material
                  </button>
                ) : (
                  <>
                    <Field
                      label="Material name"
                      value={current.material.name}
                      onChange={(v: string) =>
                        edit((p) => (p.material.name = v))
                      }
                    />
                    <Field
                      label="Young’s modulus"
                      type="number"
                      value={current.material.youngs_modulus_mpa}
                      unit="MPa"
                      onChange={(v: number) =>
                        edit((p) => (p.material.youngs_modulus_mpa = v))
                      }
                    />
                    <Field
                      label="Poisson ratio"
                      type="number"
                      value={current.material.poisson_ratio}
                      onChange={(v: number) =>
                        edit((p) => (p.material.poisson_ratio = v))
                      }
                    />
                    <Field
                      label="Density"
                      type="number"
                      value={current.material.density_g_cm3}
                      unit="g/cm³"
                      onChange={(v: number) =>
                        edit((p) => (p.material.density_g_cm3 = v))
                      }
                    />
                    <Field
                      label="Material data source"
                      value={current.material.source}
                      onChange={(v: string) =>
                        edit((p) => (p.material.source = v))
                      }
                    />
                  </>
                )}
                <div className="section-heading">FIXED SUPPORTS</div>
                {current.study.fixed_faces.length === 0 && (
                  <p className="empty-note">No fixed faces selected</p>
                )}
                {current.study.fixed_faces.map((face, i) => (
                  <div className="region-chip" key={face}>
                    <Lock size={13} />
                    <code>{face}</code>
                    <button
                      title="Remove support"
                      onClick={() =>
                        edit((p) => p.study.fixed_faces.splice(i, 1))
                      }
                    >
                      <X size={13} />
                    </button>
                  </div>
                ))}
                <div className="section-heading">DISTRIBUTED FORCES</div>
                {current.study.forces.length === 0 && (
                  <p className="empty-note">No applied forces</p>
                )}
                {current.study.forces.map((load, i) => (
                  <div className="load-card" key={load.face}>
                    <div>
                      <code>{load.face}</code>
                      <button
                        title="Remove force"
                        onClick={() => edit((p) => p.study.forces.splice(i, 1))}
                      >
                        <X size={13} />
                      </button>
                    </div>
                    <VectorField
                      label="Force"
                      unit="N"
                      value={load.vector_n}
                      onChange={(v) =>
                        edit((p) => (p.study.forces[i].vector_n = v))
                      }
                    />
                  </div>
                ))}
                <div className="section-heading">NUMERICAL SETTINGS</div>
                <Field
                  label="Initial mesh size"
                  type="number"
                  value={current.study.mesh_size_mm}
                  unit="mm"
                  onChange={(v: number) =>
                    edit((p) => (p.study.mesh_size_mm = v))
                  }
                />
                <Field
                  label="Refinement steps"
                  type="number"
                  value={current.study.refinement_steps}
                  onChange={(v: number) =>
                    edit((p) => (p.study.refinement_steps = v))
                  }
                />
                <Field
                  label="Convergence tolerance"
                  type="number"
                  value={current.study.convergence_tolerance}
                  onChange={(v: number) =>
                    edit((p) => (p.study.convergence_tolerance = v))
                  }
                />
                {current.study.stress_probe_mm ? (
                  <>
                    <VectorField
                      label="Stress probe"
                      value={current.study.stress_probe_mm}
                      onChange={(v) =>
                        edit((p) => (p.study.stress_probe_mm = v))
                      }
                    />
                    <button
                      className="text-button"
                      onClick={() =>
                        edit((p) => (p.study.stress_probe_mm = null))
                      }
                    >
                      Remove stress probe
                    </button>
                  </>
                ) : (
                  <button
                    className="wide-button"
                    onClick={() =>
                      edit(
                        (p) =>
                          (p.study.stress_probe_mm = selection?.point ?? [
                            0, 0, 0,
                          ]),
                      )
                    }
                  >
                    Add named stress probe
                  </button>
                )}
                <label className="assumptions">
                  <input
                    type="checkbox"
                    checked={current.study.assumptions_confirmed}
                    onChange={(e) =>
                      edit(
                        (p) =>
                          (p.study.assumptions_confirmed = e.target.checked),
                      )
                    }
                  />
                  <span>
                    I have reviewed the supports, forces and material data.
                    Small deformation, linear elasticity and negligible dynamic
                    effects apply.
                  </span>
                </label>
                <button
                  className="primary full-width"
                  disabled={
                    dirty ||
                    busy ||
                    pending ||
                    !current.study.assumptions_confirmed
                  }
                  onClick={() => run("simulate")}
                >
                  <Play size={15} />
                  Run static study
                </button>
                <div className="section-heading">
                  RESULTS{" "}
                  <span className="result-state">
                    {state.analysis_state.replace("_", " ")}
                  </span>
                </div>
                {state.analysis ? (
                  <>
                    <div className="result-metric">
                      <span>Maximum displacement</span>
                      <strong>
                        {state.analysis.max_displacement_mm.toPrecision(4)}{" "}
                        <small>mm</small>
                      </strong>
                    </div>
                    <div className="result-metric">
                      <span>Peak von Mises stress</span>
                      <strong>
                        {state.analysis.max_stress_mpa.toPrecision(4)}{" "}
                        <small>MPa</small>
                      </strong>
                    </div>
                    <div className="two-fields">
                      <button
                        className="wide-button"
                        onClick={() => setField("stress")}
                      >
                        View stress
                      </button>
                      <button
                        className="wide-button"
                        onClick={() => setField("displacement")}
                      >
                        View displacement
                      </button>
                    </div>
                    <p className="muted">
                      Reaction balance error:{" "}
                      {(state.analysis.force_balance_error * 100).toFixed(3)}%
                    </p>
                    {state.analysis.iterations.map((it: any, i: number) => (
                      <div className="iteration-row" key={i}>
                        <span>
                          Mesh {i + 1} · {it.nodes} nodes
                        </span>
                        <strong>
                          {it.max_displacement_mm.toPrecision(4)} mm
                        </strong>
                      </div>
                    ))}
                    {state.analysis.warnings.map((w: string) => (
                      <p className="study-warning" key={w}>
                        <AlertTriangle size={13} />
                        {w}
                      </p>
                    ))}
                  </>
                ) : (
                  <p className="empty-note">
                    A completed study will appear here. Stale results are never
                    shown as current.
                  </p>
                )}
              </>
            )}
            {tab === "history" && (
              <>
                <h2>Every change, recorded.</h2>
                <p className="muted">
                  Restore creates a new revision. Your existing history stays
                  intact.
                </p>
                {[...state.history].reverse().map((item) => (
                  <div className="history-card" key={item.revision}>
                    <div>
                      <strong>Revision {item.revision}</strong>
                      <span>{item.actor}</span>
                    </div>
                    <p>{item.summary}</p>
                    <small>{new Date(item.time).toLocaleString()}</small>
                    {item.revision !== current.revision && (
                      <button onClick={() => history("restore", item.revision)}>
                        <RotateCcw size={13} />
                        Restore
                      </button>
                    )}
                  </div>
                ))}
              </>
            )}
            {tab === "source" && (
              <>
                <h2>Editable project source</h2>
                <p className="muted">
                  This is the same typed document used by the UI and CLI.
                  Changes are validated when applied.
                </p>
                <textarea
                  className="project-source"
                  aria-label="Project JSON"
                  spellCheck={false}
                  value={source}
                  onChange={(e) => {
                    setSource(e.target.value);
                    dirtyRef.current = true;
                    setDirty(true);
                  }}
                />
                <button
                  className="primary full-width"
                  onClick={() => {
                    try {
                      save(JSON.parse(source));
                    } catch (e) {
                      setError(String(e));
                    }
                  }}
                >
                  <Check size={15} />
                  Apply source document
                </button>
                <a className="schema-link" href="/api/schema" target="_blank">
                  View project JSON Schema ↗
                </a>
              </>
            )}
            {selection && tab === "design" && (
              <>
                <div className="section-heading">SELECTED GEOMETRY</div>
                <div className="selection-card">
                  <code>{selection.id}</code>
                  <p>
                    {selection.face.kind} · {selection.face.area_mm2.toFixed(2)}{" "}
                    mm²
                  </p>
                  <small>
                    Point {selection.point.map((v) => v.toFixed(3)).join(", ")}{" "}
                    mm
                  </small>
                  <button
                    className="wide-button"
                    onClick={() => {
                      setTab("verify");
                      setActive("");
                    }}
                  >
                    Use in study
                  </button>
                </div>
              </>
            )}
          </div>
        </aside>
      </main>
      <section
        className={`verification-panel ${showBottom ? "" : "collapsed"}`}
      >
        <button
          className="verification-heading"
          onClick={() => setShowBottom(!showBottom)}
        >
          <ShieldCheck size={16} />
          <strong>Verification</strong>
          <span>{checks?.state ?? "Run checks to verify this revision"}</span>
          <ChevronDown size={15} />
        </button>
        {showBottom && (
          <div className="verification-body">
            <div className="check-list">
              {checks?.key === state.geometry_key ? (
                checks.findings.map((f: any, i: number) => (
                  <div key={i} className={`finding ${f.severity}`}>
                    <span>
                      {f.severity === "error" ? (
                        <X size={13} />
                      ) : f.severity === "warning" ? (
                        <AlertTriangle size={13} />
                      ) : (
                        <Check size={13} />
                      )}
                    </span>
                    <div>
                      <strong>{f.message}</strong>
                      {f.value !== null && (
                        <small>
                          Measured {Number(f.value).toPrecision(5)}
                          {f.limit !== null ? ` · Limit ${f.limit}` : ""}
                        </small>
                      )}
                    </div>
                  </div>
                ))
              ) : (
                <div className="check-placeholder">
                  <ShieldCheck size={24} />
                  <div>
                    <strong>Engineering evidence lives here.</strong>
                    <p>
                      Build your model, then check dimensions, clearances and
                      exported mesh integrity.
                    </p>
                  </div>
                </div>
              )}
            </div>
            <div className="job-list">
              {state.jobs.slice(0, 4).map((job) => (
                <div className={`job ${job.state}`} key={job.id}>
                  {["running", "queued"].includes(job.state) ? (
                    <Loader2 size={14} className="spin" />
                  ) : job.state === "succeeded" ? (
                    <CheckCircle2 size={14} />
                  ) : (
                    <AlertTriangle size={14} />
                  )}
                  <div>
                    <strong>
                      {job.kind}{" "}
                      <small>
                        r{job.revision} · {job.state}
                      </small>
                    </strong>
                    {job.state === "failed" && (
                      <p title={job.message}>{job.message.slice(-240)}</p>
                    )}
                  </div>
                  {["running", "queued"].includes(job.state) && (
                    <button
                      title="Cancel job"
                      onClick={() =>
                        post(`jobs/${job.id}/cancel`, {}).then(refresh)
                      }
                    >
                      <X size={13} />
                    </button>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </section>
      <footer className="statusbar">
        <span>
          <i />
          All computation runs locally
        </span>
        <span>
          {current.features.filter((f) => !f.suppressed).length} active features
          ·{" "}
          {geometry
            ? `${geometry.faces.length} faces · ${(geometry.volume_mm3 / 1000).toFixed(2)} cm³`
            : "No built geometry"}
        </span>
        <span>Oxygen CAD · 0.1 alpha</span>
      </footer>
    </div>
  );
}
