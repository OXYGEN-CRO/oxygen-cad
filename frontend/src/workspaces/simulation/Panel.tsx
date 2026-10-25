import { useEffect, useState } from "react";
import { Button, Input, SelectControl } from "../../components/ui";
import { artifact } from "../../api";
import type { Material } from "../../generated";
import type { DomainPanelProps, JsonObject } from "../contracts";
import FieldPreview, { type FieldData } from "./FieldPreview";
import "./simulation.css";

type Row = Record<string, any>;
type Data = {
  schema_version: 1;
  models: Row[];
  materials: Row[];
  studies: Row[];
  active_study_id: string | null;
};
const empty = (): Data => ({
  schema_version: 1,
  models: [],
  materials: [],
  studies: [],
  active_study_id: null,
});
const parse = (value: JsonObject): Data => ({ ...empty(), ...value }) as Data;
const id = (prefix: string) => `${prefix}-${crypto.randomUUID().slice(0, 8)}`;
const types = [
  ["linear_static", "Static stress"],
  ["modal", "Modal frequencies"],
  ["buckling", "Structural buckling"],
  ["steady_thermal", "Steady thermal"],
  ["thermal_stress", "Thermal stress"],
  ["nonlinear_static", "Nonlinear static"],
  ["dynamic_event", "Dynamic event · small strain"],
  ["quasi_static_event", "Quasi-static event · small strain"],
  ["electronics_cooling", "Electronics · prescribed-film thermal"],
  ["topology", "Topology · SIMP mesh guidance"],
  ["generative", "Generative · evaluated material alternatives"],
];
const format = (v: unknown) =>
  typeof v === "number" ? v.toPrecision(5) : String(v ?? "—");

export default function SimulationPanel(props: DomainPanelProps) {
  const [data, setData] = useState<Data>(() => parse(props.domainData));
  const [tab, setTab] = useState("setup"),
    [busy, setBusy] = useState("");
  const [inspection, setInspection] = useState<Row | null>(null),
    [results, setResults] = useState<Row | null>(null);
  const [field, setField] = useState<FieldData | null>(null),
    [quantity, setQuantity] = useState("displacement"),
    [scale, setScale] = useState(1),
    [mode, setMode] = useState(0);
  const [meshSize, setMeshSize] = useState(4),
    [refinements, setRefinements] = useState(2),
    [caseId, setCaseId] = useState("");
  const [elementFamily, setElementFamily] = useState("C3D10");
  const [probeText, setProbeText] = useState("");
  const [advanced, setAdvanced] = useState(""),
    [historyText, setHistoryText] = useState("");
  const serialized = JSON.stringify(props.domainData);
  useEffect(() => {
    setData(parse(props.domainData));
    setResults(null);
    setField(null);
  }, [serialized]);
  const dirty =
    JSON.stringify(data) !== JSON.stringify(parse(props.domainData));
  const study = data.studies.find((s) => s.id === data.active_study_id),
    model = data.models.find((m) => m.id === study?.model_id);
  const quadraticSupported = [
    "linear_static",
    "nonlinear_static",
    "steady_thermal",
    "electronics_cooling",
    "thermal_stress",
    "modal",
    "buckling",
  ].includes(study?.type);
  const physicalProbeSupported = [
    "linear_static",
    "nonlinear_static",
    "thermal_stress",
  ].includes(study?.type);
  useEffect(() => {
    setElementFamily(
      quadraticSupported ? (model?.mesh?.element_family ?? "C3D10") : "C3D4",
    );
  }, [
    study?.id,
    study?.type,
    model?.id,
    model?.mesh?.element_family,
    quadraticSupported,
  ]);
  const storedProbe = JSON.stringify(study?.settings?.probe_point_mm ?? null);
  useEffect(() => {
    const value = JSON.parse(storedProbe);
    setProbeText(value ? value.join(", ") : "");
  }, [study?.id, storedProbe]);
  const probeValues = probeText
    .trim()
    .split(/[\s,]+/)
    .map(Number);
  const validProbe =
    probeText.trim().length > 0 &&
    probeValues.length === 3 &&
    probeValues.every(Number.isFinite);
  const loadCase =
    study?.cases?.find((c: Row) => c.id === caseId) ?? study?.cases?.[0];
  const currentInspection = inspection?.studies?.find(
    (s: Row) => s.id === study?.id,
  );
  const thermal = [
    "steady_thermal",
    "electronics_cooling",
    "thermal_stress",
  ].includes(study?.type);
  const event = ["dynamic_event", "quasi_static_event"].includes(study?.type);
  const build = props.state.build as Row | null;
  const sceneBodies: Row[] =
    build?.scene?.bodies?.filter((b: Row) => b.geometry_kind === "solid") ?? [];
  const update = (fn: (draft: Data) => void) =>
    setData((before) => {
      const next = structuredClone(before);
      fn(next);
      return next;
    });
  const editStudy = (fn: (s: Row, d: Data) => void) =>
    update((d) => {
      const s = d.studies.find((x) => x.id === data.active_study_id);
      if (s) fn(s, d);
    });
  const editCase = (fn: (c: Row) => void) =>
    editStudy((s) => {
      const c = s.cases.find((x: Row) => x.id === loadCase?.id);
      if (c) fn(c);
    });
  const act = async (name: string, callback: () => Promise<unknown>) => {
    setBusy(name);
    try {
      await callback();
    } catch (e) {
      props.setError(String(e));
    } finally {
      setBusy("");
    }
  };
  const run = (operation: string, input: Row = {}) =>
    props.runDomain("simulation", operation, { study_id: study?.id, ...input });
  const inspect = () =>
    act("Inspecting", async () =>
      setInspection(await props.runDomain("simulation", "inspect")),
    );
  const save = () =>
    act("Saving", async () => {
      await props.saveDomain("simulation", data);
      setInspection(null);
    });
  const create = () =>
    update((d) => {
      const modelId = id("model"),
        studyId = id("study");
      const bodyIds = sceneBodies.length
        ? sceneBodies.map((b) => b.id)
        : ["part"];
      if (!d.materials.length && props.project.material) {
        const material = props.project.material as Material;
        const aliases: Record<string, string> = {
          thermal_conductivity_w_m_k: "conductivity_w_mk",
          specific_heat_j_kg_k: "specific_heat_j_kgk",
          thermal_expansion_per_k: "expansion_per_k",
        };
        const propertySources = { ...(material.property_sources ?? {}) };
        for (const [key, value] of Object.entries(
          material.property_sources ?? {},
        )) {
          const target = Object.hasOwn(aliases, key) ? aliases[key] : undefined;
          if (target && !(target in propertySources))
            propertySources[target] = value;
        }
        d.materials.push({
          id: "project-material",
          revision: 1,
          name: material.name,
          source: material.source,
          youngs_modulus_mpa: material.youngs_modulus_mpa,
          poisson_ratio: material.poisson_ratio,
          density_g_cm3: material.density_g_cm3,
          yield_strength_mpa: material.yield_strength_mpa,
          conductivity_w_mk: material.thermal_conductivity_w_m_k,
          specific_heat_j_kgk: material.specific_heat_j_kg_k,
          expansion_per_k: material.thermal_expansion_per_k,
          process: material.process ?? null,
          condition:
            material.condition ??
            "Not supplied; physical correlation not supplied",
          property_sources: propertySources,
          plastic_curve: (material.plastic_curve ?? []).map((point) => [
            point.stress_mpa,
            point.plastic_strain,
          ]),
          plastic_source: material.plastic_curve_source ?? null,
        });
      }
      d.models.push({
        id: modelId,
        name: "Analysis model",
        geometry_key: props.state.geometry_key,
        body_ids: bodyIds,
        simplifications: [],
        regions: [],
        mesh: null,
        refinements: [],
      });
      d.studies.push({
        id: studyId,
        name: "New study",
        type: "linear_static",
        model_id: modelId,
        assignments: bodyIds.flatMap((body_id) =>
          d.materials[0] ? [{ body_id, material_id: d.materials[0].id }] : [],
        ),
        cases: [
          {
            id: id("case"),
            name: "Load case 1",
            supports: [],
            loads: [],
            thermal: [],
            history: [],
          },
        ],
        interfaces: [],
        bolts: [],
        settings: {},
        limits: [],
        assumptions: [],
        assumptions_confirmed: false,
      });
      d.active_study_id = studyId;
    });
  const selectionRegion = (draft: Data): string => {
    if (!props.selection || props.selection.kind !== "face")
      throw new Error("Select a CAD face in the viewport first.");
    const m = draft.models.find((x) => x.id === study?.model_id)!;
    const face = props.selection.face as Row;
    const matchingBodies = sceneBodies.filter((b) =>
      b.faces?.some(
        (f: Row) =>
          f.id === props.selection!.id ||
          f.aliases?.includes(props.selection!.id),
      ),
    );
    const bodyId =
      face.body_id ??
      (matchingBodies.length === 1 ? matchingBodies[0].id : undefined) ??
      (sceneBodies.length === 0 ? "part" : undefined);
    if (!bodyId)
      throw new Error(
        "The selected face has no unique CAD body; select it again after rebuilding.",
      );
    if (!m.body_ids.includes(bodyId))
      throw new Error(
        "The selected face belongs to a body outside this analysis model.",
      );
    const regionId = `${bodyId}::${props.selection.id}`;
    m.regions ??= [];
    if (!m.regions.some((r: Row) => r.id === regionId))
      m.regions.push({
        id: regionId,
        body_id: bodyId,
        selection: props.selection.id,
        center_mm: face.center,
        normal: face.normal,
        area_mm2: face.area_mm2,
      });
    return regionId;
  };
  const chooseInterfaceFace = (
    index: number,
    role: "primary_region_id" | "secondary_region_id",
  ) => {
    try {
      const next = structuredClone(data);
      const region = selectionRegion(next);
      next.studies.find((item) => item.id === study?.id)!.interfaces[index][
        role
      ] = region;
      setData(next);
    } catch (error) {
      props.setError(String(error));
    }
  };
  const addBoundary = (kind: "support" | "load" | "thermal") => {
    try {
      const draft = structuredClone(data),
        region = selectionRegion(draft),
        s = draft.studies.find((x) => x.id === study?.id)!;
      const c = s.cases.find((x: Row) => x.id === loadCase.id);
      if (kind === "support")
        c.supports.push({
          id: id("support"),
          region_id: region,
          kind: "fixed",
        });
      if (kind === "load")
        c.loads.push({
          id: id("load"),
          region_id: region,
          kind: "force",
          vector: [0, 0, 0],
          magnitude: 0,
          source: "",
        });
      if (kind === "thermal")
        c.thermal.push({
          id: id("thermal"),
          region_id: region,
          kind: "temperature",
          value: 0,
          source: "",
        });
      setData(draft);
    } catch (e) {
      props.setError(String(e));
    }
  };
  const addMaterial = () =>
    update((d) =>
      d.materials.push({
        id: id("material"),
        revision: 1,
        name: "",
        source: "",
      }),
    );
  const loadField = (path: string) =>
    act("Loading field", async () => {
      const response = await fetch(artifact(path));
      if (!response.ok) throw new Error(await response.text());
      const value: FieldData = await response.json();
      setField(value);
      setMode(
        Math.max(0, value.modes?.findIndex((m) => m.rigid_body !== true) ?? 0),
      );
      setQuantity(
        value.element_density
          ? "density"
          : value.modes
            ? "mode"
            : value.temperature_k && !value.displacements_mm
              ? "temperature"
              : "displacement",
      );
    });
  const loadResults = () =>
    act("Loading results", async () => {
      setResults(await run("results"));
      setField(null);
      setTab("results");
    });
  return (
    <section className="simulation-panel" aria-label="Simulation workspace">
      <div className="sim-toolbar">
        <strong>Simulation</strong>
        <Button onClick={create}>New study</Button>
        <Button onClick={save} disabled={!!busy || !dirty} variant="primary">
          Save setup
        </Button>
      </div>
      <p className="sim-help">
        Versioned studies with numerical evidence. Physical validation is
        recorded separately.
      </p>
      <div className="sim-tabs" role="tablist" aria-label="Simulation views">
        {["setup", "results", "scope"].map((t) => (
          <Button
            key={t}
            role="tab"
            aria-selected={tab === t}
            onClick={() => {
              setTab(t);
              if (t === "scope") void inspect();
            }}
          >
            {t === "scope" ? "Capabilities" : t[0].toUpperCase() + t.slice(1)}
          </Button>
        ))}
      </div>
      {data.studies.length > 0 && (
        <label>
          Study
          <SelectControl
            value={data.active_study_id ?? ""}
            onChange={(e) => {
              update((d) => {
                d.active_study_id = e.target.value;
              });
              setResults(null);
              setField(null);
              setCaseId("");
            }}
          >
            {data.studies.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </SelectControl>
        </label>
      )}
      {busy && <p role="status">{busy}…</p>}
      {dirty && (
        <p className="sim-notice">
          Setup has unsaved changes. Save before meshing or solving.
        </p>
      )}
      {tab === "setup" && !study && (
        <p>
          Create a study, assign sourced materials, then select faces for its
          supports and loads.
        </p>
      )}
      {tab === "setup" && study && (
        <>
          <label>
            Study name
            <Input
              value={study.name}
              onChange={(e) =>
                editStudy((s) => {
                  s.name = e.target.value;
                })
              }
            />
          </label>
          <label>
            Study type
            <SelectControl
              value={study.type}
              onChange={(e) =>
                editStudy((s) => {
                  s.type = e.target.value;
                })
              }
            >
              {types.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </SelectControl>
          </label>
          {(study.type === "modal" || study.type === "buckling") && (
            <label>
              {study.type === "modal" && elementFamily === "C3D10"
                ? "Requested elastic modes"
                : "Requested modes"}
              <Input
                type="number"
                min="1"
                max="30"
                step="1"
                value={study.settings.mode_count ?? 6}
                onChange={(e) =>
                  editStudy((s) => {
                    s.settings.mode_count = Number(e.target.value);
                  })
                }
              />
            </label>
          )}
          {study.type === "modal" && elementFamily === "C3D10" && (
            <p className="sim-help">
              Rigid motions are reported separately. Mode amplitudes do not
              predict a forced response.
            </p>
          )}
          {study.type === "buckling" && (
            <p className="sim-help">
              Positive factors scale all declared reference loads together.
              Shapes have arbitrary amplitude; imperfect collapse requires a
              separate nonlinear study.
            </p>
          )}
          <div className="sim-toolbar">
            <Button
              disabled={dirty || !!busy}
              onClick={() =>
                void act("Cloning", async () => {
                  await run("clone_study", { new_id: id("study") });
                  await props.refresh();
                })
              }
            >
              Clone study
            </Button>
            <Button
              disabled={dirty || !!busy}
              onClick={() =>
                void act("Cloning model", async () => {
                  await run("clone_model", { new_id: id("model") });
                  await props.refresh();
                })
              }
            >
              Independent model
            </Button>
          </div>
          <details open>
            <summary>Analysis bodies and materials</summary>
            <p className="sim-help">
              {model?.body_ids.length} solid bodies ·{" "}
              {model?.mesh
                ? `${model.mesh.elements.length} tetrahedra`
                : "No volume mesh"}
            </p>
            {model?.body_ids.map((body: string) => (
              <label key={body} title={body}>
                {sceneBodies.find((b) => b.id === body)?.name ?? body}
                <SelectControl
                  aria-label={`Material for ${body}`}
                  value={
                    study.assignments.find((a: Row) => a.body_id === body)
                      ?.material_id ?? ""
                  }
                  onChange={(e) =>
                    editStudy((s) => {
                      s.assignments = s.assignments.filter(
                        (a: Row) => a.body_id !== body,
                      );
                      if (e.target.value)
                        s.assignments.push({
                          body_id: body,
                          material_id: e.target.value,
                        });
                    })
                  }
                >
                  <option value="">Choose sourced material</option>
                  {data.materials.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.name || "Untitled material"} · r{m.revision}
                    </option>
                  ))}
                </SelectControl>
              </label>
            ))}
            <Button onClick={addMaterial}>Add material</Button>
            {data.materials.map((m) => (
              <details key={m.id}>
                <summary>
                  {m.name || "New material"} · revision {m.revision}
                </summary>
                {[
                  ["name", "Material name"],
                  ["source", "Data source / citation"],
                  ["youngs_modulus_mpa", "Young’s modulus (MPa)"],
                  ["poisson_ratio", "Poisson ratio"],
                  ["density_g_cm3", "Density (g/cm³)"],
                  ["conductivity_w_mk", "Conductivity (W/m·K)"],
                  ["expansion_per_k", "Expansion (1/K)"],
                  ["yield_strength_mpa", "Yield strength (MPa)"],
                ].map(([key, label]) => (
                  <label key={key}>
                    {label}
                    <Input
                      type={
                        ["name", "source"].includes(key) ? "text" : "number"
                      }
                      step="any"
                      value={m[key] ?? ""}
                      onChange={(e) =>
                        update((d) => {
                          const row = d.materials.find((x) => x.id === m.id)!;
                          row[key] = ["name", "source"].includes(key)
                            ? e.target.value
                            : e.target.value === ""
                              ? null
                              : Number(e.target.value);
                        })
                      }
                    />
                  </label>
                ))}
                <p className="sim-help">
                  Plastic true stress/strain curves and their provenance can be
                  entered in Advanced setup. No calibrated properties are
                  generated.
                </p>
              </details>
            ))}
          </details>
          <details open>
            <summary>Load cases and boundaries</summary>
            <div className="sim-toolbar">
              <SelectControl
                aria-label="Load case"
                value={loadCase?.id ?? ""}
                onChange={(e) => {
                  setCaseId(e.target.value);
                  setHistoryText("");
                }}
              >
                {study.cases.map((c: Row) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </SelectControl>
              <Button
                onClick={() =>
                  editStudy((s) =>
                    s.cases.push({
                      id: id("case"),
                      name: `Load case ${s.cases.length + 1}`,
                      supports: [],
                      loads: [],
                      thermal: [],
                      history: [],
                    }),
                  )
                }
              >
                Add case
              </Button>
            </div>
            {loadCase && (
              <label>
                Case name
                <Input
                  value={loadCase.name}
                  onChange={(e) =>
                    editCase((c) => {
                      c.name = e.target.value;
                    })
                  }
                />
              </label>
            )}
            <p className="sim-help">
              Selected face:{" "}
              {props.selection?.id ?? "Select a face in the viewport"}
            </p>
            <div className="sim-toolbar">
              <Button
                onClick={() => addBoundary("support")}
                disabled={!props.selection}
              >
                Add support
              </Button>
              <Button
                onClick={() => addBoundary("load")}
                disabled={!props.selection}
              >
                Add load
              </Button>
              {thermal && (
                <Button
                  onClick={() => addBoundary("thermal")}
                  disabled={!props.selection}
                >
                  Add thermal boundary
                </Button>
              )}
            </div>
            {loadCase?.supports.map((s: Row, index: number) => (
              <div className="sim-boundary" key={s.id}>
                <code title={s.region_id}>{s.region_id}</code>
                <SelectControl
                  aria-label={`Support ${index + 1} type`}
                  value={s.kind}
                  onChange={(e) =>
                    editCase((c) => {
                      c.supports[index].kind = e.target.value;
                      c.supports[index].components ??= [true, true, true];
                    })
                  }
                >
                  <option value="fixed">Fixed translations</option>
                  <option value="partial">Partial translations</option>
                  <option value="frictionless">
                    Frictionless plane normal
                  </option>
                </SelectControl>
                {s.kind === "partial" && (
                  <div className="sim-toolbar">
                    {["X", "Y", "Z"].map((axis, i) => (
                      <label key={axis}>
                        <input
                          type="checkbox"
                          checked={s.components?.[i] ?? true}
                          onChange={(e) =>
                            editCase((c) => {
                              c.supports[index].components[i] =
                                e.target.checked;
                            })
                          }
                        />
                        {axis}
                      </label>
                    ))}
                  </div>
                )}
                <Button
                  onClick={() => editCase((c) => c.supports.splice(index, 1))}
                >
                  Remove support
                </Button>
              </div>
            ))}
            {loadCase?.loads.map((l: Row, index: number) => (
              <div className="sim-boundary" key={l.id}>
                <code title={l.region_id}>{l.region_id}</code>
                <SelectControl
                  aria-label={`Load ${index + 1} type`}
                  value={l.kind}
                  onChange={(e) =>
                    editCase((c) => {
                      c.loads[index].kind = e.target.value;
                    })
                  }
                >
                  {[
                    "force",
                    "pressure",
                    "moment",
                    "gravity",
                    "remote",
                    "bearing",
                    "hydrostatic",
                  ].map((kind) => (
                    <option key={kind} value={kind}>
                      {kind}
                    </option>
                  ))}
                </SelectControl>
                {l.kind === "pressure" ? (
                  <label>
                    Pressure (MPa, positive compression)
                    <Input
                      type="number"
                      step="any"
                      value={l.magnitude}
                      onChange={(e) =>
                        editCase((c) => {
                          c.loads[index].magnitude = Number(e.target.value);
                        })
                      }
                    />
                  </label>
                ) : (
                  <>
                    <span className="sim-help">
                      World vector (
                      {l.kind === "moment"
                        ? "N·mm"
                        : ["gravity", "hydrostatic"].includes(l.kind)
                          ? "mm/s²"
                          : "N"}
                      )
                    </span>
                    <div className="sim-vector">
                      {["X", "Y", "Z"].map((axis, i) => (
                        <Input
                          key={axis}
                          aria-label={`Load ${index + 1} ${axis}`}
                          type="number"
                          step="any"
                          value={l.vector?.[i] ?? 0}
                          onChange={(e) =>
                            editCase((c) => {
                              c.loads[index].vector[i] = Number(e.target.value);
                            })
                          }
                        />
                      ))}
                    </div>
                  </>
                )}
                <label>
                  Load source
                  <Input
                    value={l.source}
                    onChange={(e) =>
                      editCase((c) => {
                        c.loads[index].source = e.target.value;
                      })
                    }
                  />
                </label>
                <Button
                  onClick={() => editCase((c) => c.loads.splice(index, 1))}
                >
                  Remove load
                </Button>
              </div>
            ))}
            {loadCase?.thermal.map((b: Row, index: number) => (
              <div className="sim-boundary" key={b.id}>
                <code>{b.region_id}</code>
                <SelectControl
                  aria-label={`Thermal boundary ${index + 1} type`}
                  value={b.kind}
                  onChange={(e) =>
                    editCase((c) => {
                      c.thermal[index].kind = e.target.value;
                    })
                  }
                >
                  {[
                    "temperature",
                    "heat_power",
                    "heat_flux",
                    "convection",
                    "radiation",
                  ].map((kind) => (
                    <option key={kind} value={kind}>
                      {kind}
                    </option>
                  ))}
                </SelectControl>
                <label>
                  {
                    (
                      {
                        temperature: "Temperature (K)",
                        heat_power: "Total heat power (W)",
                        heat_flux: "Heat flux (W/mm²)",
                        convection: "Film coefficient (W/m²·K)",
                        radiation: "Emissivity (0–1)",
                      } as Row
                    )[b.kind]
                  }
                  <Input
                    type="number"
                    step="any"
                    value={b.value}
                    onChange={(e) =>
                      editCase((c) => {
                        c.thermal[index].value = Number(e.target.value);
                      })
                    }
                  />
                </label>
                {["convection", "radiation"].includes(b.kind) && (
                  <label>
                    Ambient (K)
                    <Input
                      type="number"
                      value={b.ambient_k ?? ""}
                      onChange={(e) =>
                        editCase((c) => {
                          c.thermal[index].ambient_k = Number(e.target.value);
                        })
                      }
                    />
                  </label>
                )}
                <label>
                  Thermal input source
                  <Input
                    value={b.source}
                    onChange={(e) =>
                      editCase((c) => {
                        c.thermal[index].source = e.target.value;
                      })
                    }
                  />
                </label>
                <Button
                  onClick={() => editCase((c) => c.thermal.splice(index, 1))}
                >
                  Remove boundary
                </Button>
              </div>
            ))}
            {study.type === "thermal_stress" && (
              <label>
                Stress-free temperature (K)
                <Input
                  type="number"
                  value={study.stress_free_temperature_k ?? ""}
                  onChange={(e) =>
                    editStudy((s) => {
                      s.stress_free_temperature_k = Number(e.target.value);
                    })
                  }
                />
              </label>
            )}
            {event && (
              <>
                <label>
                  Physical event duration (s)
                  <Input
                    type="number"
                    step="any"
                    value={study.settings.duration_s ?? 0.01}
                    onChange={(e) =>
                      editStudy((s) => {
                        s.settings.duration_s = Number(e.target.value);
                      })
                    }
                  />
                </label>
                <label>
                  Time (s), load multiplier per line
                  <textarea
                    value={
                      historyText ||
                      (loadCase?.history ?? [])
                        .map((p: number[]) => p.join(", "))
                        .join("\n")
                    }
                    onChange={(e) => setHistoryText(e.target.value)}
                    placeholder={"0, 0\n0.001, 1"}
                  />
                </label>
                <Button
                  onClick={() => {
                    try {
                      const history = historyText
                        .split("\n")
                        .filter(Boolean)
                        .map((line) => {
                          const row = line.split(",").map(Number);
                          if (row.length !== 2 || !row.every(Number.isFinite))
                            throw new Error(
                              "Each history row needs time, multiplier.",
                            );
                          return row;
                        });
                      editCase((c) => {
                        c.history = history;
                      });
                    } catch (e) {
                      props.setError(String(e));
                    }
                  }}
                >
                  Apply history
                </Button>
              </>
            )}
          </details>
          {(study.type === "linear_static" || study.interfaces?.length > 0) && (
            <details>
              <summary>Bonded interfaces</summary>
              <p className="sim-help">
                Transfer loads between coincident faces using a perfect bond.
                Supply the engineering source and mapping tolerance. This
                assumption has no adhesive thickness, compliance, separation or
                friction.
              </p>
              <Button
                disabled={
                  study.type !== "linear_static" || elementFamily !== "C3D10"
                }
                onClick={() =>
                  editStudy((draft) => {
                    draft.interfaces ??= [];
                    draft.interfaces.push({
                      id: id("bond"),
                      kind: "bonded",
                      formulation: "quadratic_surface_mortar",
                      primary_region_id: "",
                      secondary_region_id: "",
                      position_tolerance_mm: null,
                      source: "",
                    });
                  })
                }
              >
                Add bonded interface
              </Button>
              {study.interfaces?.map(
                (bond: Row, index: number) =>
                  bond.formulation === "quadratic_surface_mortar" && (
                    <div className="sim-boundary" key={bond.id}>
                      {(["primary", "secondary"] as const).map((role) => (
                        <label key={role}>
                          {role === "primary"
                            ? "Primary face region"
                            : "Secondary face region"}
                          <SelectControl
                            aria-label={`Bond ${index + 1} ${role} region`}
                            value={bond[`${role}_region_id`] ?? ""}
                            onChange={(event) =>
                              editStudy((draft) => {
                                draft.interfaces[index][`${role}_region_id`] =
                                  event.target.value;
                              })
                            }
                          >
                            <option value="">Choose a face region</option>
                            {model?.regions?.map((region: Row) => (
                              <option key={region.id} value={region.id}>
                                {region.id}
                              </option>
                            ))}
                          </SelectControl>
                          <Button
                            disabled={props.selection?.kind !== "face"}
                            onClick={() =>
                              chooseInterfaceFace(index, `${role}_region_id`)
                            }
                          >
                            Use selected {role} face
                          </Button>
                        </label>
                      ))}
                      <label>
                        Mapping tolerance (mm)
                        <Input
                          aria-label={`Bond ${index + 1} mapping tolerance (mm)`}
                          type="number"
                          min="0"
                          step="any"
                          value={bond.position_tolerance_mm ?? ""}
                          onChange={(event) =>
                            editStudy((draft) => {
                              draft.interfaces[index].position_tolerance_mm =
                                event.target.value === ""
                                  ? null
                                  : Number(event.target.value);
                            })
                          }
                        />
                      </label>
                      <label>
                        Bond assumption source
                        <Input
                          aria-label={`Bond ${index + 1} source`}
                          value={bond.source}
                          onChange={(event) =>
                            editStudy((draft) => {
                              draft.interfaces[index].source =
                                event.target.value;
                            })
                          }
                        />
                      </label>
                      <label className="sim-checkbox">
                        <input
                          type="checkbox"
                          checked={bond.kind !== "disabled"}
                          onChange={(event) =>
                            editStudy((draft) => {
                              draft.interfaces[index].kind = event.target
                                .checked
                                ? "bonded"
                                : "disabled";
                            })
                          }
                        />
                        Enabled
                      </label>
                      <Button
                        onClick={() =>
                          editStudy((draft) => {
                            draft.interfaces.splice(index, 1);
                          })
                        }
                      >
                        Remove bond
                      </Button>
                    </div>
                  ),
              )}
            </details>
          )}
          {(study.type === "nonlinear_static" ||
            study.interfaces?.some(
              (item: Row) => item.formulation === "surface_to_surface_penalty",
            )) && (
            <details>
              <summary>Surface contact</summary>
              <p className="sim-help">
                Experimental quadratic face contact with separation and Coulomb
                friction. Supply all properties and their source. Results remain
                inconclusive while active contact stability and frictional work
                are unqualified. Geometry is preserved; positive opening is
                clipped in solver contact fields.
              </p>
              <Button
                disabled={
                  study.type !== "nonlinear_static" || elementFamily !== "C3D10"
                }
                onClick={() =>
                  editStudy((draft) => {
                    draft.interfaces ??= [];
                    draft.interfaces.push({
                      id: id("contact"),
                      kind: "separation",
                      formulation: "surface_to_surface_penalty",
                      primary_region_id: "",
                      secondary_region_id: "",
                      position_tolerance_mm: null,
                      normal_penalty_n_mm3: null,
                      friction_coefficient: null,
                      tangential_penalty_n_mm3: null,
                      maximum_penetration_mm: null,
                      source: "",
                    });
                  })
                }
              >
                Add surface contact
              </Button>
              {study.interfaces?.map(
                (contact: Row, index: number) =>
                  contact.formulation === "surface_to_surface_penalty" && (
                    <div className="sim-boundary" key={contact.id}>
                      {(["primary", "secondary"] as const).map((role) => (
                        <label key={role}>
                          {role === "primary"
                            ? "Primary contact region"
                            : "Secondary contact region"}
                          <SelectControl
                            aria-label={`Contact ${index + 1} ${role} region`}
                            value={contact[`${role}_region_id`] ?? ""}
                            onChange={(event) =>
                              editStudy((draft) => {
                                draft.interfaces[index][`${role}_region_id`] =
                                  event.target.value;
                              })
                            }
                          >
                            <option value="">Choose a face region</option>
                            {model?.regions?.map((region: Row) => (
                              <option key={region.id} value={region.id}>
                                {region.id}
                              </option>
                            ))}
                          </SelectControl>
                          <Button
                            disabled={props.selection?.kind !== "face"}
                            onClick={() =>
                              chooseInterfaceFace(index, `${role}_region_id`)
                            }
                          >
                            Use selected {role} contact face
                          </Button>
                        </label>
                      ))}
                      {(
                        [
                          ["normal_penalty_n_mm3", "Normal penalty (N/mm³)"],
                          [
                            "friction_coefficient",
                            "Friction coefficient (0 for frictionless)",
                          ],
                          [
                            "tangential_penalty_n_mm3",
                            "Tangential penalty (N/mm³, positive friction only)",
                          ],
                          [
                            "maximum_penetration_mm",
                            "Maximum permitted penetration (mm)",
                          ],
                          [
                            "position_tolerance_mm",
                            "Initial contact geometry tolerance (mm)",
                          ],
                        ] as const
                      ).map(([field, label]) => (
                        <label key={field}>
                          {label}
                          <Input
                            aria-label={`Contact ${index + 1} ${label}`}
                            type="number"
                            min="0"
                            step="any"
                            value={contact[field] ?? ""}
                            onChange={(event) =>
                              editStudy((draft) => {
                                draft.interfaces[index][field] =
                                  event.target.value === ""
                                    ? null
                                    : Number(event.target.value);
                              })
                            }
                          />
                        </label>
                      ))}
                      <label>
                        Contact assumption and property source
                        <Input
                          aria-label={`Contact ${index + 1} source`}
                          value={contact.source}
                          onChange={(event) =>
                            editStudy((draft) => {
                              draft.interfaces[index].source =
                                event.target.value;
                            })
                          }
                        />
                      </label>
                      <label className="sim-checkbox">
                        <input
                          type="checkbox"
                          checked={contact.kind !== "disabled"}
                          onChange={(event) =>
                            editStudy((draft) => {
                              draft.interfaces[index].kind = event.target
                                .checked
                                ? "separation"
                                : "disabled";
                            })
                          }
                        />
                        Enabled
                      </label>
                      <Button
                        onClick={() =>
                          editStudy((draft) => {
                            draft.interfaces.splice(index, 1);
                          })
                        }
                      >
                        Remove contact
                      </Button>
                    </div>
                  ),
              )}
            </details>
          )}
          <details open>
            <summary>Mesh and verification</summary>
            <label>
              Element family
              <SelectControl
                aria-label="Element family"
                value={elementFamily}
                onChange={(e) => setElementFamily(e.target.value)}
              >
                <option value="C3D10" disabled={!quadraticSupported}>
                  Quadratic tetrahedra · C3D10
                </option>
                <option value="C3D4">Linear tetrahedra · C3D4</option>
              </SelectControl>
            </label>
            <div className="sim-vector">
              <label>
                Initial size (mm)
                <Input
                  type="number"
                  min="0.01"
                  step="any"
                  value={meshSize}
                  onChange={(e) => setMeshSize(Number(e.target.value))}
                />
              </label>
              <label>
                Mesh levels
                <Input
                  type="number"
                  min="1"
                  max="4"
                  value={refinements}
                  onChange={(e) => setRefinements(Number(e.target.value))}
                />
              </label>
            </div>
            <p className="sim-help">
              {elementFamily === "C3D10"
                ? "Quadratic geometry with static/nonlinear CalculiX transfer. Use fixed or axis-partial supports and force, pressure, gravity or remote loads. Curved cells are checked before solving."
                : "Linear tetrahedra. Bending and thin sections require particular care and refinement."}{" "}
              Stress maxima are screening values. Region ambiguity stops the
              job.
            </p>
            {(physicalProbeSupported || study.settings.probe_point_mm) && (
              <>
                <label>
                  Displacement probe (mm)
                  <Input
                    value={probeText}
                    placeholder="x, y, z"
                    onChange={(e) => setProbeText(e.target.value)}
                  />
                </label>
                <label>
                  Probe body
                  <SelectControl
                    aria-label="Probe body"
                    value={
                      study.settings.probe_body_id ?? model?.body_ids?.[0] ?? ""
                    }
                    onChange={(e) =>
                      editStudy((s) => {
                        s.settings.probe_body_id = e.target.value;
                      })
                    }
                  >
                    {(model?.body_ids ?? []).map((body: string) => (
                      <option key={body} value={body}>
                        {sceneBodies.find((b) => b.id === body)?.name ?? body}
                      </option>
                    ))}
                  </SelectControl>
                </label>
                <div className="sim-toolbar">
                  <Button
                    disabled={!validProbe || !physicalProbeSupported}
                    onClick={() =>
                      editStudy((s) => {
                        s.settings.probe_point_mm = probeValues;
                        s.settings.probe_body_id ??=
                          model?.body_ids?.[0] ?? null;
                      })
                    }
                  >
                    Set probe
                  </Button>
                  <Button
                    disabled={!study.settings.probe_point_mm}
                    onClick={() =>
                      editStudy((s) => {
                        s.settings.probe_point_mm = null;
                        s.settings.probe_body_id = null;
                      })
                    }
                  >
                    Clear probe
                  </Button>
                </div>
                <p className="sim-help">
                  {physicalProbeSupported
                    ? "Choose a point inside the selected solid. The same physical point is checked on every mesh. Quadratic results remain inconclusive until a probe is supplied."
                    : "This study type does not accept a physical displacement probe. Clear the saved probe or choose a supported static study."}
                </p>
              </>
            )}
            <label className="sim-checkbox">
              <input
                type="checkbox"
                checked={study.assumptions_confirmed}
                onChange={(e) =>
                  editStudy((s) => {
                    s.assumptions_confirmed = e.target.checked;
                  })
                }
              />
              I reviewed the loads, material sources and stated study
              assumptions.
            </label>
            <div className="sim-toolbar">
              <Button
                disabled={dirty || !!busy}
                onClick={() =>
                  void act("Meshing", async () => {
                    await run("mesh", {
                      mesh_size_mm: meshSize,
                      refinements,
                      element_family: elementFamily,
                    });
                    await props.refresh();
                  })
                }
              >
                Generate mesh
              </Button>
              <Button
                disabled={
                  dirty ||
                  !!busy ||
                  !model?.mesh ||
                  !study.assumptions_confirmed
                }
                variant="primary"
                onClick={() =>
                  void act("Solving", async () => {
                    const value = await run("solve");
                    setResults(value);
                    setTab("results");
                  })
                }
              >
                Solve all cases
              </Button>
              <Button disabled={dirty || !!busy} onClick={inspect}>
                Inspect setup
              </Button>
            </div>
            {currentInspection?.errors?.length > 0 && (
              <ul className="sim-notice">
                {currentInspection.errors.map((error: string) => (
                  <li key={error}>{error}</li>
                ))}
              </ul>
            )}
          </details>
          <details>
            <summary>Advanced setup</summary>
            <p className="sim-help">
              Versioned JSON supports contact node pairs, axial bolts, sourced
              plastic curves, pin supports, search preserve/obstacle cells,
              requirements and solver budgets. Unsupported adapters refuse
              execution.
            </p>
            <Button onClick={() => setAdvanced(JSON.stringify(data, null, 2))}>
              Edit complete setup
            </Button>
            {advanced && (
              <>
                <textarea
                  className="sim-json"
                  value={advanced}
                  onChange={(e) => setAdvanced(e.target.value)}
                  aria-label="Advanced simulation setup"
                />
                <Button
                  onClick={() => {
                    try {
                      setData(parse(JSON.parse(advanced)));
                      setAdvanced("");
                    } catch (e) {
                      props.setError(String(e));
                    }
                  }}
                >
                  Apply JSON draft
                </Button>
              </>
            )}
          </details>
        </>
      )}
      {tab === "results" && (
        <>
          <div className="sim-toolbar">
            <Button disabled={dirty || !study || !!busy} onClick={loadResults}>
              Load latest results
            </Button>
            <Button
              disabled={dirty || !study || !!busy}
              onClick={() =>
                void act("Comparing", async () => {
                  const compared = await run("compare", {
                    study_ids: data.studies.map((s) => s.id),
                  });
                  setInspection(compared);
                })
              }
            >
              Compare studies
            </Button>
          </div>
          {inspection?.studies
            ?.filter((s: Row) => s.results)
            .map((s: Row) => (
              <details key={s.study_id}>
                <summary>
                  {data.studies.find((x) => x.id === s.study_id)?.name} ·{" "}
                  {s.stale ? "Stale" : "Current inputs"}
                </summary>
                {s.results.map((r: Row) => (
                  <div key={r.case_id}>
                    <strong>{r.case_id}</strong>
                    <dl className="sim-values">
                      {Object.entries(r.values).map(([k, v]) => (
                        <div key={k}>
                          <dt>{k.replaceAll("_", " ")}</dt>
                          <dd>{format(v)}</dd>
                        </div>
                      ))}
                    </dl>
                  </div>
                ))}
              </details>
            ))}
          {results && (
            <>
              <p className={results.stale ? "sim-notice" : "sim-help"}>
                Revision {results.revision} ·{" "}
                {results.stale
                  ? "Stale result: inputs changed"
                  : "Exact input snapshot retained"}
              </p>
              {results.results?.map((r: Row) => (
                <details key={r.case_id} open>
                  <summary>
                    {study?.cases.find((c: Row) => c.id === r.case_id)?.name ??
                      r.case_id}{" "}
                    · {r.numerical_state.replaceAll("_", " ")}
                  </summary>
                  <p className="sim-help">
                    Requirements: {r.requirements_state.replaceAll("_", " ")} ·
                    Hardware correlation:{" "}
                    {r.physical_validation.replaceAll("_", " ")}
                  </p>
                  <dl className="sim-values">
                    {Object.entries(r.values).map(([k, v]) => (
                      <div key={k}>
                        <dt>{k.replaceAll("_", " ")}</dt>
                        <dd>{format(v)}</dd>
                      </div>
                    ))}
                  </dl>
                  {r.verification_gaps?.length > 0 && (
                    <ul className="sim-notice">
                      {r.verification_gaps.map((gap: string) => (
                        <li key={gap}>{gap}</li>
                      ))}
                    </ul>
                  )}
                  {r.findings?.map((f: Row) => (
                    <p key={f.id}>
                      {f.id}: {format(f.value)}{" "}
                      {f.operator === "le" ? "≤" : "≥"} {format(f.limit)} ·{" "}
                      {f.verified
                        ? f.satisfied
                          ? "satisfied within numerical model"
                          : "not satisfied"
                        : "unverified"}
                    </p>
                  ))}
                  <table>
                    <thead>
                      <tr>
                        <th>Mesh</th>
                        <th>Nodes</th>
                        <th>Elements</th>
                        <th>Change</th>
                      </tr>
                    </thead>
                    <tbody>
                      {r.mesh_iterations.map((m: Row, i: number) => (
                        <tr key={i}>
                          <td>{i + 1}</td>
                          <td>{m.nodes}</td>
                          <td>{m.elements}</td>
                          <td>
                            {Object.entries(m.relative_changes)
                              .map(
                                ([k, v]) =>
                                  `${k}: ${(Number(v) * 100).toFixed(2)}%`,
                              )
                              .join(", ") || "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <div className="sim-toolbar">
                    <Button onClick={() => void loadField(r.field_artifact)}>
                      View numerical field
                    </Button>
                    <a href={artifact(r.field_artifact)} download>
                      Field JSON
                    </a>
                    {r.interface_artifact && (
                      <a href={artifact(r.interface_artifact)} download>
                        {r.study_type === "nonlinear_static"
                          ? "Contact interface fields"
                          : "Bond interface fields"}
                      </a>
                    )}
                    {r.reference_load_artifact && (
                      <a href={artifact(r.reference_load_artifact)} download>
                        Reference-load fields
                      </a>
                    )}
                    <a href={artifact(r.input_artifact)} download>
                      Exact inputs
                    </a>
                  </div>
                  <details>
                    <summary>Equation, balance and energy checks</summary>
                    <pre>{JSON.stringify(r.checks, null, 2)}</pre>
                  </details>
                  <details>
                    <summary>Assumptions and limitations</summary>
                    <ul>
                      {r.warnings.map((w: string) => (
                        <li key={w}>{w}</li>
                      ))}
                    </ul>
                  </details>
                </details>
              ))}
              <div className="sim-toolbar">
                <Button
                  onClick={() =>
                    void act("Writing report", async () => {
                      const report = await run("report");
                      window.open(
                        artifact(String(report.artifact)),
                        "_blank",
                        "noopener",
                      );
                    })
                  }
                >
                  Open evidence report
                </Button>
                <Button
                  onClick={() =>
                    void act("Exporting", async () => {
                      const exported = await run("handoff");
                      window.open(
                        artifact(String(exported.artifact)),
                        "_blank",
                        "noopener",
                      );
                    })
                  }
                >
                  Export analysis package
                </Button>
              </div>
            </>
          )}
          {field && (
            <details open>
              <summary>Numerical field</summary>
              <SelectControl
                aria-label="Field quantity"
                value={quantity}
                onChange={(e) => setQuantity(e.target.value)}
              >
                <option value="displacement">
                  Displacement magnitude (mm)
                </option>
                <option value="temperature">Temperature (K)</option>
                <option value="stress">Von Mises stress (MPa)</option>
                {field.nodal_contact && (
                  <>
                    <option value="contact_pressure">
                      Contact pressure (MPa)
                    </option>
                    <option value="contact_shear">
                      Contact shear magnitude (MPa)
                    </option>
                    <option value="contact_penetration">
                      Contact penetration, negative (mm)
                    </option>
                  </>
                )}
                {field.element_density && (
                  <option value="density">
                    Relative density · mesh guidance
                  </option>
                )}
                {field.modes && (
                  <option value="mode">Normalized mode shape</option>
                )}
              </SelectControl>
              {quantity === "mode" && (
                <label>
                  Mode
                  <Input
                    type="number"
                    min="1"
                    max={field.modes?.length}
                    value={mode + 1}
                    onChange={(e) => setMode(Number(e.target.value) - 1)}
                  />
                </label>
              )}
              {quantity === "mode" &&
                field.modes?.[mode]?.frequency_hz !== undefined && (
                  <p className="sim-help">
                    {field.modes[mode].rigid_body
                      ? "Rigid-body mode"
                      : "Elastic mode"}{" "}
                    · {field.modes[mode].frequency_hz?.toPrecision(6)} Hz
                  </p>
                )}
              {quantity === "mode" &&
                field.modes?.[mode]?.load_factor !== undefined && (
                  <p className="sim-help">
                    Ideal buckling factor ·{" "}
                    {field.modes[mode].load_factor?.toPrecision(6)} × reference
                    load
                  </p>
                )}
              <label>
                Deformation display scale ×
                <Input
                  type="number"
                  step="any"
                  value={scale}
                  onChange={(e) => setScale(Number(e.target.value))}
                />
              </label>
              {field.field_state === "final" && (
                <p className="sim-help">
                  Final field at recorded time {format(field.field_time_s)} s.
                  Scalar peaks span the recorded load history.
                </p>
              )}
              <FieldPreview
                field={field}
                quantity={quantity}
                scale={scale}
                mode={mode}
              />
              {quantity.startsWith("contact_") && (
                <p className="sim-help">
                  Secondary contact faces only; nodal recovery may smooth
                  integration-point peaks.
                  {field.contact_gap_convention}
                </p>
              )}
              <p className="sim-help">
                Blue → red: low → high. Display scale changes the picture. Mode
                amplitudes are normalized, not physical motion.
              </p>
            </details>
          )}
        </>
      )}
      {tab === "scope" && (
        <>
          <p className="sim-help">
            All 20 audited families. Experimental marks a bounded
            implementation; it does not establish complete Fusion parity.
          </p>
          {inspection?.capabilities?.map((c: Row) => (
            <details key={c.id}>
              <summary>
                {c.family} · {c.status}
              </summary>
              <p>{c.scope}</p>
            </details>
          ))}
          {!inspection?.capabilities && (
            <Button onClick={inspect}>Load capability details</Button>
          )}
        </>
      )}
    </section>
  );
}
