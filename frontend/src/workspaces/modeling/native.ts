import type { JsonObject } from "../contracts";

export type Point2 = [number, number];
export interface SketchCurve extends JsonObject {
  id: string;
  type: string;
  construction?: boolean;
  fixed?: boolean;
  fully_constrained?: boolean;
  link?: string;
}
export interface SketchPoint {
  id: string;
  at: unknown;
  fixed?: boolean;
  fully_constrained?: boolean;
  link?: string;
}
export interface SketchConstraint extends JsonObject {
  id: string;
  name: string;
  param?: string;
  expression?: string;
}
export interface SketchProfile {
  index: number;
  area_mm2: number;
  centroid_sketch: unknown;
  curves: string[];
  holes: string[][];
}
export interface SketchLink extends JsonObject {
  id: string;
  lost?: boolean;
  source: JsonObject;
}
export interface NativeSketch {
  id: number;
  name: string;
  dof: number;
  status: string;
  fully_constrained: boolean;
  failing: string[];
  points: SketchPoint[];
  curves: SketchCurve[];
  constraints: SketchConstraint[];
  profiles: SketchProfile[];
  links: SketchLink[];
  plane: JsonObject;
}
export interface NativeCanvas {
  id: number;
  name: string;
  format: string;
  data: string;
  pixels: [number, number];
  center: unknown;
  width: number;
  angle: number;
  opacity: number;
  flip?: boolean;
  visible: boolean;
  component?: number;
  plane: JsonObject;
}
export interface NativeFeature {
  id: number;
  name: string;
  type: string;
  suppressed: boolean;
  rolled_back: boolean;
  error?: string;
  warning?: string;
  ms?: number;
}
export interface NativeParameter {
  name: string;
  expression: string;
  value?: number;
  unit: string;
  model: boolean;
  error?: string;
}
export interface NativeCommand {
  id: string;
  label: string;
  enabled: boolean;
  host_allowed?: boolean;
  disabled_reason?: string;
  planned?: string;
  params: string;
}
export interface NativeInspection extends JsonObject {
  active_sketch?: number | null;
  marker: number;
  timeline: NativeFeature[];
  params: NativeParameter[];
  bodies: { name: string; feature?: number }[];
}
export interface NativeSnapshot extends JsonObject {
  inspection: NativeInspection;
  sketches: NativeSketch[];
  commands: NativeCommand[];
}
export type Execute = (
  command: string,
  params?: JsonObject,
) => Promise<unknown>;

export function point(value: unknown): Point2 {
  if (Array.isArray(value)) return [Number(value[0]), Number(value[1])];
  const v = (value ?? {}) as { x?: number; y?: number };
  return [v.x ?? 0, v.y ?? 0];
}
export function distance(a: Point2, b: Point2) {
  return Math.hypot(a[0] - b[0], a[1] - b[1]);
}
export function json(value: unknown) {
  return JSON.stringify(value, null, 2);
}
export function errorText(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}
export function allowed(snapshot: NativeSnapshot | null, command: string) {
  const c = snapshot?.commands.find((candidate) => candidate.id === command);
  return !!c && c.enabled && c.host_allowed !== false && !c.planned;
}
