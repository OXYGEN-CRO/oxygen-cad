import type { JsonObject } from "../contracts";

export type Operation = { command: string; label: string; note: string; params: JsonObject; read?: boolean };
export const operations: Operation[] = [
  { command: "joint.origin", label: "Persistent origin", note: "Use a named face/circle, edge pick or explicit point. Inspect attachment references after geometry edits.", params: { name: "Shoulder", occurrence: 0, face: "F1:top" } },
  { command: "joint.create", label: "Geometric relation", note: "Relations: parallel, perpendicular, angle, point_on_plane, point_on_line, line_on_plane. Several relations compose; inspect DOFs and conflicts.", params: { name: "Alignment", type: "parallel", a: "Shoulder", b: "Bearing", flip: true } },
  { command: "joint.rigid_group", label: "Rigid group", note: "Preserves the current placement of selected occurrences.", params: { occurrences: [] } },
  { command: "joint.motion_link", label: "Motion link", note: "b = ratio × a + offset. Units are mm or degrees; choose value indices for multi-coordinate joints.", params: { a: 0, b: 0, ratio: -2, offset: 0, ia: 0, ib: 0 } },
  { command: "joint.gear", label: "Gear pair", note: "Pitch radii or tooth counts define the signed ratio. Radius inputs may reference named circular geometry.", params: { a: 0, b: 0, ra: 20, rb: 10, internal: false } },
  { command: "joint.rack_pinion", label: "Rack and pinion", note: "One revolute and one slider. Travel = pitch radius × angle in radians.", params: { a: 0, b: 0, radius: 10 } },
  { command: "joint.screw", label: "Lead screw", note: "Cylindrical joint, or revolute/slider pair. Pitch is mm per full turn.", params: { joint: 0, pitch: 2 } },
  { command: "joint.cam", label: "Cam follower", note: "Requires an actual closed sketch profile. Knife, roller and flat followers are solved by the native mechanism engine.", params: { a: 0, b: 0, profile: { sketch: "Cam profile" }, follower: "knife" } },
  { command: "joint.reach", label: "Reach target", note: "Inverse kinematics follows grounded joint chains and limits. This operation does not check contact sets.", params: { occurrence: 0, point: [20, 0, 0] } },
  { command: "joint.sweep", label: "Swept joint collision", note: "Sweep travel against selected occurrences. Read stopped/incomplete states; sampled motion interference has different coverage.", params: { joint: 0, from: 0, to: 90, samples: 21 } },
  { command: "motion.trace.keep", label: "Keep associative trace", note: "Creates a native 3D spline that follows the selected point, mechanism and driving inputs.", params: { name: "Tool path", occurrence: 0, point: [0, 0, 0], joint: 0, from: 0, to: 90, samples: 21, auto_update: true } },
  { command: "motion.trace.edit", label: "Edit trace", note: "Change source inputs or pause automatic trace updates.", params: { sketch: "Tool path", auto_update: true } },
  { command: "component.insert_design", label: "Insert linked design", note: "Select an immutable imported native-design asset ID. Reuse inserts another instance of the linked definition.", params: { asset_id: "", name: "Linked component", reuse: true } },
  { command: "component.relink", label: "Update linked source", note: "Apply a reviewed imported source revision. Native relink preserves occurrences and reports missing joint interfaces.", params: { component: 0, asset_id: "" } },
  { command: "component.break_link", label: "Break link", note: "Keeps embedded geometry and removes its external source relationship.", params: { component: 0 } },
  { command: "config.configure", label: "Configure design", note: "Add native parameter/feature columns. A Default row captures existing values.", params: { params: [], features: [] } },
  { command: "config.row", label: "Configuration row", note: "Column keys are param:<name>, suppress:<feature>, or material:<body>. Preview before activating.", params: { name: "Variant", values: {} } },
  { command: "config.preview", label: "Preview configuration", note: "Rebuild a candidate configuration without changing the current design.", params: { row: "Variant" }, read: true },
  { command: "explode.create", label: "Manual exploded view", note: "Display offsets are separate from engineering placements and joints.", params: { name: "Assembly sequence", moves: [{ occurrence: 0, translate: [0, 0, 20] }] } },
];
