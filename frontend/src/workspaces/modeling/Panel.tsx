import { useEffect, useRef, useState } from "react";
import { Button, Input, SelectControl } from "../../components/ui";
import type { DomainPanelProps, JsonObject } from "../contracts";
import {
  allowed,
  errorText,
  json,
  type Execute,
  type NativeSnapshot,
  type NativeCanvas,
} from "./native";
import SketchEditor from "./SketchEditor";
import OperationForm from "./OperationForm";
import History from "./History";
import Parameters from "./Parameters";
import Topology from "./Topology";
import CanvasAssets from "./CanvasAssets";
import { operations } from "./operations";
import "./modeling.css";

const safe = (promise: Promise<unknown>) => {
  void promise.catch(() => {});
};
type Tab = "Sketch" | "Solid" | "History" | "Parameters";

export default function ModelingPanel(props: DomainPanelProps) {
  const [snapshot, setSnapshot] = useState<NativeSnapshot | null>(null);
  const [tab, setTab] = useState<Tab>("Sketch");
  const [focusedFeature, setFocusedFeature] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<{
    command: string;
    data: unknown;
  } | null>(null);
  const [plane, setPlane] = useState("XY");
  const [name, setName] = useState("");
  const [offset, setOffset] = useState("0 mm");
  const [sketchId, setSketchId] = useState<number | null>(null);
  const [profileIds, setProfileIds] = useState<number[]>([]);
  const [operationId, setOperationId] = useState("solid.extrude");
  const [sketchOperationId, setSketchOperationId] = useState("sketch.offset");
  const [projection, setProjection] = useState('["origin"]');
  const [projectionType, setProjectionType] = useState("sketch.project");
  const [projectionSource, setProjectionSource] = useState("");
  const [projectionCurve, setProjectionCurve] = useState("");
  const [topology, setTopology] = useState<JsonObject>({});
  const propsRef = useRef(props);
  propsRef.current = props;
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const pending = useRef(0);
  const inspectGeneration = useRef(0);
  useEffect(() => {
    const navigate = (event: Event) => {
      const detail = (
        event as CustomEvent<{ feature?: number | string; tab?: string }>
      ).detail;
      const nextTab = (
        {
          sketch: "Sketch",
          solid: "Solid",
          history: "History",
          parameters: "Parameters",
        } as Record<string, Tab>
      )[detail?.tab?.toLowerCase() ?? ""];
      if (nextTab) setTab(nextTab);
      if (detail?.feature !== undefined) {
        setFocusedFeature(Number(detail.feature));
        if (!nextTab) setTab("History");
      }
    };
    window.addEventListener("o3d:modeling", navigate);
    return () => window.removeEventListener("o3d:modeling", navigate);
  }, []);
  const reload = async () => {
    const generation = ++inspectGeneration.current;
    const data = (await propsRef.current.inspectNative()) as NativeSnapshot;
    if (generation === inspectGeneration.current) setSnapshot(data);
  };
  useEffect(() => {
    let disposed = false;
    void props
      .inspectNative()
      .then((data) => {
        if (!disposed) setSnapshot(data as NativeSnapshot);
      })
      .catch((e) => {
        if (!disposed) setError(errorText(e));
      });
    return () => {
      disposed = true;
    };
  }, [props.project.revision]);
  const execute: Execute = (command, params = {}) => {
    pending.current += 1;
    setBusy(true);
    setError("");
    const run = queue.current
      .catch(() => {})
      .then(async () => {
        try {
          const reply = await propsRef.current.native(command, params);
          setResult({ command, data: reply.result });
          await reload();
          return reply.result;
        } catch (e) {
          setError(errorText(e));
          throw e;
        } finally {
          pending.current -= 1;
          setBusy(pending.current > 0);
        }
      });
    queue.current = run;
    return run;
  };
  const inspection = snapshot?.inspection;
  const locks = {
    ...Object.fromEntries(
      Object.entries(props.project.parameters ?? {}).map(
        ([name, parameter]) => [name, parameter.locked],
      ),
    ),
    ...((props.domainData.parameter_locks ?? {}) as Record<string, boolean>),
  };
  const lockParameter = (name: string, locked: boolean): Promise<void> => {
    pending.current += 1;
    setBusy(true);
    setError("");
    const run = queue.current
      .catch(() => {})
      .then(async () => {
        const current = propsRef.current;
        try {
          await current.saveDomain("modeling", {
            ...current.domainData,
            parameter_locks: {
              ...((current.domainData.parameter_locks ?? {}) as JsonObject),
              [name]: locked,
            },
          });
          await reload();
        } catch (e) {
          setError(errorText(e));
          throw e;
        } finally {
          pending.current -= 1;
          setBusy(pending.current > 0);
        }
      });
    queue.current = run;
    return run;
  };
  const sketches = snapshot?.sketches ?? [];
  const canvases = ((snapshot?.document as JsonObject | undefined)?.canvases ??
    []) as NativeCanvas[];
  const activeId = inspection?.active_sketch;
  const sketch =
    sketches.find((s) => s.id === activeId) ??
    sketches.find((s) => s.id === sketchId) ??
    sketches.at(-1);
  const operation = operations.find((o) => o.command === operationId)!;
  const sketchOperation = operations.find(
    (o) => o.command === sketchOperationId,
  )!;
  const makeSketch = async () => {
    const params: JsonObject = { plane, offset };
    if (name.trim()) params.name = name.trim();
    const created = (await execute("sketch.create", params)) as JsonObject;
    setSketchId(Number(created.sketch ?? created.id));
    setProfileIds([]);
  };
  const initial = {
    ...topology,
    ...(sketch ? { sketch: sketch.name } : {}),
    ...(profileIds.length ? { profiles: profileIds } : {}),
  };
  return (
    <div
      className="modeling-panel"
      aria-label="Modeling workspace"
      aria-busy={busy}
    >
      <div className="modeling-row">
        <strong>Modeling</strong>
        <span className="modeling-muted">
          Revision {props.project.revision}
        </span>
        <Button
          disabled={busy}
          aria-label="Refresh native model"
          onClick={() => safe(reload())}
        >
          Refresh
        </Button>
      </div>
      <nav className="modeling-tabs" aria-label="Modeling tools">
        {(["Sketch", "Solid", "History", "Parameters"] as Tab[]).map((name) => (
          <Button
            key={name}
            aria-pressed={tab === name}
            onClick={() => setTab(name)}
          >
            {name}
          </Button>
        ))}
      </nav>
      {error && (
        <div className="modeling-error" role="alert">
          <strong>Operation refused</strong>
          <p>{error}</p>
          <Button onClick={() => setError("")}>Dismiss</Button>
        </div>
      )}
      {!snapshot && !error && (
        <p className="modeling-muted">Reading native design…</p>
      )}
      {tab === "Sketch" && (
        <>
          {activeId ? (
            <div className="modeling-row">
              <span className="modeling-muted">
                Editing {sketch?.name ?? activeId}
              </span>
              <Button
                disabled={busy}
                variant="primary"
                onClick={() => safe(execute("sketch.finish"))}
              >
                Finish sketch
              </Button>
            </div>
          ) : (
            <details open={!sketches.length}>
              <summary>Create sketch</summary>
              <form
                className="modeling-form"
                onSubmit={(event) => {
                  event.preventDefault();
                  safe(makeSketch());
                }}
              >
                <label>
                  Sketch name
                  <Input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="Automatic"
                  />
                </label>
                <label>
                  Sketch plane
                  <SelectControl
                    value={plane}
                    onChange={(e) => setPlane(e.target.value)}
                  >
                    {[
                      "XY",
                      "XZ",
                      "YZ",
                      ...(inspection?.timeline ?? [])
                        .filter((f) => f.type.toLowerCase().includes("plane"))
                        .map((f) => f.name),
                    ].map((p) => (
                      <option key={p}>{p}</option>
                    ))}
                  </SelectControl>
                </label>
                <label>
                  Plane offset
                  <Input
                    value={offset}
                    onChange={(e) => setOffset(e.target.value)}
                  />
                </label>
                <Button
                  type="submit"
                  disabled={busy || !allowed(snapshot, "sketch.create")}
                  variant="primary"
                >
                  Create sketch
                </Button>
                {props.selection && (
                  <Button
                    disabled={busy}
                    onClick={() =>
                      safe(
                        execute("sketch.create", {
                          plane: { face: props.selection!.point },
                          ...(name ? { name } : {}),
                          project_edges: true,
                        }),
                      )
                    }
                  >
                    Create on selected face
                  </Button>
                )}
              </form>
            </details>
          )}
          {sketches.length > 1 && !activeId && (
            <label>
              Sketch
              <SelectControl
                aria-label="Inspect sketch"
                value={sketch?.id ?? ""}
                onChange={(e) => {
                  setSketchId(Number(e.target.value));
                  setProfileIds([]);
                }}
              >
                {sketches.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </SelectControl>
            </label>
          )}
          {sketch && (
            <SketchEditor
              key={sketch.id}
              sketch={sketch}
              active={activeId === sketch.id}
              busy={busy}
              execute={execute}
              onProfiles={setProfileIds}
              canvases={canvases}
              nativeSelection={(inspection?.selection ?? []) as JsonObject[]}
            />
          )}
          {sketch && !activeId && (
            <Button
              onClick={() => {
                setTab("Solid");
                setOperationId("solid.extrude");
              }}
            >
              Extrude selected profiles
            </Button>
          )}
          {activeId && (
            <>
              {sketch && (
                <CanvasAssets
                  canvases={canvases}
                  sketch={sketch}
                  execute={execute}
                  busy={busy}
                />
              )}
              <details>
                <summary>Project / include and repair references</summary>
                <label>
                  Projection operation
                  <SelectControl
                    value={projectionType}
                    onChange={(e) => setProjectionType(e.target.value)}
                  >
                    {[
                      ["sketch.project", "Project"],
                      ["sketch.intersect", "Intersect"],
                      ["sketch.include_3d", "Include 3D"],
                    ].map(([id, label]) => (
                      <option key={id} value={id}>
                        {label}
                      </option>
                    ))}
                  </SelectControl>
                </label>
                <label>
                  Source sketch
                  <SelectControl
                    value={projectionSource}
                    onChange={(e) => setProjectionSource(e.target.value)}
                  >
                    <option value="">Choose source sketch</option>
                    {sketches
                      .filter((s) => s.id !== activeId)
                      .map((s) => (
                        <option key={s.id} value={s.name}>
                          {s.name}
                        </option>
                      ))}
                  </SelectControl>
                </label>
                <label>
                  Source curve ID
                  <Input
                    value={projectionCurve}
                    onChange={(e) => setProjectionCurve(e.target.value)}
                  />
                </label>
                <Button
                  disabled={!projectionSource || !projectionCurve}
                  onClick={() =>
                    setProjection(
                      JSON.stringify([
                        { sketch: projectionSource, curve: projectionCurve },
                      ]),
                    )
                  }
                >
                  Use source curve
                </Button>
                {props.selection && (
                  <Button
                    onClick={() =>
                      setProjection(
                        JSON.stringify([{ face: props.selection!.point }]),
                      )
                    }
                  >
                    Use selected face outline
                  </Button>
                )}
                <label>
                  References
                  <textarea
                    aria-label="Projection references"
                    value={projection}
                    onChange={(e) => setProjection(e.target.value)}
                  />
                </label>
                <Button
                  disabled={busy}
                  onClick={() => {
                    try {
                      safe(
                        execute(projectionType, {
                          refs: JSON.parse(projection),
                        }),
                      );
                    } catch (e) {
                      setError(errorText(e));
                    }
                  }}
                >
                  Project references
                </Button>
                {(sketch?.links ?? []).map((link) => (
                  <div className="modeling-reference" key={link.id}>
                    <div className="modeling-row">
                      <strong>{link.id}</strong>
                      <span
                        className={
                          link.lost ? "modeling-danger" : "modeling-success"
                        }
                      >
                        {link.lost
                          ? "Lost reference · repair required"
                          : "Linked"}
                      </span>
                      <Button
                        disabled={busy}
                        onClick={() =>
                          safe(execute("sketch.break_link", { link: link.id }))
                        }
                      >
                        Break link
                      </Button>
                    </div>
                    <code>{JSON.stringify(link.source)}</code>
                  </div>
                ))}
                <p className="modeling-muted">
                  To replace a lost source, explicitly break its link, delete
                  the obsolete entities and project the replacement. Automatic
                  relinking that preserves curve identities is not yet
                  supported.
                </p>
              </details>
              <details>
                <summary>Sketch modifications and 3D paths</summary>
                <label>
                  Modify sketch
                  <SelectControl
                    aria-label="Modify sketch"
                    value={sketchOperationId}
                    onChange={(e) => setSketchOperationId(e.target.value)}
                  >
                    {operations
                      .filter((o) => o.group === "Sketch")
                      .map((o) => (
                        <option key={o.command} value={o.command}>
                          {o.label}
                        </option>
                      ))}
                  </SelectControl>
                </label>
                {sketchOperationId.endsWith("3d") && (
                  <Button
                    disabled={busy}
                    onClick={() =>
                      safe(execute("sketch.options", { sketch_3d: true }))
                    }
                  >
                    Enable 3D sketch
                  </Button>
                )}
                <OperationForm
                  key={sketchOperationId}
                  operation={sketchOperation}
                  execute={execute}
                  busy={busy}
                  command={snapshot?.commands.find(
                    (c) => c.id === sketchOperationId,
                  )}
                  selection={props.selection}
                />
              </details>
            </>
          )}
        </>
      )}
      {tab === "Solid" && (
        <>
          {!!activeId && (
            <p className="modeling-warning">
              A sketch is being edited. Finish it before body modification.
            </p>
          )}
          <Topology
            key={(inspection?.bodies ?? []).map((b) => b.name).join(",")}
            bodies={(inspection?.bodies ?? []).map((b) => b.name)}
            execute={execute}
            busy={busy}
            onSelection={setTopology}
          />
          <label>
            Solid operation
            <SelectControl
              aria-label="Solid operation"
              value={operationId}
              onChange={(e) => setOperationId(e.target.value)}
            >
              {["Create", "Modify", "Construct", "Plastic"].map((group) => (
                <optgroup key={group} label={group}>
                  {operations
                    .filter((o) => o.group === group)
                    .map((o) => (
                      <option key={o.command} value={o.command}>
                        {o.label}
                      </option>
                    ))}
                </optgroup>
              ))}
            </SelectControl>
          </label>
          <OperationForm
            key={`${operationId}:${JSON.stringify(initial)}`}
            operation={operation}
            execute={execute}
            busy={busy}
            command={snapshot?.commands.find((c) => c.id === operationId)}
            initial={initial}
            selection={props.selection}
          />
        </>
      )}
      {tab === "History" && (
        <History
          features={inspection?.timeline ?? []}
          marker={inspection?.marker ?? 0}
          execute={execute}
          busy={busy}
          focusedFeature={focusedFeature}
        />
      )}
      {tab === "Parameters" && (
        <Parameters
          parameters={inspection?.params ?? []}
          execute={execute}
          busy={busy}
          locks={locks}
          onLock={lockParameter}
        />
      )}
      {result && (
        <details className="modeling-command-result">
          <summary>{result.command} · engine result</summary>
          <pre aria-label="Native operation result">{json(result.data)}</pre>
        </details>
      )}
    </div>
  );
}
