import type { ComponentType } from "react";
import {
  Activity,
  AlertTriangle,
  Lock,
  Play,
  Plus,
  ShieldCheck,
  X,
} from "lucide-react";
import {
  artifact,
  type DisplayField,
  type Project,
  type Selection,
  type State,
} from "../api";
import { Button, Input, SelectControl } from "./ui";

type Props = {
  current: Project;
  state: State;
  selection: Selection | null;
  blocked: boolean;
  edit: (fn: (p: Project) => void) => void;
  run: (kind: string) => void;
  onField: (field: DisplayField) => void;
  Field: ComponentType<any>;
  VectorField: ComponentType<any>;
};

export function StudyPanel({
  current,
  state,
  selection,
  blocked,
  edit,
  run,
  onField,
  Field,
  VectorField,
}: Props) {
  const study = current.study;
  const types = state.simulation_capabilities?.studies ?? [];
  const capability = types.find((v) => v.type === study.type);
  const thermal = study.type === "thermal" || study.type === "thermal_stress";
  const eigen = study.type === "modal" || study.type === "buckling";
  const mechanical = study.type !== "thermal";
  const result = state.analysis;
  const face =
    selection?.kind === "face" &&
    selection.face.kind === "PLANE" &&
    state.geometry_state === "current"
      ? selection.id
      : null;
  const addBoundary = (kind: "fixed" | "force" | "temperature" | "heat") => {
    if (!face) return;
    edit((p) => {
      const s = p.study;
      if (kind === "fixed" && !s.fixed_faces.includes(face))
        s.fixed_faces.push(face);
      if (kind === "force" && !s.forces.some((v) => v.face === face))
        s.forces.push({ face, vector_n: [0, 0, 0] });
      if (
        kind === "temperature" &&
        !s.temperatures.some((v) => v.face === face)
      )
        s.temperatures.push({ face, temperature_c: null });
      if (kind === "heat" && !s.heat_sources.some((v) => v.face === face))
        s.heat_sources.push({ face, power_w: null });
      s.assumptions_confirmed = false;
    });
  };
  const metric = (label: string, value: number | undefined, unit: string) =>
    value == null ? null : (
      <div className="result-metric" key={label}>
        <span>{label}</span>
        <strong>
          {value.toPrecision(4)} <small>{unit}</small>
        </strong>
      </div>
    );
  return (
    <>
      <Button
        className="wide-button"
        disabled={blocked}
        onClick={() => run("check")}
      >
        <ShieldCheck size={15} />
        Check fit
      </Button>
      <div className="inspector-title">
        <div className="feature-glyph">
          <Activity size={18} />
        </div>
        <h2>{capability?.label ?? study.type}</h2>
      </div>
      <label className="field">
        <span>Study type</span>
        <SelectControl
          aria-label="Study type"
          value={study.type}
          onChange={(e) =>
            edit((p) => {
              p.study.type = e.target.value as Project["study"]["type"];
              p.study.assumptions_confirmed = false;
              p.study.stress_probe_mm = null;
              if (["modal", "thermal"].includes(p.study.type))
                p.study.forces = [];
              if (p.study.type === "thermal") p.study.fixed_faces = [];
              if (!["thermal", "thermal_stress"].includes(p.study.type)) {
                p.study.temperatures = [];
                p.study.heat_sources = [];
              }
            })
          }
        >
          {types.map((v) => (
            <option
              key={v.type}
              value={v.type}
              disabled={v.status === "unavailable"}
            >
              {v.label}
              {v.status === "unavailable" ? " · unavailable" : ""}
            </option>
          ))}
        </SelectControl>
      </label>
      {face && (
        <div className="selection-card">
          <code>{face}</code>
          <div>
            {mechanical && (
              <Button onClick={() => addBoundary("fixed")}>
                <Lock size={13} />
                Fix face
              </Button>
            )}
            {mechanical && study.type !== "modal" && (
              <Button onClick={() => addBoundary("force")}>
                <Plus size={13} />
                Apply force
              </Button>
            )}
            {thermal && (
              <>
                <Button onClick={() => addBoundary("temperature")}>
                  Set temperature
                </Button>
                <Button onClick={() => addBoundary("heat")}>Apply heat</Button>
              </>
            )}
          </div>
        </div>
      )}
      <div className="section-heading">MATERIAL</div>
      {!current.material ? (
        <Button
          className="wide-button"
          onClick={() =>
            edit((p) => {
              p.material = {
                name: "User-defined material",
                youngs_modulus_mpa: null,
                poisson_ratio: null,
                density_g_cm3: null,
                source: "",
              };
            })
          }
        >
          <Plus size={14} />
          Define material
        </Button>
      ) : (
        <>
          <Field
            label="Material name"
            value={current.material.name}
            onChange={(v: string) => edit((p) => (p.material.name = v))}
          />
          {mechanical && (
            <>
              <Field
                label="Young’s modulus"
                type="number"
                unit="MPa"
                value={current.material.youngs_modulus_mpa}
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
            </>
          )}
          <Field
            label="Density"
            type="number"
            unit="g/cm³"
            value={current.material.density_g_cm3}
            onChange={(v: number) =>
              edit((p) => (p.material.density_g_cm3 = v))
            }
          />
          {thermal && (
            <Field
              label="Thermal conductivity"
              type="number"
              unit="W/(m K)"
              value={current.material.thermal_conductivity_w_m_k}
              onChange={(v: number) =>
                edit((p) => (p.material.thermal_conductivity_w_m_k = v))
              }
            />
          )}
          {study.type === "thermal_stress" && (
            <>
              <Field
                label="Thermal expansion"
                type="number"
                unit="1/K"
                value={current.material.thermal_expansion_per_k}
                onChange={(v: number) =>
                  edit((p) => (p.material.thermal_expansion_per_k = v))
                }
              />
              <Field
                label="Stress-free temperature"
                type="number"
                unit="°C"
                value={study.reference_temperature_c}
                onChange={(v: number) =>
                  edit((p) => (p.study.reference_temperature_c = v))
                }
              />
            </>
          )}
          <Field
            label="Material data source"
            value={current.material.source}
            onChange={(v: string) => edit((p) => (p.material.source = v))}
          />
        </>
      )}
      {mechanical && (
        <>
          <div className="section-heading">FIXED SUPPORTS</div>
          {!study.fixed_faces.length && (
            <p className="empty-note">No fixed faces selected</p>
          )}
          {study.fixed_faces.map((name, i) => (
            <div className="region-chip" key={name}>
              <Lock size={13} />
              <code>{name}</code>
              <Button
                title="Remove support"
                onClick={() => edit((p) => p.study.fixed_faces.splice(i, 1))}
              >
                <X size={13} />
              </Button>
            </div>
          ))}
          {study.type !== "modal" && (
            <>
              <div className="section-heading">DISTRIBUTED FORCES</div>
              {!study.forces.length && (
                <p className="empty-note">No applied forces</p>
              )}
              {study.forces.map((load, i) => (
                <div className="load-card" key={load.face}>
                  <div>
                    <code>{load.face}</code>
                    <Button
                      title="Remove force"
                      onClick={() => edit((p) => p.study.forces.splice(i, 1))}
                    >
                      <X size={13} />
                    </Button>
                  </div>
                  <VectorField
                    label="Force"
                    unit="N"
                    value={load.vector_n}
                    onChange={(v: number[]) =>
                      edit((p) => (p.study.forces[i].vector_n = v))
                    }
                  />
                </div>
              ))}
            </>
          )}
        </>
      )}
      {thermal && (
        <>
          <div className="section-heading">THERMAL BOUNDARIES</div>
          {!study.temperatures.length && (
            <p className="empty-note">Select a face and set its temperature.</p>
          )}
          {study.temperatures.map((v, i) => (
            <div className="load-card" key={v.face}>
              <div>
                <code>{v.face}</code>
                <Button
                  title="Remove temperature"
                  onClick={() => edit((p) => p.study.temperatures.splice(i, 1))}
                >
                  <X size={13} />
                </Button>
              </div>
              <Field
                label={`Temperature ${i + 1}`}
                type="number"
                unit="°C"
                value={v.temperature_c}
                onChange={(n: number) =>
                  edit((p) => (p.study.temperatures[i].temperature_c = n))
                }
              />
            </div>
          ))}
          {study.heat_sources.map((v, i) => (
            <div className="load-card" key={v.face}>
              <div>
                <code>{v.face}</code>
                <Button
                  title="Remove heat source"
                  onClick={() => edit((p) => p.study.heat_sources.splice(i, 1))}
                >
                  <X size={13} />
                </Button>
              </div>
              <Field
                label={`Heat power ${i + 1}`}
                type="number"
                unit="W"
                value={v.power_w}
                onChange={(n: number) =>
                  edit((p) => (p.study.heat_sources[i].power_w = n))
                }
              />
            </div>
          ))}
        </>
      )}
      <div className="section-heading">NUMERICAL SETTINGS</div>
      <label className="field">
        <span>Mesher</span>
        <SelectControl
          aria-label="Mesher"
          value={study.mesher}
          onChange={(e) =>
            edit((p) => (p.study.mesher = e.target.value as "netgen" | "gmsh"))
          }
        >
          <option value="netgen">Netgen</option>
          <option value="gmsh">Gmsh</option>
        </SelectControl>
      </label>
      {eigen && (
        <Field
          label="Number of modes"
          type="number"
          value={study.mode_count}
          onChange={(v: number) => edit((p) => (p.study.mode_count = v))}
        />
      )}
      <Field
        label="Initial mesh size"
        type="number"
        unit="mm"
        value={study.mesh_size_mm}
        onChange={(v: number) => edit((p) => (p.study.mesh_size_mm = v))}
      />
      <Field
        label="Refinement steps"
        type="number"
        value={study.refinement_steps}
        onChange={(v: number) => edit((p) => (p.study.refinement_steps = v))}
      />
      <Field
        label="Convergence tolerance"
        type="number"
        value={study.convergence_tolerance}
        onChange={(v: number) =>
          edit((p) => (p.study.convergence_tolerance = v))
        }
      />
      {!eigen &&
        mechanical &&
        (study.stress_probe_mm ? (
          <>
            <VectorField
              label="Stress probe"
              value={study.stress_probe_mm}
              onChange={(v: number[]) =>
                edit((p) => (p.study.stress_probe_mm = v))
              }
            />
            <Button
              className="text-button"
              onClick={() => edit((p) => (p.study.stress_probe_mm = null))}
            >
              Remove stress probe
            </Button>
          </>
        ) : (
          <Button
            className="wide-button"
            onClick={() =>
              edit(
                (p) =>
                  (p.study.stress_probe_mm = selection?.point ?? [0, 0, 0]),
              )
            }
          >
            Add named stress probe
          </Button>
        ))}
      <label className="assumptions">
        <Input
          type="checkbox"
          checked={study.assumptions_confirmed}
          onChange={(e) =>
            edit((p) => (p.study.assumptions_confirmed = e.target.checked))
          }
        />
        <span>
          {thermal
            ? "I have reviewed the material, heat sources and temperature boundaries. Steady conduction and constant isotropic properties apply."
            : eigen
              ? "I have reviewed the material, supports and load case. Linear eigenmodes are idealized and have arbitrary amplitudes."
              : "I have reviewed the supports, forces and material data. Small deformation, linear elasticity and negligible dynamic effects apply."}
        </span>
      </label>
      <Button
        className="full-width"
        variant="primary"
        disabled={
          blocked ||
          !study.assumptions_confirmed ||
          capability?.status === "unavailable"
        }
        onClick={() => run("simulate")}
      >
        <Play size={15} />
        {study.type === "linear_static" ? "Run static study" : "Run study"}
      </Button>
      <div className="section-heading">
        RESULTS{" "}
        <span className="result-state">
          {state.analysis_state.replaceAll("_", " ")}
        </span>
      </div>
      {result ? (
        <>
          {metric("Maximum displacement", result.max_displacement_mm, "mm")}
          {metric("Peak von Mises stress", result.max_stress_mpa, "MPa")}
          {metric("Maximum temperature", result.max_temperature_c, "°C")}
          {metric("Minimum temperature", result.min_temperature_c, "°C")}
          {(result.frequencies_hz ?? result.buckling_factors ?? []).map(
            (v: number, i: number) =>
              metric(
                `Mode ${i + 1}`,
                v,
                result.frequencies_hz ? "Hz" : "load multiplier",
              ),
          )}
          <div className="two-fields">
            {result.max_stress_mpa != null && (
              <Button className="wide-button" onClick={() => onField("stress")}>
                View stress
              </Button>
            )}
            {result.max_displacement_mm != null && (
              <Button
                className="wide-button"
                onClick={() => onField("displacement")}
              >
                View displacement
              </Button>
            )}
            {result.max_temperature_c != null && (
              <Button
                className="wide-button"
                onClick={() => onField("temperature")}
              >
                View temperature
              </Button>
            )}
            {(result.frequencies_hz || result.buckling_factors) && (
              <Button className="wide-button" onClick={() => onField("mode")}>
                View mode
              </Button>
            )}
          </div>
          {result.force_balance_error != null && (
            <p className="muted">
              Reaction balance error:{" "}
              {(result.force_balance_error * 100).toFixed(3)}%
            </p>
          )}
          {result.heat_balance_error != null && (
            <p className="muted">
              Heat balance error: {(result.heat_balance_error * 100).toFixed(3)}
              %
            </p>
          )}
          {result.iterations.map((it: any, i: number) => (
            <div className="iteration-row" key={i}>
              <span>
                Mesh {i + 1} · {it.nodes} nodes
              </span>
              <strong>
                {it.max_displacement_mm != null
                  ? `${it.max_displacement_mm.toPrecision(4)} mm`
                  : it.max_temperature_c != null
                    ? `${it.max_temperature_c.toPrecision(4)} °C`
                    : `${(it.frequencies_hz ?? it.buckling_factors)?.[0]?.toPrecision(4)}`}
              </strong>
            </div>
          ))}
          {result.findings.map((v: any) => (
            <p className="muted" key={v.id}>
              {v.id}: {v.status ?? (v.passed ? "passed" : "failed")}
            </p>
          ))}
          {result.warnings.map((w: string) => (
            <p className="study-warning" key={w}>
              <AlertTriangle size={13} />
              {w}
            </p>
          ))}
          {result.artifacts?.study_package && (
            <a href={artifact(result.artifacts.study_package)} download>
              Study input package
            </a>
          )}
        </>
      ) : (
        <p className="empty-note">No study results.</p>
      )}
    </>
  );
}
