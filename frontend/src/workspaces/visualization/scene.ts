import type { Face } from "../../api";

export type CameraState = {
  target: number[];
  yaw: number;
  pitch: number;
  roll: number;
  distance: number;
  fov: number;
};
export type Mapping = {
  asset: string;
  projection: "xy" | "yz" | "zx" | "cylindrical_z";
  origin: number[];
  size_mm: number[];
  rotation: number;
  opacity: number;
  repeat: boolean;
};
export type Appearance = {
  color: string;
  metallic: number;
  roughness: number;
  opacity: number;
  texture: Mapping | null;
};
export type Light = {
  name: string;
  kind: "directional" | "point" | "spot";
  vector: number[];
  target: number[];
  intensity: number;
  color: string;
  cone_degrees: number;
  reference_distance_mm: number;
};
export type Scene = {
  name: string;
  camera: CameraState;
  width: number;
  height: number;
  background: string;
  transparent: boolean;
  exposure_ev: number;
  ambient: number;
  environment_rotation: number;
  lights: Light[];
  style: "shaded_edges" | "shaded" | "wireframe";
  appearances: Record<string, Appearance>;
  hidden: string[];
  isolated: string[];
  sections: {
    name: string;
    normal: number[];
    offset_mm: number;
    enabled: boolean;
  }[];
  transforms: Record<string, number[]>;
  renderer: "cpu-raster" | "native-cpu-raytrace";
};
export type Keyframe = {
  time: number;
  camera: CameraState | null;
  transforms: Record<string, number[]>;
  hidden: string[] | null;
  callout: string;
};
export type Storyboard = {
  name: string;
  scene: string;
  duration: number;
  fps: number;
  keyframes: Keyframe[];
};
export type Asset = {
  sha256: string;
  file: string;
  width: number;
  height: number;
};
export type Visualization = {
  schema_version: 1;
  active_scene: string;
  scenes: Scene[];
  storyboards: Storyboard[];
  assets: Record<string, Asset>;
};
export type NativeBody = {
  id: string;
  name: string;
  visible: boolean;
  mesh?: {
    positions?: number[][];
    triangles?: number[][];
    tri_face?: number[];
    faces?: Face[];
  };
};
export type NativeScene = { bodies: NativeBody[] };
export const cameraDefault = (): CameraState => ({
  target: [0, 0, 0],
  yaw: -Math.PI / 4,
  pitch: 0.61548,
  roll: 0,
  distance: 200,
  fov: 0,
});
export const appearanceDefault = (): Appearance => ({
  color: "#739bad",
  metallic: 0,
  roughness: 0.4,
  opacity: 1,
  texture: null,
});
export const sceneDefault = (name = "Product"): Scene => ({
  name,
  camera: cameraDefault(),
  width: 640,
  height: 480,
  background: "#eef1f4",
  transparent: false,
  exposure_ev: 0,
  ambient: 0.25,
  environment_rotation: 0,
  lights: [
    {
      name: "Key",
      kind: "directional",
      vector: [0.3, -0.4, 0.85],
      target: [0, 0, 0],
      color: "#ffffff",
      intensity: 0.85,
      cone_degrees: 40,
      reference_distance_mm: 100,
    },
  ],
  style: "shaded_edges",
  appearances: {},
  hidden: [],
  isolated: [],
  sections: [],
  transforms: {},
  renderer: "cpu-raster",
});
export const dataDefault = (): Visualization => ({
  schema_version: 1,
  active_scene: "Product",
  scenes: [sceneDefault()],
  storyboards: [],
  assets: {},
});
export const previewEvent = "o3d:visualization";
export const cameraEvent = "o3d:visualization-camera";

export function sampleScene(
  scene: Scene,
  board: Storyboard,
  time: number,
): { scene: Scene; callout: string } {
  const result = structuredClone(scene);
  const t = Math.min(Math.max(time, 0), board.duration);
  let callout = "";
  let left = board.keyframes[0];
  for (const key of board.keyframes) {
    if (key.time > t) break;
    left = key;
    if (key.camera) result.camera = structuredClone(key.camera);
    Object.assign(result.transforms, structuredClone(key.transforms));
    if (key.hidden) result.hidden = [...key.hidden];
    callout = key.callout;
  }
  const right = board.keyframes.find((key) => key.time > t);
  if (right) {
    const u = (t - left.time) / (right.time - left.time);
    if (right.camera) {
      for (const key of ["yaw", "pitch", "roll", "distance", "fov"] as const)
        result.camera[key] += u * (right.camera[key] - result.camera[key]);
      result.camera.target = result.camera.target.map(
        (v, i) => v + u * (right.camera!.target[i] - v),
      );
    }
    for (const [id, end] of Object.entries(right.transforms)) {
      const start = result.transforms[id] ?? [0, 0, 0];
      result.transforms[id] = start.map((v, i) => v + u * (end[i] - v));
    }
  }
  return { scene: result, callout };
}

export function nativeFaces(body: NativeBody): Face[] {
  if (body.mesh?.faces) return body.mesh.faces;
  const mesh = body.mesh;
  if (!mesh?.positions || !mesh.triangles) return [];
  const groups = new Map<number, number[][]>();
  mesh.triangles.forEach((tri, i) => {
    const id = mesh.tri_face?.[i] ?? -1;
    const group = groups.get(id) ?? [];
    group.push(tri);
    groups.set(id, group);
  });
  return Array.from(groups, ([id, triangles]) => {
    const indices = Array.from(new Set(triangles.flat()));
    const local = new Map(indices.map((n, i) => [n, i]));
    const vertices = indices.map((index) => mesh.positions![index]);
    let area = 0;
    const weighted = [0, 0, 0],
      normal = [0, 0, 0];
    for (const triangle of triangles) {
      const [a, b, c] = triangle.map((index) => mesh.positions![index]);
      const ab = b.map((v, i) => v - a[i]),
        ac = c.map((v, i) => v - a[i]);
      const cross = [
        ab[1] * ac[2] - ab[2] * ac[1],
        ab[2] * ac[0] - ab[0] * ac[2],
        ab[0] * ac[1] - ab[1] * ac[0],
      ];
      const size = Math.hypot(...cross) / 2;
      area += size;
      for (let i = 0; i < 3; i++) {
        weighted[i] += ((a[i] + b[i] + c[i]) / 3) * size;
        normal[i] += cross[i];
      }
    }
    const center = weighted.map((v) => v / Math.max(area, 1e-12));
    const norm = Math.hypot(...normal);
    return {
      id: `${body.id}:face:${id}`,
      signature: `${body.id}:face:${id}`,
      kind: "native",
      area_mm2: area,
      center,
      normal: norm > 1e-12 ? normal.map((v) => v / norm) : null,
      vertices,
      triangles: triangles.map((t) => t.map((i) => local.get(i)!)),
    };
  });
}
