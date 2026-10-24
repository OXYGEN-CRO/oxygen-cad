import { useEffect, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import type { DomainPanelProps, JsonObject } from "../contracts";
import { Button, Input, SelectControl } from "../../components/ui";
import {
  type Data,
  type Component,
  type Placement,
  type PinRef,
  type Point,
  type Result,
  id,
  position,
  refkey,
} from "./types";
import "./electronics.css";
const clone = <T,>(v: T): T => structuredClone(v);
const points = (ps: Point[]) => ps.map((p) => p.join(",")).join(" ");
const empty = {
  schema_version: 1,
  components: [],
  library: [],
  nets: [],
  sheets: [],
  board: {
    placements: [],
    tracks: [],
    vias: [],
    stackup: [],
    outline: [],
    cutouts: [],
    holes: [],
    zones: [],
  },
};

export default function ElectronicsPanel(props: DomainPanelProps) {
  const [doc, setDoc] = useState<Data>(
    (props.domainData.schema_version ? props.domainData : empty) as Data,
  );
  const [tab, setTab] = useState("Schematic"),
    [sheet, setSheet] = useState("root"),
    [selected, setSelected] = useState(""),
    [net, setNet] = useState(""),
    [layer, setLayer] = useState("F.Cu");
  const [tool, setTool] = useState("Select"),
    [width, setWidth] = useState("0.25"),
    [terminal, setTerminal] = useState<PinRef | null>(null),
    [waypoints, setWaypoints] = useState<Point[]>([]);
  const [busy, setBusy] = useState(false),
    [result, setResult] = useState<Result | null>(null),
    [message, setMessage] = useState(""),
    [newNet, setNewNet] = useState(""),
    [part, setPart] = useState("");
  const [sourceRevision, setSourceRevision] = useState(props.project.revision);
  const [source, setSource] = useState(""),
    [importFormat, setImportFormat] = useState("oxygen"),
    [variant, setVariant] = useState("");
  const [analysis, setAnalysis] = useState("op"),
    [probe, setProbe] = useState("vout"),
    [simSource, setSimSource] = useState("v1"),
    [start, setStart] = useState("10"),
    [stop, setStop] = useState("10000"),
    [step, setStep] = useState("0.00001");
  const svg = useRef<SVGSVGElement>(null),
    drag = useRef<{ id: string; board: boolean; moved: boolean } | null>(null),
    current = useRef(doc);
  current.current = doc;
  useEffect(() => {
    setDoc(
      (props.domainData.schema_version ? props.domainData : empty) as Data,
    );
    setTerminal(null);
    setWaypoints([]);
  }, [props.domainData, props.project.revision]);
  const board = tab === "Board",
    c = doc.components.find((c) => c.id === selected),
    placement = doc.board.placements.find((p) => p.component_id === selected);
  const getPart = (c: Component) =>
    doc.library.find(
      (p) => p.id === c.library_id && p.version === c.library_version,
    )!;
  const pinPoint = (ref: PinRef, onBoard = board): Point => {
    const component = doc.components.find((c) => c.id === ref.component_id)!,
      lib = getPart(component);
    if (onBoard) {
      const place = doc.board.placements.find(
          (p) => p.component_id === component.id,
        ),
        pad = lib.pads.find((p) => p.pin_id === ref.pin_id);
      return place && pad ? position(pad.at, place, true) : [0, 0];
    }
    return position(
      lib.pins.find((p) => p.id === ref.pin_id)!.at,
      component,
      false,
    );
  };
  const pinNet = (ref: PinRef) =>
    doc.nets.find((n) => n.members.some((m) => refkey(m) === refkey(ref)));
  function sourceText(value: string) {
    setSource(value);
    setSourceRevision(props.project.revision);
  }
  function sourceCurrent() {
    if (sourceRevision !== props.project.revision) {
      setMessage(
        "Source revision conflict. Reload current source and reconcile your edits.",
      );
      return false;
    }
    return true;
  }
  function fail(error: unknown) {
    const text = error instanceof Error ? error.message : String(error);
    setMessage(text);
    props.setError(text);
  }
  async function save(next: Data) {
    if (busy) return;
    setBusy(true);
    setMessage("");
    try {
      await props.saveDomain("electronics", next as unknown as JsonObject);
      setDoc(next);
    } catch (error) {
      fail(error);
      await props.refresh();
    } finally {
      setBusy(false);
    }
  }
  async function run(operation: string, input: JsonObject = {}) {
    if (busy) return;
    setBusy(true);
    setMessage("");
    try {
      const response = await props.runDomain("electronics", operation, {
        ...input,
        actor: "human",
      });
      setResult(response as Result);
      if (response.changed && response.data) setDoc(response.data as Data);
      return response as Result;
    } catch (error) {
      fail(error);
    } finally {
      setBusy(false);
    }
  }
  function moveWireEnds(next: Data, componentId: string) {
    const component = next.components.find((c) => c.id === componentId)!;
    for (const w of next.wires)
      for (const which of ["start", "end"] as const) {
        const ref = w[which];
        if (ref?.component_id === componentId)
          w.points[which === "start" ? 0 : w.points.length - 1] = position(
            getPart(component).pins.find((p) => p.id === ref.pin_id)!.at,
            component,
            false,
          );
      }
  }
  function updateComponent(fields: Partial<Component>) {
    if (!c) return;
    const next = clone(doc);
    Object.assign(
      next.components.find((x) => x.id === c.id)!,
      fields,
    );
    if (fields.at || fields.rotation) moveWireEnds(next, c.id);
    void save(next);
  }
  function updatePlacement(fields: Partial<Placement>) {
    if (!placement) return;
    const next = clone(doc);
    Object.assign(
      next.board.placements.find((p) => p.id === placement.id)!,
      fields,
    );
    void save(next);
  }
  function location(event: ReactPointerEvent): Point {
    const matrix = svg.current?.getScreenCTM();
    if (!matrix) return [0, 0];
    const p = new DOMPoint(event.clientX, event.clientY).matrixTransform(
      matrix.inverse(),
    );
    return [Math.round(p.x * 2) / 2, Math.round(p.y * 2) / 2];
  }
  function begin(event: ReactPointerEvent, component: Component) {
    event.stopPropagation();
    setSelected(component.id);
    if (tool !== "Select" || busy) return;
    const place = doc.board.placements.find(
      (p) => p.component_id === component.id,
    );
    if (board ? place?.locked : component.locked) return;
    drag.current = { id: component.id, board, moved: false };
    svg.current?.setPointerCapture(event.pointerId);
  }
  function move(event: ReactPointerEvent) {
    const d = drag.current;
    if (!d || busy) return;
    const next = clone(current.current),
      obj = d.board
        ? next.board.placements.find((p) => p.component_id === d.id)
        : next.components.find((c) => c.id === d.id);
    if (obj) {
      obj.at = location(event);
      d.moved = true;
      current.current = next;
      setDoc(next);
    }
  }
  function end() {
    const d = drag.current;
    drag.current = null;
    if (!d?.moved) return;
    const next = clone(current.current);
    if (!d.board) moveWireEnds(next, d.id);
    void save(next);
  }
  async function clickPin(ref: PinRef) {
    if (busy) return;
    setSelected(ref.component_id);
    if (tool === "Select") {
      const n = pinNet(ref);
      if (n) setNet(n.id);
      return;
    }
    if (!terminal) {
      setTerminal(ref);
      setWaypoints([pinPoint(ref)]);
      if (!net && pinNet(ref)) setNet(pinNet(ref)!.id);
      return;
    }
    if (refkey(ref) === refkey(terminal)) return;
    const target = doc.nets.find((n) => n.id === net);
    if (!target) {
      setMessage("Choose or create a net first.");
      return;
    }
    if ([terminal, ref].some((p) => pinNet(p) && pinNet(p)!.id !== target.id)) {
      setMessage(
        "Pin belongs to another net. Disconnect it explicitly before reconnecting.",
      );
      return;
    }
    if (tool === "Walk route")
      await run("route", {
        id: id("route"),
        net_id: net,
        layer,
        width: Number(width),
        start: terminal,
        end: ref,
        grid: 0.5,
      });
    else {
      const next = clone(doc),
        n = next.nets.find((n) => n.id === net)!;
      for (const p of [terminal, ref])
        if (!n.members.some((m) => refkey(m) === refkey(p))) n.members.push(p);
      if (board)
        next.board.tracks.push({
          id: id("track"),
          net_id: net,
          layer,
          points: [...waypoints, pinPoint(ref)],
          width: Number(width),
          locked: false,
        });
      else
        next.wires.push({
          id: id("wire"),
          sheet_id: sheet,
          net_id: net,
          points: [...waypoints, pinPoint(ref)],
          start: terminal,
          end: ref,
          junction_ids: [],
        });
      await save(next);
    }
    setTerminal(null);
    setWaypoints([]);
  }
  function addComponent() {
    const lib = doc.library.find((p) => `${p.id}@${p.version}` === part);
    if (!lib) {
      setMessage("Choose a library part.");
      return;
    }
    const next = clone(doc),
      cid = id("component");
    let i = 1;
    while (next.components.some((c) => c.reference === `U${i}`)) i++;
    next.components.push({
      id: cid,
      sheet_id: sheet,
      library_id: lib.id,
      library_version: lib.version,
      reference: `U${i}`,
      value: "",
      at: [15, 20],
      rotation: 0,
      locked: false,
    });
    next.board.placements.push({
      id: id("placement"),
      component_id: cid,
      at: [15, 20],
      side: "top",
      rotation: 0,
      locked: false,
    });
    setSelected(cid);
    void save(next);
  }
  function addNet() {
    if (!newNet.trim()) return;
    const next = clone(doc),
      nid = id("net");
    next.nets.push({
      id: nid,
      sheet_id: sheet,
      name: newNet.trim(),
      global_name: false,
      members: [],
    });
    setNet(nid);
    setNewNet("");
    void save(next);
  }
  function disconnect(ref: PinRef) {
    const next = clone(doc);
    for (const n of next.nets)
      n.members = n.members.filter((m) => refkey(m) !== refkey(ref));
    next.wires = next.wires.filter(
      (w) => ![w.start, w.end].some((p) => p && refkey(p) === refkey(ref)),
    );
    void save(next);
  }
  function selectFinding(objects: string[]) {
    const match =
      doc.components.find((c) =>
        objects.some((o) => o === c.id || o.startsWith(c.id + "/")),
      ) ||
      doc.components.find((c) =>
        doc.board.placements.some(
          (p) =>
            p.component_id === c.id &&
            objects.some((o) => o === p.id || o.startsWith(p.id + "/")),
        ),
      );
    if (match) setSelected(match.id);
  }
  const selectedTrack = doc.board.tracks.find((t) => t.id === selected),
    copperLayers = doc.board.stackup.filter((l) => l.kind === "copper");
  const svgPoints = board
      ? doc.board.outline
      : doc.components.filter((c) => c.sheet_id === sheet).map((c) => c.at),
    xs = svgPoints.map((p) => p[0]),
    ys = svgPoints.map((p) => p[1]);
  const minx = Math.min(0, ...xs) - 6,
    miny = Math.min(0, ...ys) - 6,
    maxx = Math.max(board ? 40 : 55, ...xs) + 8,
    maxy = Math.max(30, ...ys) + 8;
  const stale =
    !!result &&
    result.revision !== undefined &&
    result.revision !== props.project.revision;
  const field = (
    label: string,
    value: string | number,
    onChange: (value: string) => void,
    type = "text",
  ) => (
    <label>
      {label}
      <Input
        aria-label={label}
        type={type}
        defaultValue={value}
        key={`${selected}-${label}-${value}`}
        onBlur={(e) => {
          if (e.target.value !== String(value)) onChange(e.target.value);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") e.currentTarget.blur();
        }}
        disabled={busy}
      />
    </label>
  );
  return (
    <section className="electronics-panel" aria-label="Electronics workspace">
      <div className="eda-toolbar">
        <h2>Electronics</h2>
        <small>
          Shared electrical source · revision {props.project.revision}
        </small>
        {busy && <span role="status">Working…</span>}
      </div>
      {!doc.sheets.length ? (
        <>
          <p>
            Create an original four-layer RC board with a cutout, plated slot,
            bottom component and DNP variant. The example starts unrouted and
            uses illustrative footprints.
          </p>
          <Button disabled={busy} onClick={() => void run("example")}>
            Create RC example
          </Button>
        </>
      ) : (
        <>
          <div className="eda-summary">
            <span>{doc.components.length} components</span>
            <span>{doc.nets.length} nets</span>
            <span>{copperLayers.length} copper layers</span>
            <span>
              {doc.board.stackup
                .reduce((sum, l) => sum + l.thickness, 0)
                .toFixed(3)}{" "}
              mm
            </span>
            <span>
              {doc.library.filter((p) => !p.model3d).length} missing 3D models
            </span>
          </div>
          <div
            className="eda-tabs"
            role="tablist"
            aria-label="Electrical editors"
          >
            {[
              "Schematic",
              "Board",
              "Library",
              "Rules",
              "Simulation",
              "Production",
              "Source",
            ].map((t) => (
              <Button
                key={t}
                role="tab"
                aria-selected={tab === t}
                onClick={() => {
                  setTab(t);
                  setTool("Select");
                  setTerminal(null);
                  setWaypoints([]);
                }}
              >
                {t}
              </Button>
            ))}
          </div>
          {(tab === "Schematic" || board) && (
            <>
              <div className="eda-toolbar">
                {(board
                  ? ["Select", "Trace", "Walk route"]
                  : ["Select", "Wire"]
                ).map((t) => (
                  <Button
                    key={t}
                    aria-pressed={tool === t}
                    onClick={() => {
                      setTool(t);
                      setTerminal(null);
                      setWaypoints([]);
                    }}
                  >
                    {t}
                  </Button>
                ))}
                <label>
                  Sheet
                  <SelectControl
                    aria-label="Schematic sheet"
                    value={sheet}
                    onChange={(e) => setSheet(e.target.value)}
                  >
                    {doc.sheets.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name}
                      </option>
                    ))}
                  </SelectControl>
                </label>
                <label>
                  Net
                  <SelectControl
                    aria-label="Active net"
                    value={net}
                    onChange={(e) => setNet(e.target.value)}
                  >
                    <option value="">Choose net</option>
                    {doc.nets
                      .filter((n) => board || n.sheet_id === sheet)
                      .map((n) => (
                        <option key={n.id} value={n.id}>
                          {n.name}
                        </option>
                      ))}
                  </SelectControl>
                </label>
                {board && (
                  <>
                    <label>
                      Layer
                      <SelectControl
                        aria-label="Copper layer"
                        value={layer}
                        onChange={(e) => setLayer(e.target.value)}
                      >
                        {copperLayers.map((l) => (
                          <option key={l.id}>{l.id}</option>
                        ))}
                      </SelectControl>
                    </label>
                    <label>
                      Width mm
                      <Input
                        aria-label="Trace width"
                        type="number"
                        step="0.05"
                        min="0.05"
                        value={width}
                        onChange={(e) => setWidth(e.target.value)}
                      />
                    </label>
                  </>
                )}
                <Button
                  disabled={busy}
                  onClick={() => void run(board ? "drc" : "erc")}
                >
                  {board ? "Run DRC" : "Run ERC"}
                </Button>
              </div>
              <p>
                {tool === "Select"
                  ? "Drag components; select pins to cross-probe their nets."
                  : `${tool}: select net, click first pin, add bends, click destination pin.`}{" "}
                {terminal && `Start: ${refkey(terminal)}`}
              </p>
              <div className="eda-editor">
                <svg
                  ref={svg}
                  className="eda-canvas"
                  role="img"
                  aria-label={
                    board
                      ? "Editable PCB layout"
                      : "Editable electrical schematic"
                  }
                  viewBox={`${minx} ${miny} ${maxx - minx} ${maxy - miny}`}
                  onPointerMove={move}
                  onPointerUp={end}
                  onPointerCancel={() => {
                    drag.current = null;
                  }}
                  onPointerDown={(e) => {
                    if (
                      e.target === e.currentTarget &&
                      terminal &&
                      tool !== "Walk route"
                    )
                      setWaypoints([...waypoints, location(e)]);
                  }}
                >
                  <defs>
                    <pattern
                      id="eda-grid"
                      width="2"
                      height="2"
                      patternUnits="userSpaceOnUse"
                    >
                      <path
                        d="M2 0 L0 0 0 2"
                        className="eda-grid"
                        fill="none"
                      />
                    </pattern>
                  </defs>
                  <rect
                    x={minx}
                    y={miny}
                    width={maxx - minx}
                    height={maxy - miny}
                    fill="url(#eda-grid)"
                    pointerEvents="none"
                  />
                  {board && (
                    <>
                      <path
                        className="eda-outline"
                        pointerEvents="none"
                        d={[doc.board.outline, ...doc.board.cutouts]
                          .map(
                            (p) =>
                              "M" + p.map((v) => v.join(",")).join("L") + "Z",
                          )
                          .join(" ")}
                      />
                      {doc.board.holes.map((h) => (
                        <g key={h.id} pointerEvents="none">
                          <circle
                            cx={h.at[0]}
                            cy={h.at[1]}
                            r={h.diameter / 2}
                            fill="var(--viewport-bg)"
                            stroke="var(--text-muted)"
                            strokeWidth=".1"
                          />
                          {h.slot && (
                            <line
                              x1={h.at[0]}
                              y1={h.at[1]}
                              x2={h.at[0] + h.slot[0]}
                              y2={h.at[1] + h.slot[1]}
                              stroke="var(--viewport-bg)"
                              strokeWidth={h.diameter}
                              strokeLinecap="round"
                            />
                          )}
                        </g>
                      ))}
                      {doc.board.zones.map((z) => (
                        <polygon
                          key={z.id}
                          points={points(z.polygon)}
                          fill="none"
                          stroke="var(--danger)"
                          strokeWidth=".1"
                          strokeDasharray=".5 .5"
                        />
                      ))}
                      {doc.nets
                        .filter((n) => !net || n.id === net)
                        .map((n) => {
                          const members = n.members.filter((m) =>
                            doc.board.placements.some(
                              (p) => p.component_id === m.component_id,
                            ),
                          );
                          return members
                            .slice(1)
                            .map((m, i) => (
                              <line
                                key={`${n.id}-${i}`}
                                x1={pinPoint(members[0], true)[0]}
                                y1={pinPoint(members[0], true)[1]}
                                x2={pinPoint(m, true)[0]}
                                y2={pinPoint(m, true)[1]}
                                className="eda-ratsnest"
                              />
                            ));
                        })}
                      {doc.board.tracks.map((t) => (
                        <polyline
                          data-track-id={t.id}
                          key={t.id}
                          points={points(t.points)}
                          className="eda-track"
                          fill="none"
                          stroke={
                            t.id === selected
                              ? "var(--text)"
                              : t.layer === "F.Cu"
                                ? "var(--warning)"
                                : "var(--text-muted)"
                          }
                          strokeWidth={t.width}
                          opacity={t.layer === layer ? 1 : 0.45}
                          onPointerDown={(e) => {
                            e.stopPropagation();
                            setSelected(t.id);
                            setNet(t.net_id);
                          }}
                        />
                      ))}
                      {doc.board.vias.map((v) => (
                        <g key={v.id}>
                          <circle
                            cx={v.at[0]}
                            cy={v.at[1]}
                            r={v.diameter / 2}
                            fill="var(--warning)"
                          />
                          <circle
                            cx={v.at[0]}
                            cy={v.at[1]}
                            r={v.drill / 2}
                            fill="var(--viewport-bg)"
                          />
                        </g>
                      ))}
                    </>
                  )}
                  {!board &&
                    doc.wires
                      .filter((w) => w.sheet_id === sheet)
                      .map((w) => (
                        <polyline
                          key={w.id}
                          points={points(w.points)}
                          className="eda-wire"
                          onPointerDown={(e) => {
                            e.stopPropagation();
                            setNet(w.net_id);
                          }}
                        />
                      ))}
                  {doc.components
                    .filter((c) =>
                      board
                        ? doc.board.placements.some(
                            (p) => p.component_id === c.id,
                          )
                        : c.sheet_id === sheet,
                    )
                    .map((component) => {
                      const lib = getPart(component),
                        loc = board
                          ? doc.board.placements.find(
                              (p) => p.component_id === component.id,
                            )!
                          : component;
                      return (
                        <g
                          key={component.id}
                          data-component-id={component.id}
                          className={`eda-component ${selected === component.id ? "selected" : ""}`}
                          onPointerDown={(e) => begin(e, component)}
                        >
                          <rect
                            className="eda-silk"
                            x={loc.at[0] - lib.body_size[0] / 2}
                            y={loc.at[1] - lib.body_size[1] / 2}
                            width={lib.body_size[0]}
                            height={lib.body_size[1]}
                            transform={`rotate(${loc.rotation} ${loc.at.join(" ")})`}
                          />
                          <text
                            x={loc.at[0]}
                            y={loc.at[1] - 2.4}
                            className="eda-label"
                            textAnchor="middle"
                          >
                            {component.reference} {component.value}
                            {loc.locked ? " 🔒" : ""}
                          </text>
                          {(board ? lib.pads : lib.pins).map((p) => {
                            const pinid = "pin_id" in p ? p.pin_id : p.id,
                              ref = {
                                component_id: component.id,
                                pin_id: pinid,
                              },
                              pos = position(p.at, loc, board),
                              connected = pinNet(ref),
                              cls = `eda-pin ${connected ? "connected" : ""} ${connected?.id === net ? "highlight" : ""}`;
                            return (
                              <g
                                key={p.id}
                                data-pin-id={refkey(ref)}
                                onPointerDown={(e) => {
                                  e.stopPropagation();
                                  void clickPin(ref);
                                }}
                              >
                                {board && "size" in p ? (
                                  <rect
                                    x={pos[0] - p.size[0] / 2}
                                    y={pos[1] - p.size[1] / 2}
                                    width={p.size[0]}
                                    height={p.size[1]}
                                    rx={
                                      p.shape === "circle"
                                        ? p.size[0] / 2
                                        : 0.08
                                    }
                                    transform={`rotate(${loc.rotation + (board && "side" in loc && loc.side === "bottom" ? -p.rotation : p.rotation)} ${pos.join(" ")})`}
                                    className={cls}
                                  />
                                ) : (
                                  <circle
                                    cx={pos[0]}
                                    cy={pos[1]}
                                    r=".55"
                                    className={cls}
                                  />
                                )}
                                <text
                                  x={pos[0]}
                                  y={pos[1] + 1.6}
                                  textAnchor="middle"
                                  className="eda-pin-label"
                                >
                                  {p.number}
                                  {connected ? " " + connected.name : ""}
                                </text>
                              </g>
                            );
                          })}
                        </g>
                      );
                    })}
                  {terminal && (
                    <polyline
                      points={points(waypoints)}
                      className="eda-wire"
                      strokeDasharray=".6 .3"
                    />
                  )}
                </svg>
                <div className="eda-properties">
                  {c ? (
                    <>
                      <h3>
                        {c.reference} · {c.id}
                      </h3>
                      {field("Reference", c.reference, (v) =>
                        updateComponent({ reference: v }),
                      )}
                      {field("Value", c.value, (v) =>
                        updateComponent({ value: v }),
                      )}
                      <p>
                        {getPart(c).name} @{c.library_version} · 3D{" "}
                        {getPart(c).model3d
                          ? "declared; unresolved"
                          : "missing"}
                      </p>
                      <div className="eda-fields">
                        {field(
                          "X mm",
                          (board ? placement : c)?.at[0] ?? 0,
                          (v) =>
                            board
                              ? updatePlacement({
                                  at: [Number(v), placement!.at[1]],
                                })
                              : updateComponent({ at: [Number(v), c.at[1]] }),
                          "number",
                        )}
                        {field(
                          "Y mm",
                          (board ? placement : c)?.at[1] ?? 0,
                          (v) =>
                            board
                              ? updatePlacement({
                                  at: [placement!.at[0], Number(v)],
                                })
                              : updateComponent({ at: [c.at[0], Number(v)] }),
                          "number",
                        )}
                        {field(
                          "Rotation deg",
                          (board ? placement : c)?.rotation ?? 0,
                          (v) =>
                            board
                              ? updatePlacement({ rotation: Number(v) })
                              : updateComponent({ rotation: Number(v) }),
                          "number",
                        )}
                      </div>
                      {board && placement && (
                        <label>
                          Board side
                          <SelectControl
                            aria-label="Board side"
                            value={placement.side}
                            disabled={busy}
                            onChange={(e) =>
                              updatePlacement({
                                side: e.target.value as Placement["side"],
                              })
                            }
                          >
                            <option value="top">Top</option>
                            <option value="bottom">Bottom</option>
                          </SelectControl>
                        </label>
                      )}
                      <Button
                        disabled={busy}
                        aria-pressed={(board ? placement : c)?.locked}
                        onClick={() =>
                          board
                            ? updatePlacement({ locked: !placement?.locked })
                            : updateComponent({ locked: !c.locked })
                        }
                      >
                        {(board ? placement : c)?.locked ? "Unlock" : "Lock"}{" "}
                        {board ? "footprint" : "component"}
                      </Button>
                      <table>
                        <thead>
                          <tr>
                            <th>Pin</th>
                            <th>Net</th>
                            <th />
                          </tr>
                        </thead>
                        <tbody>
                          {getPart(c).pins.map((p) => {
                            const ref = { component_id: c.id, pin_id: p.id };
                            return (
                              <tr key={p.id}>
                                <td>
                                  {p.number} {p.name}
                                </td>
                                <td>{pinNet(ref)?.name ?? "Unconnected"}</td>
                                <td>
                                  <Button
                                    disabled={busy || !pinNet(ref)}
                                    onClick={() => disconnect(ref)}
                                  >
                                    Disconnect
                                  </Button>
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </>
                  ) : selectedTrack ? (
                    <>
                      <h3>Trace {selectedTrack.id}</h3>
                      <p>
                        {selectedTrack.layer} · {selectedTrack.width} mm
                      </p>
                      <Button
                        disabled={busy}
                        onClick={() => {
                          const next = clone(doc);
                          next.board.tracks = next.board.tracks.filter(
                            (t) => t.id !== selectedTrack.id,
                          );
                          setSelected("");
                          void save(next);
                        }}
                      >
                        Rip up selected trace
                      </Button>
                      <Button
                        disabled={busy}
                        onClick={() => {
                          const next = clone(doc);
                          next.board.tracks.find(
                            (t) => t.id === selectedTrack.id,
                          )!.locked = !selectedTrack.locked;
                          void save(next);
                        }}
                      >
                        {selectedTrack.locked ? "Unlock" : "Lock"} trace
                      </Button>
                    </>
                  ) : (
                    <p>
                      Select a component, pad or trace to inspect its identity.
                    </p>
                  )}
                  <label>
                    Library part
                    <SelectControl
                      aria-label="Library part"
                      value={part}
                      onChange={(e) => setPart(e.target.value)}
                    >
                      <option value="">Choose part</option>
                      {doc.library.map((p) => (
                        <option
                          key={`${p.id}@${p.version}`}
                          value={`${p.id}@${p.version}`}
                        >
                          {p.name} @{p.version}
                        </option>
                      ))}
                    </SelectControl>
                  </label>
                  <Button disabled={busy} onClick={addComponent}>
                    Place component
                  </Button>
                  <label>
                    New net
                    <Input
                      aria-label="New net name"
                      value={newNet}
                      onChange={(e) => setNewNet(e.target.value)}
                    />
                  </label>
                  <Button disabled={busy || !newNet.trim()} onClick={addNet}>
                    Create net
                  </Button>
                </div>
              </div>
            </>
          )}
          {tab === "Library" && (
            <div className="eda-section">
              <p>
                Versioned symbol → pin → pad → model identity. Library
                substitutions reject reversed mappings and reduced ratings.
              </p>
              <table>
                <thead>
                  <tr>
                    <th>Part/version</th>
                    <th>MPN</th>
                    <th>Pins/pads</th>
                    <th>3D</th>
                  </tr>
                </thead>
                <tbody>
                  {doc.library.map((p) => (
                    <tr key={`${p.id}@${p.version}`}>
                      <td>
                        <Button
                          onClick={() => sourceText(JSON.stringify(p, null, 2))}
                        >
                          {p.name} @{p.version}
                        </Button>
                      </td>
                      <td>{p.mpn || "Missing"}</td>
                      <td>
                        {p.pins.length}/{p.pads.length}
                      </td>
                      <td>{p.model3d ? "Declared; unresolved" : "Missing"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <label>
                Editable library record
                <textarea
                  aria-label="Library record"
                  value={source}
                  onChange={(e) => setSource(e.target.value)}
                />
              </label>
              <Button
                disabled={busy}
                onClick={() => {
                  try {
                    if (!sourceCurrent()) return;
                    const p = JSON.parse(source);
                    void run("edit", {
                      changes: [
                        {
                          collection: "library",
                          action: "upsert",
                          id: p.id,
                          version: p.version,
                          value: p,
                        },
                      ],
                    });
                  } catch (error) {
                    fail(error);
                  }
                }}
              >
                Save library record
              </Button>
              <Button
                disabled={busy}
                onClick={() =>
                  void run("package", {
                    id: id("soic16"),
                    version: "1",
                    name: "SOIC-16 dimensional example",
                    pin_count: 16,
                    pitch: 1.27,
                    body_size: [3.9, 9.9],
                    row_spacing: 5.4,
                    pad_size: [1.55, 0.6],
                    source:
                      "Illustrative user dimensions; replace with attributed datasheet",
                    licence: "MIT",
                  })
                }
              >
                Generate SOIC-16 example
              </Button>
            </div>
          )}
          {tab === "Rules" && (
            <div className="eda-section">
              <h3>Physical stackup</h3>
              <table>
                <thead>
                  <tr>
                    <th>Layer</th>
                    <th>Kind</th>
                    <th>Thickness mm</th>
                    <th>Material</th>
                  </tr>
                </thead>
                <tbody>
                  {doc.board.stackup.map((l) => (
                    <tr key={l.id}>
                      <td>{l.id}</td>
                      <td>{l.kind}</td>
                      <td>{l.thickness}</td>
                      <td>{l.material}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <h3>{doc.process.name}</h3>
              <p>{doc.process.source}</p>
              <div className="eda-fields">
                {(
                  [
                    "min_width",
                    "clearance",
                    "min_drill",
                    "annular_ring",
                    "edge_clearance",
                    "mask_web",
                  ] as const
                ).map((k) => (
                  <label key={k}>
                    {k} mm
                    <Input
                      aria-label={k}
                      type="number"
                      step="0.05"
                      defaultValue={doc.process[k]}
                      onBlur={(e) => {
                        if (Number(e.target.value) !== doc.process[k]) {
                          const next = clone(doc);
                          next.process[k] = Number(e.target.value);
                          void save(next);
                        }
                      }}
                    />
                  </label>
                ))}
              </div>
              <p>
                {doc.rules.length} scoped rules. Higher priority overrides lower
                priority. Stackup, outlines, keepouts, pair rules and waivers
                are editable in Source.
              </p>
              <div className="eda-toolbar">
                <Button disabled={busy} onClick={() => void run("drc")}>
                  Check local rules
                </Button>
                <Button disabled={busy} onClick={() => void run("kicad_drc")}>
                  Independent KiCad DRC
                </Button>
                <Button disabled={busy} onClick={() => void run("mcad")}>
                  Inspect ECAD–MCAD identities
                </Button>
              </div>
            </div>
          )}
          {tab === "Simulation" && (
            <div className="eda-section">
              <p>
                Runs installed ngspice with explicit pin mappings and complete
                models. Missing models and solver failures remain errors.
              </p>
              <div className="eda-fields">
                <label>
                  Analysis
                  <SelectControl
                    aria-label="Circuit analysis"
                    value={analysis}
                    onChange={(e) => setAnalysis(e.target.value)}
                  >
                    {["op", "dc", "ac", "tran"].map((a) => (
                      <option key={a}>{a}</option>
                    ))}
                  </SelectControl>
                </label>
                <label>
                  Probe net
                  <SelectControl
                    aria-label="Probe net"
                    value={probe}
                    onChange={(e) => setProbe(e.target.value)}
                  >
                    {doc.nets.map((n) => (
                      <option key={n.id} value={n.id}>
                        {n.name}
                      </option>
                    ))}
                  </SelectControl>
                </label>
                <label>
                  Source
                  <SelectControl
                    aria-label="Circuit source"
                    value={simSource}
                    onChange={(e) => setSimSource(e.target.value)}
                  >
                    {doc.components
                      .filter((c) =>
                        ["V", "I"].includes(getPart(c).spice?.kind ?? ""),
                      )
                      .map((c) => (
                        <option key={c.id} value={c.id}>
                          {c.reference}
                        </option>
                      ))}
                  </SelectControl>
                </label>
                <label>
                  Start (V or Hz)
                  <Input
                    aria-label="Sweep start"
                    value={start}
                    onChange={(e) => setStart(e.target.value)}
                  />
                </label>
                <label>
                  Stop (V, Hz or s)
                  <Input
                    aria-label="Sweep stop"
                    value={stop}
                    onChange={(e) => setStop(e.target.value)}
                  />
                </label>
                <label>
                  Step (V or s)
                  <Input
                    aria-label="Sweep step"
                    value={step}
                    onChange={(e) => setStep(e.target.value)}
                  />
                </label>
              </div>
              <Button
                disabled={busy}
                onClick={() =>
                  void run("simulate", {
                    analysis,
                    probes: [probe],
                    source: simSource,
                    start: Number(start),
                    stop: Number(stop),
                    step: Number(step),
                    points: 30,
                    ac_sources: { [simSource]: "1" },
                    ...(analysis === "tran"
                      ? {
                          pulses: {
                            [simSource]: ["0", "5", "0", "1n", "1n", "1", "2"],
                          },
                        }
                      : {}),
                  })
                }
              >
                Solve circuit
              </Button>
              {result?.rows && result.rows.length > 0 && (
                <TracePlot result={result} />
              )}
            </div>
          )}
          {tab === "Production" && (
            <div className="eda-section">
              <label>
                Assembly variant
                <SelectControl
                  aria-label="Assembly variant"
                  value={variant}
                  onChange={(e) => setVariant(e.target.value)}
                >
                  <option value="">Base</option>
                  {doc.variants.map((v) => (
                    <option key={v.id} value={v.id}>
                      {v.name}
                    </option>
                  ))}
                </SelectControl>
              </label>
              <p>
                KiCad produces copper/mask/paste/outline Gerbers and separate
                PTH/NPTH drills. Releases with check findings remain blocked.
                Supplier qualification remains separate.
              </p>
              <div className="eda-toolbar">
                {[
                  ["assembly", "Inspect BOM and placement"],
                  ["export", "Editable source package"],
                  ["fabrication", "Export fabrication"],
                  ["release", "Build release package"],
                  ["export_step", "Export board STEP"],
                ].map(([op, label]) => (
                  <Button
                    key={op}
                    disabled={busy}
                    onClick={() => void run(op, variant ? { variant } : {})}
                  >
                    {label}
                  </Button>
                ))}
              </div>
              {result?.bom !== undefined && (
                <pre>
                  {JSON.stringify(
                    { bom: result.bom, positions: result.positions },
                    null,
                    2,
                  )}
                </pre>
              )}
            </div>
          )}
          {tab === "Source" && (
            <div className="eda-section">
              <p>
                Advanced authoring uses the same schema and revision-safe save
                as visual editing. Hierarchy, buses, models, stackup, outlines,
                connector mates and harness routes remain inspectable.
              </p>
              <Button onClick={() => sourceText(JSON.stringify(doc, null, 2))}>
                Load current source
              </Button>
              <label>
                Electrical source or import
                <textarea
                  aria-label="Electrical source"
                  value={source}
                  onChange={(e) => setSource(e.target.value)}
                />
              </label>
              <label>
                Editable format
                <SelectControl
                  aria-label="Import format"
                  value={importFormat}
                  onChange={(e) => setImportFormat(e.target.value)}
                >
                  <option value="oxygen">Oxygen electrical JSON</option>
                  <option value="kicad_board">KiCad board</option>
                </SelectControl>
              </label>
              <div className="eda-toolbar">
                <Button
                  disabled={busy}
                  onClick={() => {
                    if (sourceCurrent())
                      void run("import", {
                        format: importFormat,
                        text: source,
                      });
                  }}
                >
                  {importFormat === "oxygen"
                    ? "Apply electrical source"
                    : "Preview KiCad import"}
                </Button>
                {result?.candidate && (
                  <Button
                    disabled={busy}
                    onClick={() => {
                      if (sourceCurrent()) void save(result.candidate!);
                    }}
                  >
                    Accept reviewed translation
                  </Button>
                )}
                <Button disabled={busy} onClick={() => void run("inspect")}>
                  Inspect flattened netlist
                </Button>
              </div>
              {result?.loss_report && (
                <div className="eda-findings">
                  {result.loss_report.findings.map((f, i) => (
                    <p key={i}>
                      {f.severity}: {f.message}
                    </p>
                  ))}
                </div>
              )}
            </div>
          )}
        </>
      )}
      {message && (
        <p className="eda-error" role="alert">
          {message}
        </p>
      )}
      {result && (
        <div className={`eda-status ${stale ? "stale" : ""}`} role="status">
          {stale ? "Stale result — design revision changed. " : ""}
          {result.state ?? "Completed"} · revision{" "}
          {result.revision ?? props.project.revision}
          {typeof result.source_hash === "string" && (
            <> · source {result.source_hash.slice(0, 12)}</>
          )}
        </div>
      )}
      {result?.findings && (
        <div className="eda-findings" aria-label="Electrical findings">
          {!result.findings.length ? (
            <p>No findings in checked rule set.</p>
          ) : (
            result.findings.map((f) => (
              <div className="eda-finding" key={f.id}>
                <Button onClick={() => selectFinding(f.objects)}>
                  {f.waived ? "Waived" : f.severity}: {f.code}
                  <br />
                  {f.message}
                  <br />
                  {f.objects.join(", ")}
                </Button>
              </div>
            ))
          )}
        </div>
      )}
      {result?.manifest != null &&
        typeof result.manifest === "object" &&
        "review_state" in result.manifest &&
        result.manifest.review_state === "blocked_by_findings" && (
          <p className="eda-error">
            Production blocked by check findings. Review ERC/DRC reports before
            release.
          </p>
        )}
      {result?.artifacts && (
        <div className="eda-artifacts">
          {result.artifacts.map((a) => (
            <a
              key={a.path}
              href={
                "/api/artifacts/" +
                a.path.split("/").map(encodeURIComponent).join("/")
              }
              download
            >
              {a.name}
            </a>
          ))}
        </div>
      )}
      {result &&
        !result.findings &&
        !result.rows &&
        !result.artifacts &&
        result.bom === undefined && (
          <details>
            <summary>Result details</summary>
            <pre>{JSON.stringify(result, null, 2)}</pre>
          </details>
        )}
    </section>
  );
}
function TracePlot({ result }: { result: Result }) {
  const allRows = result.rows!,
    stride = Math.max(1, Math.ceil(allRows.length / 2000)),
    rows = allRows.filter((_, i) => i % stride === 0),
    columns = result.columns!,
    complex = result.analysis === "ac",
    ys = rows.map((r) => (complex ? Math.hypot(r[1], r[2]) : r[1])),
    xs = rows.map((r) => (complex ? Math.log10(r[0]) : r[0])),
    minx = Math.min(...xs),
    maxx = Math.max(...xs),
    miny = Math.min(0, ...ys),
    maxy = Math.max(...ys),
    plot = rows.map((_, i) => [
      30 + ((xs[i] - minx) / (maxx - minx || 1)) * 530,
      180 - ((ys[i] - miny) / (maxy - miny || 1)) * 155,
    ]);
  return (
    <>
      <svg
        className="eda-plot"
        viewBox="0 0 600 210"
        role="img"
        aria-label="Solved circuit probe plot"
      >
        <path d="M30 20 V180 H570" fill="none" stroke="var(--text-muted)" />
        <polyline
          points={plot.map((p) => p.join(",")).join(" ")}
          fill="none"
          stroke="var(--success)"
          strokeWidth="2"
        />
        <text x="35" y="205" fill="var(--text-secondary)" fontSize="11">
          {complex ? "log₁₀ frequency (Hz)" : columns[0]} · {rows.length} solved
          samples
        </text>
        <text x="35" y="15" fill="var(--text-secondary)" fontSize="11">
          {complex ? "Voltage magnitude" : columns[1]} · {maxy.toPrecision(5)}{" "}
          max
        </text>
      </svg>
      <details>
        <summary>Raw solved data</summary>
        <pre>
          {columns.join("\t") +
            "\n" +
            rows
              .slice(0, 500)
              .map((r) => r.join("\t"))
              .join("\n")}
        </pre>
      </details>
    </>
  );
}
