import { Button, Input, SelectControl } from "../../components/ui";
type Row = Record<string, any>;
export default function FlowSetup({
  study,
  loadCase,
  editStudy,
  editCase,
}: {
  study: Row;
  loadCase?: Row;
  editStudy: (fn: (s: Row) => void) => void;
  editCase: (fn: (c: Row) => void) => void;
}) {
  return (
    <>
      <p className="sim-help">
        Experimental steady laminar incompressible Newtonian flow.
        Pressure-field accuracy remains unresolved; every flow result stays
        inconclusive. Supply the fluid volume itself and assign a sourced fluid
        material. Heat transfer, turbulence, buoyancy, moving geometry and
        compressibility are unavailable.
      </p>
      <label>
        <input
          type="checkbox"
          checked={!!study.flow}
          onChange={(e) =>
            editStudy((s) => {
              s.flow = e.target.checked
                ? {
                    domain_kind: "fluid_volume",
                    model: "steady_laminar_incompressible_newtonian",
                    control_volumes: "barycentric_hexahedra",
                    source: "",
                    maximum_iterations: 1000,
                    pressure_reference_pa: null,
                  }
                : null;
            })
          }
        />
        The selected geometry represents the fluid volume
      </label>
      {study.flow && (
        <>
          <label>
            Fluid model and domain source
            <Input
              value={study.flow.source}
              onChange={(e) =>
                editStudy((s) => {
                  s.flow.source = e.target.value;
                })
              }
            />
          </label>
          <label>
            Closed-volume gauge reference (Pa; optional with a pressure
            boundary)
            <Input
              type="number"
              step="any"
              value={study.flow.pressure_reference_pa ?? ""}
              onChange={(e) =>
                editStudy((s) => {
                  s.flow.pressure_reference_pa =
                    e.target.value === "" ? null : Number(e.target.value);
                })
              }
            />
          </label>
          <label>
            Maximum SIMPLE iterations
            <Input
              type="number"
              min="20"
              max="2000"
              value={study.flow.maximum_iterations}
              onChange={(e) =>
                editStudy((s) => {
                  s.flow.maximum_iterations = Number(e.target.value);
                })
              }
            />
          </label>
        </>
      )}
      {(loadCase?.flow ?? []).map((b: Row, index: number) => (
        <div className="sim-boundary" key={b.id}>
          <code title={b.region_id}>{b.region_id}</code>
          <SelectControl
            aria-label={`Fluid boundary ${index + 1} type`}
            value={b.kind}
            onChange={(e) =>
              editCase((c) => {
                const row = c.flow[index];
                row.kind = e.target.value;
                row.velocity_m_s = ["velocity", "moving_wall"].includes(
                  row.kind,
                )
                  ? [0, 0, 0]
                  : null;
                row.pressure_gauge_pa = row.kind === "pressure" ? 0 : null;
              })
            }
          >
            <option value="no_slip">Stationary no-slip wall</option>
            <option value="moving_wall">Tangential moving wall</option>
            <option value="velocity">Velocity boundary</option>
            <option value="pressure">Gauge-pressure boundary</option>
            <option value="symmetry">Symmetry plane</option>
          </SelectControl>
          {b.velocity_m_s &&
            ["X", "Y", "Z"].map((axis, i) => (
              <label key={axis}>
                Velocity {axis} (m/s)
                <Input
                  type="number"
                  step="any"
                  value={b.velocity_m_s[i]}
                  onChange={(e) =>
                    editCase((c) => {
                      c.flow[index].velocity_m_s[i] = Number(e.target.value);
                    })
                  }
                />
              </label>
            ))}
          {b.kind === "pressure" && (
            <label>
              Gauge pressure (Pa)
              <Input
                type="number"
                step="any"
                value={b.pressure_gauge_pa ?? ""}
                onChange={(e) =>
                  editCase((c) => {
                    c.flow[index].pressure_gauge_pa = Number(e.target.value);
                  })
                }
              />
            </label>
          )}
          <label>
            Fluid boundary source
            <Input
              value={b.source}
              onChange={(e) =>
                editCase((c) => {
                  c.flow[index].source = e.target.value;
                })
              }
            />
          </label>
          <Button onClick={() => editCase((c) => c.flow.splice(index, 1))}>
            Remove fluid boundary
          </Button>
        </div>
      ))}
      <p className="sim-help">
        Assign every exterior face exactly once. Velocity uses m/s, gauge
        pressure uses Pa, and material viscosity uses Pa·s. Display fields are
        recovered from native finite-volume cells; no displacement deformation
        is applied.
      </p>
    </>
  );
}
