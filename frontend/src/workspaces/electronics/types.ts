export type Point = [number, number];
export type PinRef = { component_id: string; pin_id: string };
export type Pin = {
  id: string;
  number: string;
  name: string;
  electrical: string;
  at: Point;
};
export type Pad = {
  id: string;
  pin_id: string;
  number: string;
  at: Point;
  size: Point;
  shape: string;
  kind: string;
  rotation: number;
  drill: number;
  slot: Point | null;
  layers: string[];
  mask_margin: number;
  paste_margin: number;
};
export type Part = {
  id: string;
  version: string;
  name: string;
  manufacturer: string;
  mpn: string;
  source: string;
  licence: string;
  pins: Pin[];
  pads: Pad[];
  body_size: Point;
  model3d: object | null;
  spice: { kind: string } | null;
};
export type Component = {
  id: string;
  sheet_id: string;
  library_id: string;
  library_version: string;
  reference: string;
  value: string;
  at: Point;
  rotation: number;
  locked: boolean;
};
export type Net = {
  id: string;
  sheet_id: string;
  name: string;
  global_name: boolean;
  members: PinRef[];
};
export type Placement = {
  id: string;
  component_id: string;
  at: Point;
  side: "top" | "bottom";
  rotation: number;
  locked: boolean;
};
export type Track = {
  id: string;
  net_id: string;
  layer: string;
  points: Point[];
  width: number;
  locked: boolean;
};
export type Wire = {
  id: string;
  sheet_id: string;
  net_id: string;
  points: Point[];
  start: PinRef | null;
  end: PinRef | null;
  junction_ids: string[];
};
export type Via = {
  id: string;
  net_id: string;
  at: Point;
  diameter: number;
  drill: number;
  start_layer: string;
  end_layer: string;
  technology: string;
  locked: boolean;
};
export type Data = {
  schema_version: 1;
  units: "mm";
  name: string;
  library: Part[];
  sheets: {
    id: string;
    name: string;
    parent_id: string | null;
    ports: object[];
  }[];
  components: Component[];
  nets: Net[];
  wires: Wire[];
  buses: object[];
  no_connect: PinRef[];
  board: {
    id: string;
    outline: Point[];
    cutouts: Point[][];
    holes: {
      id: string;
      at: Point;
      diameter: number;
      slot: Point | null;
      plated: boolean;
    }[];
    stackup: {
      id: string;
      kind: string;
      thickness: number;
      material: string;
    }[];
    placements: Placement[];
    tracks: Track[];
    vias: Via[];
    zones: { id: string; polygon: Point[]; layer: string; kind: string }[];
    origin: Point;
    transform: number[];
    outline_owner: string;
    mechanical_occurrence_id: string | null;
  };
  process: {
    name: string;
    source: string;
    min_width: number;
    clearance: number;
    min_drill: number;
    annular_ring: number;
    edge_clearance: number;
    mask_web: number;
  };
  rules: {
    id: string;
    priority: number;
    net_ids: string[];
    layers: string[];
    width: number | null;
    clearance: number | null;
  }[];
  variants: {
    id: string;
    name: string;
    dnp: string[];
    values: Record<string, string>;
  }[];
  differential_pairs: object[];
  waivers: object[];
  mates: object[];
  harness: object[];
  provenance: object[];
};
export type Finding = {
  id: string;
  code: string;
  objects: string[];
  message: string;
  severity: string;
  waived: boolean;
};
export type Result = {
  revision?: number;
  state?: string;
  findings?: Finding[];
  artifacts?: { name: string; path: string }[];
  columns?: string[];
  rows?: number[][];
  candidate?: Data;
  loss_report?: { findings: { message: string; severity: string }[] };
  [key: string]: unknown;
};
export const id = (prefix: string) =>
  `${prefix}_${crypto.randomUUID().replaceAll("-", "").slice(0, 12)}`;
export const refkey = (p: PinRef) => `${p.component_id}/${p.pin_id}`;
export const rotate = (p: Point, angle: number): Point => {
  const a = (angle * Math.PI) / 180;
  return [
    p[0] * Math.cos(a) - p[1] * Math.sin(a),
    p[0] * Math.sin(a) + p[1] * Math.cos(a),
  ];
};
export function position(
  p: Point,
  c: Component | Placement,
  board: boolean,
): Point {
  const r = rotate(
    [p[0] * (board && "side" in c && c.side === "bottom" ? -1 : 1), p[1]],
    c.rotation,
  );
  return [r[0] + c.at[0], r[1] + c.at[1]];
}
