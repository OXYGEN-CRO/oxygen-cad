import type {
  Project as GeneratedProject,
  Study as GeneratedStudy,
} from "./generated";
export type DisplayField =
  | "solid"
  | "stress"
  | "displacement"
  | "temperature"
  | "mode"
  | "plastic_strain";
export const projectPrefix =
  window.location.pathname.match(/^\/p\/[a-zA-Z0-9_-]+(?=\/|$)/)?.[0] ?? "";
export async function api(path: string, options: RequestInit = {}) {
  const response = await fetch(`${projectPrefix}/api/${path}`, options);
  if (!response.ok) {
    const text = await response.text();
    let detail = text;
    try {
      detail = JSON.parse(text).detail ?? text;
    } catch {}
    throw new Error(
      typeof detail === "string" ? detail : JSON.stringify(detail),
    );
  }
  return response.json();
}
export const post = (path: string, body: unknown) =>
  api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
export const artifact = (path: string) =>
  `${projectPrefix}/api/artifacts/${path.split("/").map(encodeURIComponent).join("/")}`;
export type Feature = {
  id: string;
  type: "profile" | "extrude" | "hole" | "fillet" | "chamfer" | "native";
  name: string;
  suppressed: boolean;
  [key: string]: any;
};
export type Study = Omit<
  GeneratedStudy,
  | "forces"
  | "stress_probe_mm"
  | "probe_point_mm"
  | "temperatures"
  | "heat_sources"
  | "convection"
  | "radiation"
> & {
  fixed_faces: string[];
  forces: { face: string; vector_n: number[] }[];
  mesh_size_mm: number;
  refinement_steps: number;
  convergence_tolerance: number;
  stress_probe_mm: number[] | null;
  probe_point_mm: number[] | null;
  temperatures: { face: string; temperature_c: number | null }[];
  heat_sources: { face: string; power_w: number | null }[];
  convection: {
    face: string;
    coefficient_w_m2_k: number | null;
    ambient_temperature_c: number | null;
    source: string;
  }[];
  radiation: {
    face: string;
    emissivity: number | null;
    ambient_temperature_c: number | null;
    source: string;
  }[];
  assumptions_confirmed: boolean;
};
export type Project = Omit<
  GeneratedProject,
  | "features"
  | "references"
  | "placement"
  | "material"
  | "study"
  | "named_studies"
> & {
  features: Feature[];
  references: any[];
  placement: { translation: number[]; rotation: number[] };
  material: any;
  requirements: any[];
  study: Study;
  named_studies: Record<string, Study>;
};
export type Face = {
  id: string;
  body_id?: string;
  signature: string;
  kind: string;
  area_mm2: number;
  center: number[];
  normal: number[] | null;
  vertices: number[][];
  triangles: number[][];
};
export type Geometry = {
  key: string;
  revision: number;
  faces: Face[];
  references: { id: string; name: string; faces: Face[]; visible: boolean }[];
  dimensions_mm: number[];
  bounds: number[][];
  volume_mm3: number | null;
  center_of_mass?: number[] | null;
  exact_solid_volume_mm3?: number;
  artifacts: Record<string, string>;
};
export type Selection = {
  id: string;
  kind: "face" | "reference";
  point: number[];
  face: Face;
  reference?: string;
};
export type State = {
  studies: Record<
    string,
    {
      id: string;
      name: string;
      type: string;
      key: string;
      state: string;
      result: any;
    }
  >;
  simulation_capabilities: {
    studies: { type: string; label: string; status: string }[];
    scope: string;
  };
  project: Project;
  build: Geometry | null;
  last_build: Geometry | null;
  geometry_state: string;
  analysis_state: string;
  analysis: any;
  history: any[];
  jobs: any[];
  context: any;
  geometry_key: string;
  checks: any;
};
