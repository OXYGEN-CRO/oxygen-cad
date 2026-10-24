/* Browser form fixture. It never pretends to execute a physics worker. */
import { useState } from "react";
import { createRoot } from "react-dom/client";
import SimulationPanel from "../Panel";
import FieldPreview from "../FieldPreview";
import referenceField from "./axial-field.json";
import type { Project, State, Selection } from "../../../api";
import type { JsonObject } from "../../contracts";
import "../../../style.css";
const face = {
  id: "part:xmax",
  signature: "reference-face",
  kind: "PLANE",
  area_mm2: 4,
  center: [10, 1, 1],
  normal: [1, 0, 0],
  vertices: [],
  triangles: [],
};
const project = {
  schema_version: 1,
  geometry_engine: "occt",
  native_document: null,
  domains: {},
  engineering_brief: null,
  revision: 1,
  name: "Form reference",
  units: "mm",
  parameters: {},
  features: [],
  references: [],
  placement: { translation: [0, 0, 0], rotation: [0, 0, 0] },
  material: {
    name: "Sourced form reference",
    source: "Synthetic form fixture, no material calibration",
    youngs_modulus_mpa: 200000,
    poisson_ratio: 0.3,
    density_g_cm3: 7.8,
  },
  requirements: [],
  active_study_id: "default",
  named_studies: {},
  study: {
    schema_version: 1,
    type: "linear_static",
    name: "Engineering study",
    fixed_faces: [],
    forces: [],
    temperatures: [],
    heat_sources: [],
    reference_temperature_c: null,
    mode_count: 6,
    mesher: "netgen",
    max_nodes: 200000,
    solver_timeout_seconds: 180,
    mesh_size_mm: 4,
    refinement_steps: 2,
    convergence_tolerance: 0.05,
    stress_probe_mm: null,
    probe_point_mm: null,
    assumptions_confirmed: false,
    large_deformation: true,
    nonlinear_increments: 10,
    max_nonlinear_increments: 200,
    load_path: [],
  },
} as Project;
const selection = {
  id: face.id,
  kind: "face",
  point: face.center,
  face,
} as Selection;
function Fixture() {
  const [data, setData] = useState<JsonObject>({ schema_version: 1 });
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(1);
  const state = {
    studies: {},
    simulation_capabilities: { studies: [], scope: "UI form fixture; numerical execution is separate" },
    project,
    build: null,
    last_build: null,
    geometry_state: "not_run",
    analysis_state: "not_run",
    analysis: null,
    history: [],
    jobs: [],
    context: {},
    geometry_key: "reference-geometry",
    checks: null,
  } as State;
  return (
    <main style={{ width: 390, margin: "20px auto", padding: 20 }}>
      <SimulationPanel
        project={{ ...project, revision }}
        state={state}
        domainData={data}
        selection={selection}
        saveDomain={async (_domain, value) => {
          setData(value);
          setRevision((r) => r + 1);
        }}
        runDomain={async () => {
          throw new Error(
            "This form fixture cannot execute simulation workers.",
          );
        }}
        refresh={async () => {}}
        native={async () => {
          throw new Error("Not used by form fixture");
        }}
        inspectNative={async () => ({})}
        setError={setError}
      />
      <p role="alert">{error}</p>
      <details>
        <summary>Analytical reference numerical field</summary>
        <FieldPreview
          field={referenceField}
          quantity="displacement"
          scale={1}
        />
      </details>
      <output aria-label="Saved simulation setup">
        {JSON.stringify(data)}
      </output>
      <output aria-label="Saved revision">{revision}</output>
    </main>
  );
}
createRoot(document.getElementById("root")!).render(<Fixture />);
