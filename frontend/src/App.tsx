import { useCallback, useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import {
  Activity,
  ArrowDownToLine,
  ArrowLeft,
  ArrowRight,
  Box,
  Check,
  CheckCircle2,
  ChevronDown,
  Code2,
  Copy,
  Eye,
  EyeOff,
  FileBox,
  Focus,
  History,
  Loader2,
  Lock,
  MoreHorizontal,
  Sun,
  Moon,
  PanelLeft,
  PanelRight,
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
  type Project,
  type Selection,
  type State,
  type DisplayField,
} from "./api";
import { FeatureNavigator } from "./components/FeatureNavigator";
import { InspectorSection } from "./components/InspectorSection";
import { StudyPanel } from "./components/StudyPanel";
import { Viewport, type ViewOrientation } from "./Viewport";
import { Breadcrumbs } from "./components/Breadcrumbs";
import {
  Button,
  Input,
  SelectControl,
  Menu,
  MenuTrigger,
  MenuContent,
  MenuItem,
  MenuSeparator,
  ToolButton,
} from "./components/ui";
import { useTheme } from "./theme";

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
        <Input
          aria-label={label}
          type={type}
          step={step ?? "any"}
          value={value ?? ""}
          onChange={(e) =>
            onChange(
              type === "number"
                ? e.target.value === ""
                  ? null
                  : Number(e.target.value)
                : e.target.value,
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
            <Input
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
      <SelectControl
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
      </SelectControl>
    </label>
  );
}

export default function App() {
  const { theme, toggleTheme } = useTheme();
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
    [field, setField] = useState<DisplayField>("solid"),
    [modeIndex, setModeIndex] = useState(0),
    [incrementIndex, setIncrementIndex] = useState(-1),
    [deformation, setDeformation] = useState(0),
    [addOpen, setAddOpen] = useState(false),
    [checks, setChecks] = useState<any>(null),
    [source, setSource] = useState(""),
    [showAgent, setShowAgent] = useState(false),
    [showBottom, setShowBottom] = useState(false),
    [leftOpen, setLeftOpen] = useState(true),
    [rightOpen, setRightOpen] = useState(false);
  const [viewRequest, setViewRequest] = useState({
    name: "isometric" as ViewOrientation,
    version: 0,
  });
  const featureSearch = useRef<HTMLInputElement>(null);
  const saveCurrent = useRef(() => {});
  const draftRef = useRef(draft);
  draftRef.current = draft;
  const editSerial = useRef(0);
  const savingRef = useRef(false);
  const pendingSave = useRef(false);
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  const upload = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const analysis = state?.analysis;
    const available =
      field === "solid" ||
      (analysis &&
        (field === "stress"
          ? analysis.max_stress_mpa != null
          : field === "displacement"
            ? analysis.max_displacement_mm != null
            : field === "temperature"
              ? analysis.max_temperature_c != null
              : field === "plastic_strain"
                ? analysis.max_equivalent_plastic_strain != null
                : Boolean(
                    analysis.frequencies_hz || analysis.buckling_factors,
                  )));
    if (!available) setField("solid");
    setModeIndex(0);
    setIncrementIndex(-1);
  }, [state?.analysis?.key]);
  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setAddOpen(false);
    };
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
  }, []);
  const refresh = useCallback(async () => {
    try {
      const result = await api("state");
      setState((previous) =>
        previous &&
        previous.project.revision === result.project.revision &&
        previous.geometry_state === result.geometry_state &&
        previous.analysis_state === result.analysis_state &&
        JSON.stringify(
          Object.values(previous.studies ?? {}).map((s: any) => [
            s.key,
            s.result?.completed_at,
          ]),
        ) ===
          JSON.stringify(
            Object.values(result.studies ?? {}).map((s: any) => [
              s.key,
              s.result?.completed_at,
            ]),
          ) &&
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
    editSerial.current += 1;
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
    if (savingRef.current) {
      pendingSave.current = true;
      return;
    }
    savingRef.current = true;
    const submittedSerial = editSerial.current;
    const baseRevision = draftRef.current?.revision ?? project.revision;
    let succeeded = false;
    setBusy(true);
    setError("");
    try {
      const saved = await post("commands", {
        expected_revision: baseRevision,
        actor: "human",
        action: "replace",
        project,
        summary: "Manual studio edit",
      });
      const refreshed = await refresh();
      if (editSerial.current === submittedSerial) {
        const confirmed = refreshed?.project ?? saved.project;
        draftRef.current = confirmed;
        setDraft(confirmed);
        setSource(pretty(confirmed));
        dirtyRef.current = false;
        setDirty(false);
      } else {
        // Preserve edits made while the server acknowledged this save. They
        // start from our accepted revision, never a newer agent revision.
        setDraft((latest) => {
          const next = {
            ...(latest ?? project),
            revision: saved.project.revision,
          };
          draftRef.current = next;
          return next;
        });
        dirtyRef.current = true;
        setDirty(true);
      }
      succeeded = true;
    } catch (e) {
      setError(String(e));
    } finally {
      savingRef.current = false;
      setBusy(false);
      const queued = pendingSave.current;
      pendingSave.current = false;
      if (queued && succeeded)
        requestAnimationFrame(() => saveCurrent.current());
    }
  }
  async function run(kind: string) {
    setBusy(true);
    setError("");
    try {
      if (dirty) throw new Error("Save your edits before running an operation");
      if (kind === "check") setShowBottom(true);
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
    if (!measureMode) {
      setRightOpen(true);
      if (tab !== "verify") {
        setTab("design");
        setActive("");
      }
    }
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
    setRightOpen(true);
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
  const studyView = current
    ? {
        ...current,
        study:
          current.active_study_id === "default"
            ? current.study
            : current.named_studies[current.active_study_id],
      }
    : null;
  const analysis = dirty ? null : state?.analysis;
  const increment =
    analysis?.increments?.[
      incrementIndex < 0 ? analysis.increments.length - 1 : incrementIndex
    ];
  const displayedAnalysis = increment
    ? { ...analysis, ...increment }
    : analysis;
  const studyState =
    state && dirty
      ? { ...state, analysis: null, analysis_state: "unsaved_changes" }
      : state;
  function editStudy(fn: (p: Project) => void) {
    edit((p) => {
      if (p.active_study_id === "default") fn(p);
      else {
        const primary = p.study;
        p.study = p.named_studies[p.active_study_id];
        fn(p);
        p.named_studies[p.active_study_id] = p.study;
        p.study = primary;
      }
    });
  }
  const geometry = state?.build ?? state?.last_build;
  const feature = current?.features.find((f) => f.id === active);
  const reference = current?.references.find((r) => r.id === active);
  const pending = state?.jobs.some((j) =>
    ["queued", "running"].includes(j.state),
  );
  const inspectorVisible =
    rightOpen && Boolean(tab !== "design" || feature || reference || selection);
  function openPanel(panel: string) {
    setTab(panel);
    setRightOpen(true);
  }
  function toggleInspector() {
    if (inspectorVisible) setRightOpen(false);
    else {
      if (tab === "design" && !feature && !reference && !selection)
        setTab("project");
      setRightOpen(true);
    }
  }
  const panelLabel =
    tab === "verify"
      ? "Study"
      : tab === "source"
        ? "Source"
        : tab === "history"
          ? "History"
          : tab === "project"
            ? "Project"
            : feature?.name ||
              reference?.name ||
              (selection ? "Selection" : "Model");
  const lastExport = state?.jobs.find(
    (j) => j.kind === "export" && j.state === "succeeded",
  );
  saveCurrent.current = () => {
    if (!dirty) return;
    if (savingRef.current) {
      pendingSave.current = true;
      return;
    }
    if (busy) return;
    try {
      void save(tab === "source" ? JSON.parse(source) : draft);
    } catch (error) {
      setError(String(error));
    }
  };
  useEffect(() => {
    function shortcut(event: KeyboardEvent) {
      if (!current || !state || event.defaultPrevented || event.isComposing)
        return;
      const target = event.target as HTMLElement;
      if (target.closest('[role="menu"], [role="dialog"]')) return;
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        // JSON editors commit on blur. Let that state settle before saving the
        // latest draft, including when the buffer was the only pending change.
        if (
          target instanceof HTMLTextAreaElement &&
          target.closest(".source-field")
        ) {
          try {
            JSON.parse(target.value);
          } catch {
            setError("Editor contains invalid JSON");
            return;
          }
          flushSync(() => target.blur());
        }
        requestAnimationFrame(() => saveCurrent.current());
        return;
      }
      if (
        event.metaKey ||
        event.ctrlKey ||
        event.altKey ||
        event.shiftKey ||
        target.closest('input, textarea, select, [contenteditable="true"]')
      )
        return;
      if (event.key.toLowerCase() === "f") {
        event.preventDefault();
        setFit((v) => v + 1);
      }
      if (event.key.toLowerCase() === "m") {
        event.preventDefault();
        setMeasureMode((v) => !v);
        setMeasure([]);
      }
      if (event.key === "/") {
        event.preventDefault();
        setLeftOpen(true);
        requestAnimationFrame(() => featureSearch.current?.focus());
      }
      if (event.key === "Escape") {
        setSelection(null);
        setMeasure([]);
        setMeasureMode(false);
        setIsolate(false);
      }
    }
    document.addEventListener("keydown", shortcut);
    return () => document.removeEventListener("keydown", shortcut);
  });
  if (!current || !state)
    return (
      <div className="loading-screen">
        <p>{error || "Opening workspace…"}</p>
        <Loader2 className="spin" />
      </div>
    );
  return (
    <div className="app-shell">
      <header className="topbar">
        <Button
          className="sidebar-toggle"
          aria-label={
            leftOpen ? "Collapse feature sidebar" : "Expand feature sidebar"
          }
          aria-expanded={leftOpen}
          aria-controls="feature-sidebar"
          title={
            leftOpen ? "Collapse feature sidebar" : "Expand feature sidebar"
          }
          onClick={() => setLeftOpen(!leftOpen)}
        >
          <PanelLeft size={16} />
        </Button>
        <Breadcrumbs
          items={[
            {
              label: "Workspace",
              expanded: leftOpen,
              controls: "feature-sidebar",
              onToggle: () => setLeftOpen(!leftOpen),
            },
            {
              label: panelLabel,
              expanded: inspectorVisible,
              controls: "inspector-sidebar",
              onToggle: toggleInspector,
            },
          ]}
        />
        {dirty && (
          <span
            className="unsaved"
            title="Unsaved edits"
            aria-label="Unsaved edits"
          >
            <i aria-hidden="true" />
            <span className="unsaved-label">Unsaved</span>
          </span>
        )}
        <div className="top-actions">
          <Button
            className="theme-toggle"
            size="icon"
            onClick={toggleTheme}
            aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
            title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
          >
            {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
          </Button>
          {dirty && (
            <ToolButton
              hint="Save changes"
              shortcut={/Mac/i.test(navigator.platform) ? "⌘S" : "Ctrl S"}
              variant="primary"
              aria-label="Save changes"
              disabled={busy}
              onClick={() => {
                try {
                  save(tab === "source" ? JSON.parse(source) : draft);
                } catch (e) {
                  setError(String(e));
                }
              }}
            >
              <Save size={15} />
              <span className="save-label">Save changes</span>
            </ToolButton>
          )}
          <Button
            title="Export"
            aria-label="Export"
            disabled={
              busy || dirty || pending || state.geometry_state !== "current"
            }
            onClick={() => run("export")}
          >
            <ArrowDownToLine size={16} />
          </Button>
          {lastExport && (
            <a
              className="download-link"
              href={artifact(lastExport.result.artifact)}
              download
              aria-label={`Download r${lastExport.revision}`}
              title={`Download r${lastExport.revision}`}
            >
              <ArrowDownToLine size={13} />
              <span className="download-label">
                Download r{lastExport.revision}
              </span>
            </a>
          )}
          <Menu>
            <MenuTrigger asChild>
              <Button title="Project tools" aria-label="Project tools">
                <MoreHorizontal size={19} />
              </Button>
            </MenuTrigger>
            <MenuContent className="tools-popover">
              {[
                ["project", Settings2, "Project settings"],
                ["verify", ShieldCheck, "Study setup"],
                ["source", Code2, "Source"],
                ["history", History, "History"],
              ].map(([id, Icon, label]: any) => (
                <MenuItem
                  className="ui-menu-item"
                  key={id}
                  onSelect={() => {
                    openPanel(id);
                  }}
                >
                  <Icon size={15} />
                  {label}
                </MenuItem>
              ))}
              <MenuItem
                className="ui-menu-item"
                disabled={dirty}
                onSelect={() => {
                  upload.current?.click();
                }}
              >
                <Upload size={15} />
                Import STEP
              </MenuItem>
              <MenuItem
                className="ui-menu-item"
                onSelect={() => {
                  setShowAgent(!showAgent);
                }}
              >
                <Terminal size={15} />
                Agent tools
              </MenuItem>
              <MenuSeparator className="menu-divider" />
              <MenuItem
                className="ui-menu-item"
                disabled={dirty || state.project.revision === 0}
                onSelect={() => {
                  history("undo");
                }}
              >
                <ArrowLeft size={15} />
                Undo
              </MenuItem>
              <MenuItem
                className="ui-menu-item"
                disabled={dirty}
                onSelect={() => {
                  history("redo");
                }}
              >
                <ArrowRight size={15} />
                Redo
              </MenuItem>
            </MenuContent>
          </Menu>
          <Button
            className="sidebar-toggle"
            aria-label={
              inspectorVisible
                ? "Collapse inspector sidebar"
                : "Expand inspector sidebar"
            }
            aria-expanded={inspectorVisible}
            aria-controls="inspector-sidebar"
            title={
              inspectorVisible
                ? "Collapse inspector sidebar"
                : "Expand inspector sidebar"
            }
            onClick={toggleInspector}
          >
            <PanelRight size={16} />
          </Button>
        </div>
      </header>
      {error && (
        <div role="alert" className="error-banner">
          <AlertTriangle size={16} />
          <span>{error.replace("Error: ", "")}</span>
          <Button onClick={() => setError("")} aria-label="Dismiss error">
            <X size={16} />
          </Button>
        </div>
      )}
      {showAgent && (
        <div className="agent-banner">
          <Terminal size={18} />
          <div>
            <strong>Agent commands</strong>
            <code>
              ./o3d inspect --json &nbsp; · &nbsp; ./o3d context --json &nbsp; ·
              &nbsp; ./o3d apply proposal.json --expected-revision{" "}
              {state.project.revision}
            </code>
          </div>
          <Button
            aria-label="Copy agent commands"
            onClick={() =>
              navigator.clipboard.writeText(
                `./o3d inspect --json\n./o3d context --json\n./o3d apply proposal.json --expected-revision ${state.project.revision}`,
              )
            }
          >
            <Copy size={16} />
          </Button>
          <Button
            aria-label="Close agent tools"
            onClick={() => setShowAgent(false)}
          >
            <X size={15} />
          </Button>
        </div>
      )}
      <main className="workspace">
        <aside className="left-panel" id="feature-sidebar" hidden={!leftOpen}>
          <div className="panel-heading">
            <span>
              Features{" "}
              <small className="feature-count">{current.features.length}</small>
            </span>
            <Button
              onClick={() => setAddOpen(!addOpen)}
              aria-label="Add feature"
              aria-expanded={addOpen}
            >
              <Plus size={16} />
            </Button>
          </div>
          <FeatureNavigator
            features={current.features}
            active={active}
            searchRef={featureSearch}
            onSelect={(id) => {
              setActive(id);
              setTab("design");
              setRightOpen(true);
            }}
          />
          {addOpen && (
            <div className="add-menu">
              {["profile", "extrude", "hole", "fillet", "chamfer"].map(
                (type) => (
                  <Button key={type} onClick={() => add(type)}>
                    <Plus size={14} />
                    Add {type}
                  </Button>
                ),
              )}
            </div>
          )}
          {current.references.length > 0 && (
            <div className="tree-heading references-heading">
              <FileBox size={14} />
              <span>References</span>
            </div>
          )}
          {current.references.map((ref) => (
            <div className="reference-row" key={ref.id}>
              <Button
                className={`tree-item ${active === ref.id ? "selected" : ""}`}
                onClick={() => {
                  setActive(ref.id);
                  setTab("design");
                  setRightOpen(true);
                }}
              >
                <FileBox size={14} />
                <span>{ref.name}</span>
              </Button>
              <Button
                title="Toggle reference visibility"
                onClick={() =>
                  edit((p) => {
                    const r = p.references.find((v) => v.id === ref.id)!;
                    r.visible = !r.visible;
                  })
                }
              >
                {ref.visible ? <Eye size={13} /> : <EyeOff size={13} />}
              </Button>
            </div>
          ))}
          <Input
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
        </aside>
        <section className="canvas-panel">
          <div className="canvas-toolbar">
            <div>
              <ToolButton
                hint="Measure between two surface points"
                shortcut="M"
                className={measureMode ? "tool-active" : ""}
                aria-pressed={measureMode}
                onClick={() => {
                  setMeasureMode(!measureMode);
                  setMeasure([]);
                }}
              >
                <Ruler size={17} />
              </ToolButton>
              <span />
              <ToolButton
                hint="Section view"
                className={section ? "tool-active" : ""}
                aria-pressed={section}
                onClick={() => setSection(!section)}
              >
                <Scissors size={17} />
              </ToolButton>
              {current.references.length > 0 && (
                <Button
                  className={isolate ? "tool-active" : ""}
                  title="Isolate manufactured part"
                  aria-pressed={isolate}
                  onClick={() => setIsolate(!isolate)}
                >
                  <Eye size={17} />
                </Button>
              )}
            </div>
            <div>
              {analysis && (
                <SelectControl
                  aria-label="Viewport display"
                  value={field}
                  onChange={(e) => setField(e.target.value as any)}
                >
                  <option value="solid">Solid model</option>
                  <option
                    value="stress"
                    disabled={analysis.max_stress_mpa == null}
                  >
                    Stress field
                  </option>
                  <option
                    value="displacement"
                    disabled={analysis.max_displacement_mm == null}
                  >
                    Displacement
                  </option>
                  <option
                    value="temperature"
                    disabled={analysis.max_temperature_c == null}
                  >
                    Temperature
                  </option>
                  <option
                    value="mode"
                    disabled={
                      !analysis.frequencies_hz && !analysis.buckling_factors
                    }
                  >
                    Mode shape
                  </option>
                  {analysis.max_equivalent_plastic_strain != null && (
                    <option value="plastic_strain">Plastic strain</option>
                  )}
                </SelectControl>
              )}
              {analysis?.increments && (
                <SelectControl
                  aria-label="Load increment"
                  value={
                    incrementIndex < 0
                      ? analysis.increments.length - 1
                      : incrementIndex
                  }
                  onChange={(e) => setIncrementIndex(Number(e.target.value))}
                >
                  {analysis.increments.map((v: any, i: number) => (
                    <option value={i} key={i}>
                      Step {i + 1} · load ×{v.load_factor.toPrecision(3)}
                    </option>
                  ))}
                </SelectControl>
              )}
              {field === "mode" && analysis && (
                <SelectControl
                  aria-label="Mode number"
                  value={modeIndex}
                  onChange={(e) => setModeIndex(Number(e.target.value))}
                >
                  {(
                    analysis.frequencies_hz ??
                    analysis.buckling_factors ??
                    []
                  ).map((value: number, i: number) => (
                    <option value={i} key={i}>
                      Mode {i + 1} · {value.toPrecision(4)}{" "}
                      {analysis.frequencies_hz ? "Hz" : "×"}
                    </option>
                  ))}
                </SelectControl>
              )}
              <Menu>
                <MenuTrigger asChild>
                  <Button
                    className="view-picker"
                    aria-label="Orient view"
                    title="Orient view"
                  >
                    <Box size={14} />
                    <span>View</span>
                    <ChevronDown size={12} />
                  </Button>
                </MenuTrigger>
                <MenuContent className="tools-popover view-menu">
                  {(
                    ["isometric", "front", "top", "right"] as ViewOrientation[]
                  ).map((name) => (
                    <MenuItem
                      className="ui-menu-item"
                      key={name}
                      onSelect={() =>
                        setViewRequest((prev) => ({
                          name,
                          version: prev.version + 1,
                        }))
                      }
                    >
                      <span>
                        {name.charAt(0).toUpperCase() + name.slice(1)}
                      </span>
                    </MenuItem>
                  ))}
                </MenuContent>
              </Menu>
              <ToolButton
                hint="Fit model"
                shortcut="F"
                onClick={() => setFit((v) => v + 1)}
              >
                <Focus size={17} />
              </ToolButton>
            </div>
          </div>
          <Viewport
            theme={theme}
            viewRequest={viewRequest}
            geometry={geometry ?? null}
            project={studyView ?? current}
            selected={selection}
            onSelect={select}
            section={section}
            field={field}
            analysis={displayedAnalysis}
            modeIndex={modeIndex}
            fit={fit}
            deformation={deformation}
            measure={measure}
            showStudy={tab === "verify"}
            isolate={isolate}
          />
          {state.geometry_state === "stale" && (
            <div className="stale-label">
              <AlertTriangle size={14} />
              Showing previous geometry · rebuild required
            </div>
          )}
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
              <Button onClick={() => setMeasure([])}>
                <X size={13} />
              </Button>
            </div>
          )}
          {field !== "solid" && displayedAnalysis && (
            <div className="field-legend">
              <span>
                {field === "stress"
                  ? "von Mises stress · MPa"
                  : field === "temperature"
                    ? "Temperature · °C"
                    : field === "plastic_strain"
                      ? "Equivalent plastic strain · dimensionless"
                      : field === "mode"
                        ? "Mode shape · relative amplitude"
                        : "Displacement · mm"}
              </span>
              <div />
              <small>
                {field === "temperature"
                  ? displayedAnalysis.min_temperature_c?.toPrecision(4)
                  : 0}{" "}
                <b>
                  {(field === "stress"
                    ? displayedAnalysis.max_stress_mpa
                    : field === "temperature"
                      ? displayedAnalysis.max_temperature_c
                      : field === "plastic_strain"
                        ? displayedAnalysis.max_equivalent_plastic_strain
                        : field === "mode"
                          ? 1
                          : displayedAnalysis.max_displacement_mm
                  )?.toPrecision(4)}
                </b>
              </small>
              <label>
                {field === "mode" ? "Mode scale" : "Deformation"} ×{deformation}
                <Input
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
          {(state.geometry_state !== "current" || pending) && (
            <div className="canvas-actions">
              <Button
                className="build-button"
                variant="primary"
                disabled={busy || dirty || pending}
                onClick={() => run("build")}
              >
                {pending ? (
                  <Loader2 size={15} className="spin" />
                ) : (
                  <Play size={15} />
                )}
                {pending ? "Working…" : "Build model"}
              </Button>
            </div>
          )}
        </section>
        {inspectorVisible && (
          <aside
            id="inspector-sidebar"
            className={`right-panel ${tab === "source" ? "source-panel" : ""}`}
          >
            <div className="panel-heading">
              <span>
                {tab === "verify"
                  ? "Engineering study"
                  : tab === "history"
                    ? "Revision history"
                    : tab === "source"
                      ? "Project source"
                      : tab === "project"
                        ? "Project"
                        : "Properties"}
              </span>
              <Button
                aria-label="Close properties"
                onClick={() => {
                  setRightOpen(false);
                }}
              >
                <X size={15} />
              </Button>
            </div>
            <div className="properties">
              {tab === "design" && (
                <>
                  {feature ? (
                    <>
                      <div className="inspector-title">
                        <div className="feature-glyph">
                          {feature.type === "profile" ? (
                            <Square size={18} />
                          ) : (
                            <Box size={18} />
                          )}
                        </div>
                        <div>
                          <h2>{feature.name || feature.id}</h2>
                        </div>
                      </div>
                      <InspectorSection title="Geometry">
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
                              <div className="two-fields">
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
                              </div>
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
                            <InspectorSection title="Placement" open={false}>
                              <Select
                                label="Construction plane"
                                value={feature.plane}
                                options={["XY", "XZ", "YZ"]}
                                onChange={(v: string) =>
                                  patchFeature({ plane: v })
                                }
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
                            </InspectorSection>
                          </>
                        )}
                        {feature.type === "native" && (
                          <>
                            <Field
                              label="Native command"
                              value={feature.command}
                              onChange={(command: string) =>
                                patchFeature({ command })
                              }
                            />
                            <label className="source-field">
                              Arguments
                              <textarea
                                aria-label="Native arguments"
                                key={`${feature.id}-${state.project.revision}`}
                                defaultValue={pretty(feature.params)}
                                spellCheck={false}
                                onBlur={(event) => {
                                  try {
                                    patchFeature({
                                      params: JSON.parse(event.target.value),
                                    });
                                  } catch {
                                    setError(
                                      "Native arguments must be a JSON object",
                                    );
                                  }
                                }}
                              />
                            </label>
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
                              onChange={(v: string) =>
                                patchFeature({ profile: v })
                              }
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
                              onChange={(v: string) =>
                                patchFeature({ axis: v })
                              }
                            />
                            <label className="checkbox">
                              <Input
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
                                  <Input
                                    type="checkbox"
                                    checked={feature.reverse}
                                    onChange={(e) =>
                                      patchFeature({
                                        reverse: e.target.checked,
                                      })
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
                                onChange={(v: number) =>
                                  patchFeature({ rows: v })
                                }
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
                                        (x: number, j: number) =>
                                          j === i ? n : x,
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
                      </InspectorSection>
                      <InspectorSection title="Feature details" open={false}>
                        <Field
                          label="Feature name"
                          value={feature.name}
                          onChange={(v: string) => patchFeature({ name: v })}
                        />
                        <label className="checkbox">
                          <Input
                            type="checkbox"
                            checked={feature.suppressed}
                            onChange={(e) =>
                              patchFeature({ suppressed: e.target.checked })
                            }
                          />
                          Suppress feature
                        </label>
                        <div className="feature-order">
                          <Button
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
                          </Button>
                          <Button
                            disabled={
                              current.features.at(-1)?.id === feature.id
                            }
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
                          </Button>
                        </div>
                        <Button
                          className="danger-link"
                          variant="danger"
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
                        </Button>
                      </InspectorSection>
                    </>
                  ) : reference ? (
                    <>
                      <h2>{reference.name}</h2>
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
                        <Input
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
                      <Button
                        className="danger-link"
                        variant="danger"
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
                      </Button>
                    </>
                  ) : null}
                </>
              )}
              {tab === "project" && (
                <>
                  <Field
                    label="Project name"
                    value={current.name}
                    onChange={(value: string) => edit((p) => (p.name = value))}
                  />
                  <div className="section-heading">
                    PARAMETERS
                    <Button
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
                    </Button>
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
                      <Button
                        aria-label={`${p.locked ? "Unlock" : "Lock"} ${name}`}
                        className={p.locked ? "locked" : ""}
                        onClick={() =>
                          edit((d) => (d.parameters[name].locked = !p.locked))
                        }
                      >
                        {p.locked ? <Lock size={14} /> : <Unlock size={14} />}
                      </Button>
                      <Button
                        title={`Delete ${name}`}
                        onClick={() =>
                          edit((d) => {
                            delete d.parameters[name];
                          })
                        }
                      >
                        <X size={12} />
                      </Button>
                    </div>
                  ))}
                  <details>
                    <summary>Part placement</summary>
                    <VectorField
                      label="Part translation"
                      value={current.placement.translation}
                      onChange={(v) =>
                        edit((p) => (p.placement.translation = v))
                      }
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
                    <Button
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
                    </Button>
                  </div>
                  {current.requirements.map((req, i) => (
                    <div className="requirement-card" key={req.id}>
                      <div>
                        <strong>{req.id}</strong>
                        <Button
                          title="Delete requirement"
                          onClick={() =>
                            edit((p) => p.requirements.splice(i, 1))
                          }
                        >
                          <X size={13} />
                        </Button>
                      </div>
                      <Select
                        label="Requirement type"
                        value={req.kind}
                        options={[
                          "max_dimension",
                          "max_mass",
                          "min_clearance",
                          "max_displacement",
                          "max_temperature",
                          "min_frequency",
                          "min_buckling_factor",
                          "max_stress",
                        ]}
                        onChange={(v: string) =>
                          edit((p) => {
                            p.requirements[i].kind = v;
                            if (
                              [
                                "max_dimension",
                                "max_mass",
                                "min_clearance",
                              ].includes(v)
                            )
                              p.requirements[i].study_id = null;
                          })
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
                      {!["max_dimension", "max_mass", "min_clearance"].includes(
                        req.kind,
                      ) && (
                        <Select
                          label="Applies to study"
                          value={req.study_id ?? ""}
                          options={[
                            { value: "", label: "All studies" },
                            ...Object.entries({
                              default: current.study,
                              ...current.named_studies,
                            }).map(([id, s]) => ({ value: id, label: s.name })),
                          ]}
                          onChange={(v: string) =>
                            edit(
                              (p) => (p.requirements[i].study_id = v || null),
                            )
                          }
                        />
                      )}
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
                        <Input
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
                <StudyPanel
                  document={current}
                  editDocument={edit}
                  current={studyView ?? current}
                  state={studyState ?? state}
                  selection={selection}
                  blocked={Boolean(busy || dirty || pending)}
                  edit={editStudy}
                  run={run}
                  onField={setField}
                  Field={Field}
                  VectorField={VectorField}
                />
              )}
              {tab === "history" && (
                <>
                  {[...state.history].reverse().map((item) => (
                    <div className="history-card" key={item.revision}>
                      <div>
                        <strong>Revision {item.revision}</strong>
                        <span>{item.actor}</span>
                      </div>
                      <p>{item.summary}</p>
                      <small>{new Date(item.time).toLocaleString()}</small>
                      {item.revision !== current.revision && (
                        <Button
                          onClick={() => history("restore", item.revision)}
                        >
                          <RotateCcw size={13} />
                          Restore
                        </Button>
                      )}
                    </div>
                  ))}
                </>
              )}
              {tab === "source" && (
                <>
                  <textarea
                    className="project-source"
                    aria-label="Project JSON"
                    spellCheck={false}
                    value={source}
                    onChange={(e) => {
                      setSource(e.target.value);
                      editSerial.current += 1;
                      dirtyRef.current = true;
                      setDirty(true);
                    }}
                  />
                  <Button
                    className="full-width"
                    variant="primary"
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
                  </Button>
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
                      {selection.face.kind} ·{" "}
                      {selection.face.area_mm2.toFixed(2)} mm²
                    </p>
                    <small>
                      Point{" "}
                      {selection.point.map((v) => v.toFixed(3)).join(", ")} mm
                    </small>
                    <Button
                      className="wide-button"
                      onClick={() => {
                        openPanel("verify");
                        setActive("");
                      }}
                    >
                      Use in study
                    </Button>
                  </div>
                </>
              )}
            </div>
          </aside>
        )}
      </main>
      {(showBottom || pending || state.jobs[0]?.state === "failed") && (
        <section
          className={`verification-panel ${showBottom ? "" : "collapsed"}`}
        >
          <Button
            className="verification-heading"
            aria-expanded={showBottom}
            aria-controls="verification-body"
            onClick={() => setShowBottom(!showBottom)}
          >
            <ShieldCheck size={16} />
            <strong>Verification</strong>
            <span>{checks?.state ?? state.jobs[0]?.state}</span>
            <ChevronDown size={15} />
          </Button>
          {showBottom && (
            <div className="verification-body" id="verification-body">
              <div className="check-list">
                {checks?.key === state.geometry_key
                  ? checks.findings.map((f: any, i: number) => (
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
                  : null}
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
                      <Button
                        title="Cancel job"
                        onClick={() =>
                          post(`jobs/${job.id}/cancel`, {}).then(refresh)
                        }
                      >
                        <X size={13} />
                      </Button>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
