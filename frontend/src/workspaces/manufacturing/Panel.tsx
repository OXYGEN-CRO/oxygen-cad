import { useEffect, useState, type ReactNode } from "react";
import { Button, Input, SelectControl } from "../../components/ui";
import { artifact } from "../../api";
import type { DomainPanelProps, JsonObject } from "../contracts";
import reference from "../../../../examples/manufacturing.json";
import "./manufacturing.css";

type Item = Record<string, any>;
type Process = {
  schema_version: 1;
  machines: Item[];
  tools: Item[];
  setups: Item[];
  operations: Item[];
  nesting?: Item | null;
  printer?: Item | null;
  plastics?: Item | null;
};
const empty: Process = {
  schema_version: 1,
  machines: [],
  tools: [],
  setups: [],
  operations: [],
};
const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value));
const message = (error: unknown) =>
  error instanceof Error ? error.message : String(error);
const pointsText = (points: number[][] | undefined) =>
  (points || []).map((p) => p.join(", ")).join("\n");
function parsePoints(text: string): number[][] {
  return text
    .split(/\n|;/)
    .filter((s) => s.trim())
    .map((row) => {
      const values = row
        .trim()
        .split(/[\s,]+/)
        .map(Number);
      if (values.length !== 2 || values.some((v) => !Number.isFinite(v)))
        throw new Error("Enter one X, Y pair per line");
      return values;
    });
}
function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="manufacturing-field">
      <span>{label}</span>
      {children}
    </label>
  );
}
function NumberField({
  label,
  value,
  onChange,
  step = "any",
}: {
  label: string;
  value: number;
  onChange: (n: number) => void;
  step?: string;
}) {
  return (
    <Field label={label}>
      <Input
        type="number"
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </Field>
  );
}
function VectorField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number[];
  onChange: (n: number[]) => void;
}) {
  return (
    <fieldset className="manufacturing-vector">
      <legend>{label}</legend>
      {value.map((n, i) => (
        <Input
          key={i}
          aria-label={`${label} ${"XYZ"[i]}`}
          type="number"
          step="any"
          value={n}
          onChange={(e) =>
            onChange(
              value.map((v, j) => (j === i ? Number(e.target.value) : v)),
            )
          }
        />
      ))}
    </fieldset>
  );
}
function OutlineField({
  label,
  value,
  onChange,
  setError,
}: {
  label: string;
  value: number[][];
  onChange: (p: number[][]) => void;
  setError: (e: string) => void;
}) {
  const [text, setText] = useState(pointsText(value));
  useEffect(() => setText(pointsText(value)), [JSON.stringify(value)]);
  return (
    <Field label={label}>
      <textarea
        value={text}
        rows={4}
        spellCheck={false}
        placeholder="10, 10&#10;50, 10&#10;50, 40&#10;10, 40"
        onChange={(e) => setText(e.target.value)}
        onBlur={() => {
          try {
            onChange(parsePoints(text));
          } catch (e) {
            setError(message(e));
          }
        }}
      />
    </Field>
  );
}

function Preview({ result }: { result: Item }) {
  const paths = (result.paths || []) as Item[];
  const nest = result.nest;
  const all: number[][] = nest
    ? nest.placements.flatMap((p: Item) => p.boundary)
    : paths.flatMap((p) => p.moves.map((m: Item) => [m.x, m.y]));
  if (!all.length) return null;
  const xs = all.map((p) => p[0]),
    ys = all.map((p) => p[1]);
  const x = xs.reduce((a, b) => Math.min(a, b), Infinity),
    y = ys.reduce((a, b) => Math.min(a, b), Infinity),
    width = xs.reduce((a, b) => Math.max(a, b), -Infinity) - x || 1,
    height = ys.reduce((a, b) => Math.max(a, b), -Infinity) - y || 1;
  const pad = Math.max(width, height) * 0.05;
  return (
    <svg
      className="manufacturing-preview"
      role="img"
      aria-label={nest ? "Nested sheet preview" : "XY toolpath preview"}
      viewBox={`${x - pad} ${y - pad} ${width + 2 * pad} ${height + 2 * pad}`}
    >
      {nest
        ? nest.placements
            .filter((p: Item) => p.sheet === 0)
            .map((p: Item, i: number) => (
              <path
                key={i}
                fillRule="evenodd"
                d={[p.boundary, ...p.holes]
                  .map(
                    (ring: number[][]) =>
                      "M" + ring.map((q) => q.join(",")).join("L") + "Z",
                  )
                  .join(" ")}
              />
            ))
        : paths.map((path, i) => (
            <g key={i}>
              {path.moves.slice(1, 20001).map((m: Item, j: number) => {
                const a = path.moves[j];
                return (
                  <line
                    key={j}
                    x1={a.x}
                    y1={a.y}
                    x2={m.x}
                    y2={m.y}
                    data-motion={m.motion}
                  />
                );
              })}
            </g>
          ))}
    </svg>
  );
}

export default function ManufacturingPanel(props: DomainPanelProps) {
  const { domainData, saveDomain, runDomain, native, setError } = props;
  const [data, setData] = useState<Process>(
    () => ({ ...clone(empty), ...clone(domainData) }) as Process,
  );
  const [tab, setTab] = useState("Machining");
  const [setupId, setSetupId] = useState("");
  const [toolId, setToolId] = useState("");
  const [opId, setOpId] = useState("");
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [result, setResult] = useState<Item | null>(null);
  const [measurements, setMeasurements] = useState(
    "id,nominal,measured,lower_tolerance,upper_tolerance,unit\n",
  );
  const [power, setPower] = useState(200);
  const [probe, setProbe] = useState({
    points: [[20, 20]],
    start: 2,
    seek: -2,
    feed: 50,
  });
  const [snap, setSnap] = useState({
    position: [40, 20, 3],
    hook: [1, 0, 0],
    length: 15,
    thickness: 1.5,
    width: 6,
    catch_depth: 1,
    catch_length: 3,
  });
  const [sheet, setSheet] = useState({
    width: 100,
    height: 60,
    thickness: 2,
    radius: 3,
    k: 0.4,
    flange: 20,
    point: [50, 0, 2],
    body: "",
  });
  const [boss, setBoss] = useState({
    position: [10, 10, 3],
    diameter: 8,
    hole: 3,
    height: 10,
    draft: 1,
  });
  useEffect(() => {
    if (!dirty) setData({ ...clone(empty), ...clone(domainData) } as Process);
  }, [domainData, dirty]);
  const setup = data.setups.find((s) => s.id === setupId) || data.setups[0];
  const machine = data.machines.find((m) => m.id === setup?.machine_id);
  const tool = data.tools.find((t) => t.id === toolId) || data.tools[0];
  const ops = data.operations.filter((o) => o.setup_id === setup?.id);
  const op = ops.find((o) => o.id === opId) || ops[0];
  const update = (next: Process) => {
    setData(next);
    setDirty(true);
    setResult(null);
  };
  const edit = (
    kind: "setups" | "machines" | "tools" | "operations",
    id: string,
    patch: Item,
  ) =>
    update({
      ...data,
      [kind]: data[kind].map((item) =>
        item.id === id ? { ...item, ...patch } : item,
      ),
    });
  const persist = async () => {
    await saveDomain("manufacturing", data as unknown as JsonObject);
    setDirty(false);
  };
  const action = async (work: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await work();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };
  const run = (operation: string, input: Item = {}) =>
    action(async () => {
      if (dirty) await persist();
      setResult(await runDomain("manufacturing", operation, input));
    });
  const id = (prefix: string) => `${prefix}_${Date.now().toString(36)}`;
  const addSetup = () => {
    const machineId = data.machines[0]?.id;
    if (!machineId) {
      setError("Add a machine before creating a setup");
      return;
    }
    const nextId = id("setup");
    update({
      ...data,
      setups: [
        ...data.setups,
        {
          id: nextId,
          name: "New setup",
          machine_id: machineId,
          stock: {
            kind: "box",
            minimum: [0, 0, -10],
            maximum: [100, 60, 0],
            material: "Unspecified",
          },
          fixtures: [],
          wcs_origin: [0, 0, 0],
          wcs_rotation_z_deg: 0,
          work_offset: 54,
          clearance_z: 5,
          tolerance_mm: 0.05,
        },
      ],
    });
    setSetupId(nextId);
  };
  const addMachine = () => {
    const nextId = id("machine");
    update({
      ...data,
      machines: [
        ...data.machines,
        {
          id: nextId,
          name: "New machine",
          process: "milling",
          post: "linuxcnc_mill_v1",
          travel_min: [-100, -100, -100],
          travel_max: [300, 300, 200],
          max_spindle_rpm: 10000,
          max_feed_mm_min: 3000,
          provenance: "User-defined envelope; qualification required",
        },
      ],
    });
  };
  const addTool = () => {
    const nextId = id("tool");
    update({
      ...data,
      tools: [
        ...data.tools,
        {
          id: nextId,
          name: "New end mill",
          number: data.tools.length + 1,
          kind: "flat_endmill",
          diameter: 6,
          flute_length: 20,
          gauge_length: 40,
          holder_diameter: 16,
          spindle_rpm: 5000,
          feed_mm_min: 300,
          plunge_mm_min: 100,
          provenance: "User-defined tool",
        },
      ],
    });
    setToolId(nextId);
  };
  const addOperation = () => {
    if (!setup || !tool) {
      setError("Create a setup and tool first");
      return;
    }
    const nextId = id("op");
    update({
      ...data,
      operations: [
        ...data.operations,
        {
          id: nextId,
          name: "New pocket",
          setup_id: setup.id,
          tool_id: tool.id,
          strategy: machine?.process === "turning" ? "turn_profile" : "pocket",
          boundary: [
            [10, 10],
            [50, 10],
            [50, 40],
            [10, 40],
          ],
          holes: [[20, 20]],
          islands: [],
          profile_zr: [
            [-2, 10],
            [-30, 10],
          ],
          top_z: 0,
          bottom_z: -2,
          stepdown: 1,
          stepover: 0.4,
          allowance_mm: 0,
          peck_mm: 2,
          depends_on: [],
        },
      ],
    });
    setOpId(nextId);
  };
  const formSetup = setup && (
    <>
      <Field label="Setup">
        <SelectControl
          value={setup.id}
          onChange={(e) => setSetupId(e.target.value)}
        >
          {data.setups.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </SelectControl>
      </Field>
      <Field label="Setup name">
        <Input
          value={setup.name}
          onChange={(e) => edit("setups", setup.id, { name: e.target.value })}
        />
      </Field>
      <Field label="Machine">
        <SelectControl
          value={setup.machine_id}
          onChange={(e) =>
            edit("setups", setup.id, { machine_id: e.target.value })
          }
        >
          {data.machines.map((m) => (
            <option key={m.id} value={m.id}>
              {m.name}
            </option>
          ))}
        </SelectControl>
      </Field>
      <Field label="Stock material">
        <Input
          value={setup.stock.material}
          onChange={(e) =>
            edit("setups", setup.id, {
              stock: { ...setup.stock, material: e.target.value },
            })
          }
        />
      </Field>
      <Field label="Stock shape">
        <SelectControl
          value={setup.stock.kind || "box"}
          onChange={(e) =>
            edit("setups", setup.id, {
              stock: { ...setup.stock, kind: e.target.value },
            })
          }
        >
          <option value="box">Box</option>
          <option value="cylinder">Cylinder along Z</option>
        </SelectControl>
      </Field>
      <VectorField
        label="Stock minimum (mm)"
        value={setup.stock.minimum}
        onChange={(minimum) =>
          edit("setups", setup.id, { stock: { ...setup.stock, minimum } })
        }
      />
      <VectorField
        label="Stock maximum (mm)"
        value={setup.stock.maximum}
        onChange={(maximum) =>
          edit("setups", setup.id, { stock: { ...setup.stock, maximum } })
        }
      />
      <VectorField
        label="WCS origin (mm)"
        value={setup.wcs_origin || [0, 0, 0]}
        onChange={(wcs_origin) => edit("setups", setup.id, { wcs_origin })}
      />
      <div className="manufacturing-grid">
        <NumberField
          label="XY rotation (deg)"
          value={setup.wcs_rotation_z_deg || 0}
          onChange={(wcs_rotation_z_deg) =>
            edit("setups", setup.id, { wcs_rotation_z_deg })
          }
        />
        <NumberField
          label="Work offset (54–59)"
          value={setup.work_offset || 54}
          onChange={(work_offset) => edit("setups", setup.id, { work_offset })}
        />
        <NumberField
          label="Clearance Z (mm)"
          value={setup.clearance_z}
          onChange={(clearance_z) => edit("setups", setup.id, { clearance_z })}
        />
        <NumberField
          label="Tolerance (mm)"
          value={setup.tolerance_mm || 0.05}
          onChange={(tolerance_mm) =>
            edit("setups", setup.id, { tolerance_mm })
          }
        />
      </div>
      <details>
        <summary>Fixture envelopes</summary>
        {(setup.fixtures || []).map((f: Item) => (
          <div key={f.id}>
            <Field label="Fixture name">
              <Input
                value={f.name}
                onChange={(e) =>
                  edit("setups", setup.id, {
                    fixtures: setup.fixtures.map((v: Item) =>
                      v.id === f.id ? { ...v, name: e.target.value } : v,
                    ),
                  })
                }
              />
            </Field>
            {["minimum", "maximum"].map((bound) => (
              <VectorField
                key={bound}
                label={`Fixture ${bound} (mm)`}
                value={f[bound]}
                onChange={(value) =>
                  edit("setups", setup.id, {
                    fixtures: setup.fixtures.map((v: Item) =>
                      v.id === f.id ? { ...v, [bound]: value } : v,
                    ),
                  })
                }
              />
            ))}
          </div>
        ))}
        <Button
          onClick={() =>
            edit("setups", setup.id, {
              fixtures: [
                ...(setup.fixtures || []),
                {
                  id: id("fixture"),
                  name: "Clamp",
                  minimum: [0, 0, -5],
                  maximum: [5, 5, 2],
                },
              ],
            })
          }
        >
          Add fixture
        </Button>
      </details>
    </>
  );
  return (
    <section
      className="manufacturing-panel"
      aria-label="Manufacturing workspace"
    >
      <div className="manufacturing-heading">
        <strong>Manufacturing</strong>
        <Button
          disabled={busy || !dirty}
          variant="primary"
          onClick={() => action(persist)}
        >
          {dirty ? "Save process" : "Saved"}
        </Button>
      </div>
      <nav aria-label="Manufacturing workflows">
        {[
          "Machining",
          "Libraries",
          "Sheet & cutting",
          "Additive",
          "Plastics",
          "Inspection",
        ].map((name) => (
          <Button
            key={name}
            aria-pressed={tab === name}
            onClick={() => setTab(name)}
          >
            {name}
          </Button>
        ))}
      </nav>
      {!data.setups.length && !data.machines.length && (
        <div className="manufacturing-empty">
          <p>Define the machine, stock and tools for a repeatable process.</p>
          <Button
            onClick={() => {
              update(clone(reference) as Process);
            }}
          >
            Load reference workflows
          </Button>
          <Button onClick={addMachine}>Add machine</Button>
        </div>
      )}
      {tab === "Machining" && (
        <>
          <div className="manufacturing-actions">
            <Button onClick={addSetup}>Add setup</Button>
            <Button onClick={addOperation}>Add operation</Button>
          </div>
          <details open>
            <summary>Setup, stock and WCS</summary>
            {formSetup}
          </details>
          {op && (
            <details open>
              <summary>Machining operations</summary>
              <Field label="Operation">
                <SelectControl
                  value={op.id}
                  onChange={(e) => setOpId(e.target.value)}
                >
                  {ops.map((o) => (
                    <option key={o.id} value={o.id}>
                      {o.name}
                    </option>
                  ))}
                </SelectControl>
              </Field>
              <Field label="Operation name">
                <Input
                  value={op.name}
                  onChange={(e) =>
                    edit("operations", op.id, { name: e.target.value })
                  }
                />
              </Field>
              <Field label="Cutting tool">
                <SelectControl
                  value={op.tool_id}
                  onChange={(e) =>
                    edit("operations", op.id, { tool_id: e.target.value })
                  }
                >
                  {data.tools.map((t) => (
                    <option key={t.id} value={t.id}>
                      T{t.number} · {t.name}
                    </option>
                  ))}
                </SelectControl>
              </Field>
              <Field label="Strategy">
                <SelectControl
                  value={op.strategy}
                  onChange={(e) =>
                    edit("operations", op.id, { strategy: e.target.value })
                  }
                >
                  {(machine?.process === "turning"
                    ? ["turn_profile", "turn_face", "turn_groove"]
                    : [
                        "face",
                        "pocket",
                        "contour",
                        "drill",
                        "rough_3d",
                        "rest_3d",
                        "parallel_finish",
                      ]
                  ).map((s) => (
                    <option key={s} value={s}>
                      {s.replaceAll("_", " ")}
                    </option>
                  ))}
                </SelectControl>
              </Field>
              {op.strategy.startsWith("turn_") ? (
                <OutlineField
                  label="Profile: axial Z, radius (mm)"
                  value={op.profile_zr}
                  onChange={(profile_zr) =>
                    edit("operations", op.id, { profile_zr })
                  }
                  setError={setError}
                />
              ) : op.strategy === "drill" ? (
                <OutlineField
                  label="Hole centers: X, Y (mm)"
                  value={op.holes}
                  onChange={(holes) => edit("operations", op.id, { holes })}
                  setError={setError}
                />
              ) : (
                <OutlineField
                  label="Closed boundary: X, Y (mm)"
                  value={op.boundary}
                  onChange={(boundary) =>
                    edit("operations", op.id, { boundary })
                  }
                  setError={setError}
                />
              )}
              <div className="manufacturing-grid">
                {[
                  ["top_z", "Top Z (mm)", 0],
                  ["bottom_z", "Bottom Z (mm)", -2],
                  ["stepdown", "Depth per pass (mm)", 1],
                  ["stepover", "Stepover / diameter", 0.4],
                  ["allowance_mm", "Stock allowance (mm)", 0],
                  ["peck_mm", "Drill peck (mm)", 2],
                ].map(([key, label, fallback]) => (
                  <NumberField
                    key={key}
                    label={String(label)}
                    value={op[String(key)] ?? fallback}
                    onChange={(value) =>
                      edit("operations", op.id, { [key]: value })
                    }
                  />
                ))}
              </div>
              {op.strategy === "contour" && (
                <Field label="Tab positions (fractions 0–1, comma separated)">
                  <Input
                    value={(op.tab_centers || []).join(",")}
                    onChange={(e) =>
                      edit("operations", op.id, {
                        tab_centers: e.target.value
                          .split(",")
                          .filter(Boolean)
                          .map(Number),
                      })
                    }
                  />
                </Field>
              )}
              <fieldset className="manufacturing-dependencies">
                <legend>Predecessor operations / rest stock</legend>
                {ops
                  .filter(
                    (candidate) =>
                      data.operations.indexOf(candidate) <
                      data.operations.indexOf(op),
                  )
                  .map((candidate) => (
                    <label className="manufacturing-check" key={candidate.id}>
                      <input
                        type="checkbox"
                        checked={(op.depends_on || []).includes(candidate.id)}
                        onChange={(e) =>
                          edit("operations", op.id, {
                            depends_on: e.target.checked
                              ? [...(op.depends_on || []), candidate.id]
                              : (op.depends_on || []).filter(
                                  (value: string) => value !== candidate.id,
                                ),
                          })
                        }
                      />
                      {candidate.name}
                    </label>
                  ))}
              </fieldset>
            </details>
          )}
          {setup && (
            <div className="manufacturing-actions">
              <Button
                disabled={busy}
                variant="primary"
                onClick={() => run("post", { setup_id: setup.id })}
              >
                Generate NC & checks
              </Button>
              <Button
                disabled={busy}
                onClick={() => run("release", { setup_id: setup.id })}
              >
                Release package
              </Button>
            </div>
          )}
        </>
      )}
      {tab === "Libraries" && (
        <>
          <div className="manufacturing-actions">
            <Button onClick={addMachine}>Add machine</Button>
            <Button onClick={addTool}>Add tool</Button>
          </div>
          {data.machines.map((m) => (
            <details key={m.id}>
              <summary>{m.name}</summary>
              <Field label="Machine name">
                <Input
                  value={m.name}
                  onChange={(e) =>
                    edit("machines", m.id, { name: e.target.value })
                  }
                />
              </Field>
              <Field label="Process">
                <SelectControl
                  value={m.process}
                  onChange={(e) => {
                    const process = e.target.value;
                    edit("machines", m.id, {
                      process,
                      post: (
                        {
                          milling: "linuxcnc_mill_v1",
                          turning: "linuxcnc_lathe_v1",
                          cutting: "grbl_laser_v1",
                          fff: "cura_fff_v1",
                        } as Item
                      )[process],
                    });
                  }}
                >
                  {["milling", "turning", "cutting", "fff"].map((p) => (
                    <option key={p}>{p}</option>
                  ))}
                </SelectControl>
              </Field>
              <p>Post: {m.post}</p>
              <VectorField
                label="Machine travel minimum (mm)"
                value={m.travel_min}
                onChange={(travel_min) =>
                  edit("machines", m.id, { travel_min })
                }
              />
              <VectorField
                label="Machine travel maximum (mm)"
                value={m.travel_max}
                onChange={(travel_max) =>
                  edit("machines", m.id, { travel_max })
                }
              />
              <NumberField
                label="Maximum spindle (rpm)"
                value={m.max_spindle_rpm || 10000}
                onChange={(max_spindle_rpm) =>
                  edit("machines", m.id, { max_spindle_rpm })
                }
              />
              <NumberField
                label="Maximum feed (mm/min)"
                value={m.max_feed_mm_min || 3000}
                onChange={(max_feed_mm_min) =>
                  edit("machines", m.id, { max_feed_mm_min })
                }
              />
              <Field label="Machine definition source">
                <Input
                  value={m.provenance}
                  onChange={(e) =>
                    edit("machines", m.id, { provenance: e.target.value })
                  }
                />
              </Field>
            </details>
          ))}
          {tool && (
            <details open>
              <summary>Tool and holder</summary>
              <Field label="Library tool">
                <SelectControl
                  value={tool.id}
                  onChange={(e) => setToolId(e.target.value)}
                >
                  {data.tools.map((t) => (
                    <option key={t.id} value={t.id}>
                      T{t.number} · {t.name}
                    </option>
                  ))}
                </SelectControl>
              </Field>
              <Field label="Tool name">
                <Input
                  value={tool.name}
                  onChange={(e) =>
                    edit("tools", tool.id, { name: e.target.value })
                  }
                />
              </Field>
              <Field label="Tool type">
                <SelectControl
                  value={tool.kind}
                  onChange={(e) =>
                    edit("tools", tool.id, { kind: e.target.value })
                  }
                >
                  {[
                    "flat_endmill",
                    "ball_endmill",
                    "drill",
                    "turning_insert",
                    "laser",
                    "probe",
                  ].map((k) => (
                    <option key={k} value={k}>
                      {k.replaceAll("_", " ")}
                    </option>
                  ))}
                </SelectControl>
              </Field>
              <div className="manufacturing-grid">
                {[
                  ["number", "Tool number", 1],
                  ["diameter", "Diameter (mm)", 6],
                  ["flute_length", "Flute length (mm)", 20],
                  ["gauge_length", "Gauge length (mm)", 40],
                  ["holder_diameter", "Holder diameter (mm)", 16],
                  ["spindle_rpm", "Spindle (rpm)", 5000],
                  ["feed_mm_min", "Feed (mm/min)", 300],
                  ["plunge_mm_min", "Plunge (mm/min)", 100],
                ].map(([key, label, fallback]) => (
                  <NumberField
                    key={key}
                    label={String(label)}
                    value={tool[String(key)] ?? fallback}
                    onChange={(value) =>
                      edit("tools", tool.id, { [key]: value })
                    }
                  />
                ))}
              </div>
              <Field label="Tool definition source">
                <Input
                  value={tool.provenance}
                  onChange={(e) =>
                    edit("tools", tool.id, { provenance: e.target.value })
                  }
                />
              </Field>
            </details>
          )}
        </>
      )}
      {tab === "Sheet & cutting" && (
        <>
          <details open>
            <summary>Sheet design and bend data</summary>
            <div className="manufacturing-grid">
              {[
                ["width", "Base width (mm)"],
                ["height", "Base height (mm)"],
                ["thickness", "Thickness (mm)"],
                ["radius", "Inside bend radius (mm)"],
                ["k", "K-factor"],
                ["flange", "Flange height (mm)"],
              ].map(([key, label]) => (
                <NumberField
                  key={key}
                  label={label}
                  value={(sheet as Item)[key]}
                  onChange={(value) => setSheet({ ...sheet, [key]: value })}
                />
              ))}
            </div>
            <Button
              disabled={busy}
              onClick={() =>
                action(async () => {
                  await native("sheet.manage_rules", {
                    name: "Manufacturing sheet",
                    thickness: sheet.thickness,
                    bend_radius: sheet.radius,
                    k_factor: sheet.k,
                    active: true,
                  });
                  const name = id("base");
                  await native("sketch.create", { name, plane: "XY" });
                  await native("sketch.rectangle.two_point", {
                    p0: [0, 0],
                    p1: [sheet.width, sheet.height],
                  });
                  await native("sketch.finish");
                  await native("sheet.flange", { sketch: name });
                })
              }
            >
              Create base sheet
            </Button>
            <VectorField
              label="Edge point for flange (mm)"
              value={sheet.point}
              onChange={(point) => setSheet({ ...sheet, point })}
            />
            <Button
              disabled={busy}
              onClick={() =>
                action(async () => {
                  await native("sheet.flange", {
                    edges: [sheet.point],
                    height: sheet.flange,
                    angle: 90,
                    ...(sheet.body ? { body: sheet.body } : {}),
                  });
                })
              }
            >
              Add 90° flange
            </Button>
            <Field label="Sheet body name (optional)">
              <Input
                value={sheet.body}
                onChange={(e) => setSheet({ ...sheet, body: e.target.value })}
              />
            </Field>
            <Button
              disabled={busy}
              variant="primary"
              onClick={() =>
                run("sheet_release", sheet.body ? { body: sheet.body } : {})
              }
            >
              Develop, verify & export DXF
            </Button>
          </details>
          <details open>
            <summary>Batch nesting and laser cutting</summary>
            {!data.nesting ? (
              <Button
                onClick={() =>
                  update({ ...data, nesting: clone(reference.nesting) })
                }
              >
                Add sheet and part quantities
              </Button>
            ) : (
              <>
                <div className="manufacturing-grid">
                  {[
                    ["sheet_width_mm", "Sheet width (mm)"],
                    ["sheet_height_mm", "Sheet height (mm)"],
                    ["margin_mm", "Edge margin (mm)"],
                    ["gap_mm", "Part gap (mm)"],
                    ["kerf_mm", "Kerf (mm)"],
                    ["max_sheets", "Maximum sheets"],
                  ].map(([key, label]) => (
                    <NumberField
                      key={key}
                      label={label}
                      value={data.nesting![key]}
                      onChange={(value) =>
                        update({
                          ...data,
                          nesting: { ...data.nesting, [key]: value },
                        })
                      }
                    />
                  ))}
                </div>
                <Field label="Sheet material">
                  <Input
                    value={data.nesting.material}
                    onChange={(e) =>
                      update({
                        ...data,
                        nesting: {
                          ...data.nesting,
                          material: e.target.value,
                          parts: data.nesting!.parts.map((p: Item) => ({
                            ...p,
                            material: e.target.value,
                          })),
                        },
                      })
                    }
                  />
                </Field>
                <NumberField
                  label="Sheet thickness (mm)"
                  value={data.nesting.thickness_mm}
                  onChange={(thickness_mm) =>
                    update({
                      ...data,
                      nesting: {
                        ...data.nesting,
                        thickness_mm,
                        parts: data.nesting!.parts.map((p: Item) => ({
                          ...p,
                          thickness_mm,
                        })),
                      },
                    })
                  }
                />
                {data.nesting.parts.map((p: Item, i: number) => (
                  <details key={p.id}>
                    <summary>
                      {p.id} · quantity {p.quantity}
                    </summary>
                    <NumberField
                      label="Quantity"
                      value={p.quantity}
                      onChange={(quantity) =>
                        update({
                          ...data,
                          nesting: {
                            ...data.nesting,
                            parts: data.nesting!.parts.map(
                              (v: Item, j: number) =>
                                i === j ? { ...v, quantity } : v,
                            ),
                          },
                        })
                      }
                    />
                    <Field label="Allowed rotations (degrees)">
                      <Input
                        value={p.rotations_deg.join(",")}
                        onChange={(e) =>
                          update({
                            ...data,
                            nesting: {
                              ...data.nesting,
                              parts: data.nesting!.parts.map(
                                (v: Item, j: number) =>
                                  i === j
                                    ? {
                                        ...v,
                                        rotations_deg: e.target.value
                                          .split(",")
                                          .filter(Boolean)
                                          .map(Number),
                                      }
                                    : v,
                              ),
                            },
                          })
                        }
                      />
                    </Field>
                    <OutlineField
                      label="Part boundary (X, Y mm)"
                      value={p.boundary}
                      setError={setError}
                      onChange={(boundary) =>
                        update({
                          ...data,
                          nesting: {
                            ...data.nesting,
                            parts: data.nesting!.parts.map(
                              (v: Item, j: number) =>
                                i === j ? { ...v, boundary } : v,
                            ),
                          },
                        })
                      }
                    />
                  </details>
                ))}
                <Button
                  onClick={() =>
                    update({
                      ...data,
                      nesting: {
                        ...data.nesting,
                        parts: [
                          ...data.nesting!.parts,
                          {
                            ...clone(reference.nesting.parts[0]),
                            id: id("part"),
                            quantity: 1,
                            material: data.nesting!.material,
                            thickness_mm: data.nesting!.thickness_mm,
                          },
                        ],
                      },
                    })
                  }
                >
                  Add nesting part
                </Button>
                <Field label="Cutting setup">
                  <SelectControl
                    value={setup?.id || ""}
                    onChange={(e) => setSetupId(e.target.value)}
                  >
                    {data.setups.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name}
                      </option>
                    ))}
                  </SelectControl>
                </Field>
                <Field label="Laser tool">
                  <SelectControl
                    value={tool?.id || ""}
                    onChange={(e) => setToolId(e.target.value)}
                  >
                    {data.tools.map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.name}
                      </option>
                    ))}
                  </SelectControl>
                </Field>
                <NumberField
                  label="GRBL laser power S (0–1000)"
                  value={power}
                  onChange={setPower}
                />
                <div className="manufacturing-actions">
                  <Button disabled={busy} onClick={() => run("nest")}>
                    Nest & export DXF
                  </Button>
                  <Button
                    disabled={busy || !setup || !tool}
                    onClick={() =>
                      run("cut", {
                        setup_id: setup?.id,
                        tool_id: tool?.id,
                        power_s: power,
                      })
                    }
                  >
                    Generate cutting NC
                  </Button>
                </div>
              </>
            )}
          </details>
        </>
      )}
      {tab === "Additive" && (
        <>
          {!data.printer ? (
            <Button
              onClick={() => {
                const existing = data.machines.find((m) => m.process === "fff");
                const machine = existing || {
                  ...clone(reference.machines[3]),
                  id: id("printer"),
                };
                update({
                  ...data,
                  machines: existing
                    ? data.machines
                    : [...data.machines, machine],
                  printer: {
                    ...clone(reference.printer),
                    machine_id: machine.id,
                    print_speed_mm_s: 40,
                    wall_count: 2,
                  },
                });
              }}
            >
              Configure FFF process
            </Button>
          ) : (
            <>
              <Field label="FFF machine">
                <SelectControl
                  value={data.printer.machine_id}
                  onChange={(e) =>
                    update({
                      ...data,
                      printer: { ...data.printer, machine_id: e.target.value },
                    })
                  }
                >
                  {data.machines
                    .filter((m) => m.process === "fff")
                    .map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.name}
                      </option>
                    ))}
                </SelectControl>
              </Field>
              <div className="manufacturing-grid">
                {[
                  ["layer_height_mm", "Layer height (mm)"],
                  ["nozzle_diameter_mm", "Nozzle diameter (mm)"],
                  ["filament_diameter_mm", "Filament diameter (mm)"],
                  ["temperature_c", "Nozzle temperature (°C)"],
                  ["bed_temperature_c", "Bed temperature (°C)"],
                  ["infill_percent", "Infill (%)"],
                  ["wall_count", "Wall count"],
                  ["print_speed_mm_s", "Print speed (mm/s)"],
                ].map(([key, label]) => (
                  <NumberField
                    key={key}
                    label={label}
                    value={
                      data.printer![key] ?? (reference.printer as Item)[key]
                    }
                    onChange={(value) =>
                      update({
                        ...data,
                        printer: { ...data.printer, [key]: value },
                      })
                    }
                  />
                ))}
              </div>
              <VectorField
                label="Build orientation (deg)"
                value={data.printer.orientation_deg || [0, 0, 0]}
                onChange={(orientation_deg) =>
                  update({
                    ...data,
                    printer: { ...data.printer, orientation_deg },
                  })
                }
              />
              <label className="manufacturing-check">
                <input
                  type="checkbox"
                  checked={data.printer.supports}
                  onChange={(e) =>
                    update({
                      ...data,
                      printer: { ...data.printer, supports: e.target.checked },
                    })
                  }
                />
                Generate supports
              </label>
              <Field label="Material and printer profile source">
                <Input
                  value={data.printer.profile_source}
                  onChange={(e) =>
                    update({
                      ...data,
                      printer: {
                        ...data.printer,
                        profile_source: e.target.value,
                      },
                    })
                  }
                />
              </Field>
              <Button
                disabled={busy}
                variant="primary"
                onClick={() => run("slice")}
              >
                Slice current design
              </Button>
              <p>
                Build volume, positive extrusion and layers are checked. Review
                the build on the configured printer.
              </p>
            </>
          )}
        </>
      )}
      {tab === "Plastics" && (
        <>
          {!data.plastics ? (
            <Button
              onClick={() =>
                update({ ...data, plastics: clone(reference.plastics) })
              }
            >
              Set material rules
            </Button>
          ) : (
            <>
              <Field label="Polymer">
                <Input
                  value={data.plastics.material}
                  onChange={(e) =>
                    update({
                      ...data,
                      plastics: { ...data.plastics, material: e.target.value },
                    })
                  }
                />
              </Field>
              <Field label="Material design guide / source">
                <Input
                  value={data.plastics.source}
                  onChange={(e) =>
                    update({
                      ...data,
                      plastics: { ...data.plastics, source: e.target.value },
                    })
                  }
                />
              </Field>
              <div className="manufacturing-grid">
                {[
                  ["min_wall_mm", "Minimum wall (mm)"],
                  ["max_wall_mm", "Maximum wall (mm)"],
                  ["min_draft_deg", "Minimum draft (deg)"],
                ].map(([key, label]) => (
                  <NumberField
                    key={key}
                    label={label}
                    value={data.plastics![key]}
                    onChange={(value) =>
                      update({
                        ...data,
                        plastics: { ...data.plastics, [key]: value },
                      })
                    }
                  />
                ))}
              </div>
              <VectorField
                label="Tooling pull direction"
                value={data.plastics.pull_direction}
                onChange={(pull_direction) =>
                  update({
                    ...data,
                    plastics: { ...data.plastics, pull_direction },
                  })
                }
              />
              <Button disabled={busy} onClick={() => run("plastics_advice")}>
                Analyze current design
              </Button>
              <p>
                Localized draft, section thickness and pull-access advice.
                Review the supplier rules before release.
              </p>
            </>
          )}
          <details>
            <summary>Native plastic boss</summary>
            <VectorField
              label="Boss face position (mm)"
              value={boss.position}
              onChange={(position) => setBoss({ ...boss, position })}
            />
            <div className="manufacturing-grid">
              {[
                ["diameter", "Boss diameter (mm)"],
                ["hole", "Hole diameter (mm)"],
                ["height", "Boss height (mm)"],
                ["draft", "Boss draft (deg)"],
              ].map(([key, label]) => (
                <NumberField
                  key={key}
                  label={label}
                  value={(boss as Item)[key]}
                  onChange={(value) => setBoss({ ...boss, [key]: value })}
                />
              ))}
            </div>
            <Button
              disabled={busy}
              onClick={() =>
                action(async () => {
                  await native("plastic.boss", {
                    position: boss.position,
                    diameter: boss.diameter,
                    hole_diameter: boss.hole,
                    height: boss.height,
                    draft: boss.draft,
                  });
                })
              }
            >
              Create boss
            </Button>
          </details>
          <details>
            <summary>Native snap-fit</summary>
            <VectorField
              label="Snap-fit face position (mm)"
              value={snap.position}
              onChange={(position) => setSnap({ ...snap, position })}
            />
            <VectorField
              label="Catch direction"
              value={snap.hook}
              onChange={(hook) => setSnap({ ...snap, hook })}
            />
            <div className="manufacturing-grid">
              {[
                ["length", "Beam length (mm)"],
                ["thickness", "Beam thickness (mm)"],
                ["width", "Beam width (mm)"],
                ["catch_depth", "Catch depth (mm)"],
                ["catch_length", "Catch length (mm)"],
              ].map(([key, label]) => (
                <NumberField
                  key={key}
                  label={label}
                  value={(snap as Item)[key]}
                  onChange={(value) => setSnap({ ...snap, [key]: value })}
                />
              ))}
            </div>
            <Button
              disabled={busy}
              onClick={() =>
                action(async () => {
                  await native("plastic.snap_fit", snap);
                })
              }
            >
              Create snap-fit
            </Button>
          </details>
        </>
      )}
      {tab === "Inspection" && (
        <>
          <p>
            Import nominal and measured values with signed tolerances in mm or
            degrees.
          </p>
          <Field label="Inspection measurements CSV">
            <textarea
              rows={8}
              value={measurements}
              spellCheck={false}
              onChange={(e) => setMeasurements(e.target.value)}
            />
          </Field>
          <Button
            disabled={busy}
            onClick={() => run("inspect", { csv: measurements })}
          >
            Evaluate inspection
          </Button>
          <details>
            <summary>Offline Z probing plan</summary>
            <Field label="Probe tool">
              <SelectControl
                value={tool?.id || ""}
                onChange={(e) => setToolId(e.target.value)}
              >
                {data.tools
                  .filter((t) => t.kind === "probe")
                  .map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name}
                    </option>
                  ))}
              </SelectControl>
            </Field>
            <OutlineField
              label="Probe centers: X, Y (mm)"
              value={probe.points}
              setError={setError}
              onChange={(points) => setProbe({ ...probe, points })}
            />
            <div className="manufacturing-grid">
              <NumberField
                label="Probe start Z (mm)"
                value={probe.start}
                onChange={(start) => setProbe({ ...probe, start })}
              />
              <NumberField
                label="Probe seek Z (mm)"
                value={probe.seek}
                onChange={(seek) => setProbe({ ...probe, seek })}
              />
              <NumberField
                label="Probe feed (mm/min)"
                value={probe.feed}
                onChange={(feed) => setProbe({ ...probe, feed })}
              />
            </div>
            <Button
              disabled={busy || !setup || !tool}
              onClick={() =>
                run("probe_plan", {
                  setup_id: setup?.id,
                  tool_id: tool?.id,
                  points: probe.points.map((p) => ({
                    x: p[0],
                    y: p[1],
                    start_z: probe.start,
                    seek_z: probe.seek,
                    feed_mm_min: probe.feed,
                  })),
                })
              }
            >
              Generate probe cycles
            </Button>
          </details>
          <p>
            For alignment, use CSV columns id, nominal_x, nominal_y, nominal_z,
            measured_x, measured_y, measured_z.
          </p>
          <Field label="Inspection setup">
            <SelectControl
              value={setup?.id || ""}
              onChange={(e) => setSetupId(e.target.value)}
            >
              {data.setups.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </SelectControl>
          </Field>
          <Button
            disabled={busy || !setup}
            onClick={() =>
              run("align", { csv: measurements, setup_id: setup?.id })
            }
          >
            Calculate alignment
          </Button>
          {result?.alignment?.translation_within_tolerance && (
            <Button
              disabled={busy}
              onClick={() =>
                run("apply_probe", {
                  csv: measurements,
                  setup_id: setup?.id,
                  source_input_key: result.input_key,
                })
              }
            >
              Apply measured WCS translation
            </Button>
          )}
        </>
      )}
      {busy && <p role="status">Running manufacturing workflow…</p>}
      {result && (
        <div className="manufacturing-results" aria-live="polite">
          <strong>
            {result.state === "rejected"
              ? "Release blocked"
              : "Ready for process review"}
          </strong>
          {result.reason && <p>{result.reason}</p>}
          <Preview result={result} />
          {result.verification?.map((r: Item) => (
            <p key={r.operation_id}>
              {r.operation_id}: {r.violation_count} violations · feed travel{" "}
              {r.feed_distance_mm.toFixed(1)} mm
              {r.remaining_reachable_area_mm2 !== undefined
                ? ` · residual floor ${r.remaining_reachable_area_mm2.toFixed(3)} mm²`
                : ""}
            </p>
          ))}
          {result.nest && (
            <p>
              {result.nest.sheet_count} sheets ·{" "}
              {(result.nest.utilization * 100).toFixed(1)}% utilization
            </p>
          )}
          {result.slicing && (
            <p>
              {result.slicing.analysis.layer_count} layers ·{" "}
              {result.slicing.analysis.extrusion_filament_mm.toFixed(1)} mm
              filament · {result.slicing.provenance.engine}{" "}
              {result.slicing.provenance.version}
            </p>
          )}
          {result.inspection && (
            <p>
              {result.inspection.pass_count} passed ·{" "}
              {result.inspection.fail_count} failed
            </p>
          )}
          {result.plastics && (
            <p>
              {result.plastics.findings.length} localized findings across{" "}
              {result.plastics.sampled_faces} sampled faces
            </p>
          )}
          {result.sheet && (
            <p>
              {result.sheet.flat_pattern.bends.length} bends · independently
              reloaded DXF, {result.sheet.dxf_audit.area_mm2.toFixed(1)} mm²
            </p>
          )}
          <div className="manufacturing-artifacts">
            {Object.entries(result.artifacts || {}).map(([name, path]) => (
              <a key={name} href={artifact(String(path))} download>
                {name}
              </a>
            ))}
          </div>
          <details>
            <summary>Process report and limits</summary>
            <pre>
              {JSON.stringify(
                {
                  ...result,
                  paths: undefined,
                  slicing: result.slicing
                    ? {
                        ...result.slicing,
                        analysis: {
                          ...result.slicing.analysis,
                          preview_segments: undefined,
                        },
                      }
                    : undefined,
                },
                null,
                2,
              )}
            </pre>
          </details>
        </div>
      )}
    </section>
  );
}
