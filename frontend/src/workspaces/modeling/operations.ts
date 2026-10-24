import type { JsonObject } from "../contracts";

export interface Field {
  key: string;
  label: string;
  kind?: "text" | "json" | "names" | "boolean";
  value?: string;
  options?: string[];
  optional?: boolean;
}
export interface Operation {
  command: string;
  label: string;
  group: "Create" | "Modify" | "Construct" | "Sketch";
  fields: Field[];
}
const expr = (
  key: string,
  label: string,
  value = "10 mm",
  optional = false,
): Field => ({ key, label, value, optional });
const names = (
  key: string,
  label: string,
  value = "",
  optional = false,
): Field => ({ key, label, value, kind: "names", optional });
const data = (
  key: string,
  label: string,
  value: string,
  optional = false,
): Field => ({ key, label, value, kind: "json", optional });
const choice = (key: string, label: string, options: string[]): Field => ({
  key,
  label,
  options,
  value: options[0],
});
const text = (
  key: string,
  label: string,
  value = "",
  optional = false,
): Field => ({ key, label, value, kind: "text", optional });
const operation = choice("operation", "Operation", [
  "new",
  "join",
  "cut",
  "intersect",
  "auto",
]);
const sketch = text("sketch", "Profile sketch", "", true);
const profiles = data(
  "profiles",
  "Profiles (stable curve loops or indices)",
  "",
  true,
);
const body = text("body", "Body", "", true);
const faces = data("faces", "Faces (native names or points)", "[]");
const edges = data("edges", "Edges (native names or points)", "[]");
const bodies = names("bodies", "Bodies");
const path = [
  text("path_sketch", "Path sketch"),
  names("path", "Path curves in order"),
];

export const operations: Operation[] = [
  {
    command: "sketch.text",
    label: "Sketch text",
    group: "Sketch",
    fields: [
      text("text", "Text", "OXYGEN"),
      data("at", "Baseline start", "[0,0]"),
      expr("height", "Capital height", "5"),
      expr("angle", "Text angle", "0"),
      choice("align", "Alignment", ["left", "center", "right"]),
    ],
  },
  {
    command: "solid.extrude",
    label: "Extrude",
    group: "Create",
    fields: [
      sketch,
      profiles,
      expr("distance", "Distance"),
      choice("direction", "Direction", ["positive", "negative", "symmetric"]),
      expr("distance2", "Second side", "", true),
      expr("taper", "Taper angle", "", true),
      operation,
    ],
  },
  {
    command: "solid.revolve",
    label: "Revolve",
    group: "Create",
    fields: [
      sketch,
      profiles,
      text("axis", "Axis or sketch line", "Y"),
      expr("angle", "Angle", "360 deg"),
      operation,
    ],
  },
  {
    command: "solid.sweep",
    label: "Sweep",
    group: "Create",
    fields: [
      sketch,
      profiles,
      ...path,
      choice("orientation", "Orientation", ["perpendicular", "parallel"]),
      expr("twist", "Twist", "0 deg"),
      operation,
    ],
  },
  {
    command: "solid.loft",
    label: "Loft",
    group: "Create",
    fields: [
      data(
        "sections",
        "Ordered sections",
        '[{"sketch":"Sketch1"},{"sketch":"Sketch2"}]',
      ),
      data("rails", "Guide rails", "", true),
      operation,
    ],
  },
  {
    command: "solid.box",
    label: "Box",
    group: "Create",
    fields: [
      expr("length", "Length", "40 mm"),
      expr("width", "Width", "30 mm"),
      expr("height", "Height", "10 mm"),
      data("corner", "Corner", "[0,0,0]"),
      operation,
    ],
  },
  {
    command: "solid.cylinder",
    label: "Cylinder",
    group: "Create",
    fields: [
      expr("diameter", "Diameter", "20 mm"),
      expr("height", "Height"),
      data("base", "Base center", "[0,0,0]"),
      choice("axis", "Axis", ["Z", "X", "Y"]),
      operation,
    ],
  },
  {
    command: "solid.sphere",
    label: "Sphere",
    group: "Create",
    fields: [
      expr("radius", "Radius"),
      data("center", "Center", "[0,0,0]"),
      operation,
    ],
  },
  {
    command: "solid.torus",
    label: "Torus",
    group: "Create",
    fields: [
      expr("major", "Major radius", "20 mm"),
      expr("minor", "Minor radius", "3 mm"),
      operation,
    ],
  },
  {
    command: "solid.hole",
    label: "Hole",
    group: "Create",
    fields: [
      data("position", "Position on face", "[0,0,10]"),
      expr("diameter", "Diameter", "5 mm"),
      expr("depth", "Depth (empty = through all)", "", true),
      choice("type", "Hole type", [
        "simple",
        "drilled",
        "counterbore",
        "countersink",
      ]),
      expr("cb_diameter", "Counterbore diameter", "", true),
      expr("cb_depth", "Counterbore depth", "", true),
      expr("cs_diameter", "Countersink diameter", "", true),
      text("thread", "Thread designation", "", true),
    ],
  },
  {
    command: "solid.thread",
    label: "Thread",
    group: "Create",
    fields: [
      data("face", "Cylindrical face point", "[10,0,5]"),
      text("designation", "Designation", "M8"),
      {
        key: "modeled",
        label: "Model actual thread geometry",
        kind: "boolean",
        value: "false",
      },
    ],
  },
  {
    command: "solid.pipe",
    label: "Pipe",
    group: "Create",
    fields: [
      ...path,
      expr("diameter", "Diameter", "4 mm"),
      expr("wall", "Wall thickness (empty = solid)", "", true),
      operation,
    ],
  },
  {
    command: "solid.pattern.rectangular",
    label: "Rectangular pattern",
    group: "Create",
    fields: [
      bodies,
      data("dir1", "Direction", "[1,0,0]"),
      expr("count1", "Quantity", "3"),
      expr("spacing1", "Spacing", "20 mm"),
    ],
  },
  {
    command: "solid.pattern.circular",
    label: "Circular pattern",
    group: "Create",
    fields: [
      bodies,
      text("axis", "Axis", "Z"),
      expr("count", "Quantity", "4"),
      expr("angle", "Total angle", "360 deg"),
    ],
  },
  {
    command: "solid.mirror",
    label: "Mirror bodies",
    group: "Create",
    fields: [bodies, text("plane", "Plane or construction plane", "YZ")],
  },
  {
    command: "solid.fillet",
    label: "Fillet",
    group: "Modify",
    fields: [
      edges,
      expr("radius", "Radius", "1 mm"),
      choice("type", "Continuity / radius", [
        "constant",
        "curvature",
        "variable",
        "chord",
      ]),
      expr("radius2", "End radius", "", true),
      choice("fit", "Fit behavior", ["exact", "reduce", "taper"]),
      body,
    ],
  },
  {
    command: "solid.chamfer",
    label: "Chamfer",
    group: "Modify",
    fields: [
      edges,
      expr("distance", "Distance", "1 mm"),
      expr("distance2", "Second distance", "", true),
      body,
    ],
  },
  {
    command: "solid.shell",
    label: "Shell",
    group: "Modify",
    fields: [
      faces,
      expr("thickness", "Thickness", "1 mm"),
      choice("direction", "Direction", ["inside", "outside", "both"]),
      body,
    ],
  },
  {
    command: "solid.offset_face",
    label: "Offset faces",
    group: "Modify",
    fields: [faces, expr("distance", "Distance", "1 mm"), body],
  },
  {
    command: "solid.press_pull",
    label: "Press / pull",
    group: "Modify",
    fields: [
      data("face", "Face point", "[0,0,10]"),
      expr("distance", "Distance", "2 mm"),
    ],
  },
  {
    command: "face.delete",
    label: "Delete / heal faces",
    group: "Modify",
    fields: [
      faces,
      {
        key: "heal",
        label: "Heal neighboring faces",
        kind: "boolean",
        value: "true",
      },
      body,
    ],
  },
  {
    command: "solid.draft",
    label: "Draft",
    group: "Modify",
    fields: [
      faces,
      expr("angle", "Draft angle", "3 deg"),
      text("neutral", "Neutral plane", "XY"),
      body,
    ],
  },
  {
    command: "solid.move",
    label: "Move / copy bodies",
    group: "Modify",
    fields: [
      bodies,
      data("translate", "Translation", "[10,0,0]"),
      { key: "copy", label: "Create a copy", kind: "boolean", value: "false" },
    ],
  },
  {
    command: "solid.scale",
    label: "Scale bodies",
    group: "Modify",
    fields: [bodies, expr("factor", "Scale factor", "2")],
  },
  {
    command: "solid.combine",
    label: "Combine bodies",
    group: "Modify",
    fields: [
      text("target", "Target body"),
      names("tools", "Tool bodies"),
      choice("operation", "Operation", ["join", "cut", "intersect"]),
      {
        key: "keep_tools",
        label: "Keep tool bodies",
        kind: "boolean",
        value: "false",
      },
    ],
  },
  {
    command: "solid.split_body",
    label: "Split body",
    group: "Modify",
    fields: [text("body", "Body"), text("plane", "Split plane", "XY")],
  },
  {
    command: "construct.plane.offset",
    label: "Offset plane",
    group: "Construct",
    fields: [
      text("base", "Base plane", "XY"),
      expr("offset", "Offset", "10 mm"),
      text("name", "Name", "Offset plane"),
    ],
  },
  {
    command: "construct.plane.midplane",
    label: "Midplane",
    group: "Construct",
    fields: [
      data("a", "First plane / face", '{"plane":"XY"}'),
      data(
        "b",
        "Second plane / face",
        '{"plane":{"origin":[0,0,20],"normal":[0,0,1]}}',
      ),
    ],
  },
  {
    command: "construct.plane.at_angle",
    label: "Plane at angle",
    group: "Construct",
    fields: [
      text("base", "Base plane", "XY"),
      text("axis", "Axis", "X"),
      expr("angle", "Angle", "45 deg"),
    ],
  },
  {
    command: "construct.axis.through_cylinder",
    label: "Axis through cylinder",
    group: "Construct",
    fields: [data("face", "Cylindrical face reference", '{"face":[10,0,5]}')],
  },
  {
    command: "construct.axis.two_points",
    label: "Axis through points",
    group: "Construct",
    fields: [
      data("a", "First point reference", '{"point":[0,0,0]}'),
      data("b", "Second point reference", '{"point":[10,10,10]}'),
    ],
  },
  {
    command: "sketch.offset",
    label: "Offset curves",
    group: "Sketch",
    fields: [
      names("curves", "Curves"),
      expr("distance", "Distance", "2 mm"),
      choice("direction", "Direction", ["one_side", "symmetric"]),
    ],
  },
  {
    command: "sketch.fillet",
    label: "Sketch fillet",
    group: "Sketch",
    fields: [
      text("a", "First curve"),
      text("b", "Second curve"),
      expr("radius", "Radius", "2 mm"),
    ],
  },
  {
    command: "sketch.pattern.rectangular",
    label: "Pattern sketch entities",
    group: "Sketch",
    fields: [
      names("entities", "Entities"),
      expr("count", "Quantity", "3"),
      expr("spacing", "Spacing", "10 mm"),
    ],
  },
  {
    command: "sketch.pattern.circular",
    label: "Circular sketch pattern",
    group: "Sketch",
    fields: [
      names("entities", "Entities"),
      data("center", "Center", "[0,0]"),
      expr("count", "Quantity", "4"),
      expr("angle", "Angle", "360 deg"),
    ],
  },
  {
    command: "sketch.mirror",
    label: "Mirror sketch entities",
    group: "Sketch",
    fields: [names("entities", "Entities"), text("line", "Mirror line")],
  },
  {
    command: "sketch.move",
    label: "Move / copy entities",
    group: "Sketch",
    fields: [
      names("entities", "Entities"),
      data("translate", "Translation", "[10,0]"),
      { key: "copy", label: "Create a copy", kind: "boolean", value: "false" },
    ],
  },
  {
    command: "sketch.line3d",
    label: "3D polyline",
    group: "Sketch",
    fields: [data("points", "3D points", "[[0,0,0],[20,0,0],[20,20,20]]")],
  },
  {
    command: "sketch.spline3d",
    label: "3D spline",
    group: "Sketch",
    fields: [data("points", "3D fit points", "[[0,0,0],[10,10,10],[20,0,20]]")],
  },
];

export function operationParams(
  op: Operation,
  values: Record<string, string>,
): JsonObject {
  const params: JsonObject = {};
  for (const field of op.fields) {
    const value = (values[field.key] ?? "").trim();
    if (!value && field.optional) continue;
    if (!value && field.kind !== "boolean")
      throw new Error(`${field.label} is required`);
    if (field.kind === "json") params[field.key] = JSON.parse(value);
    else if (field.kind === "names")
      params[field.key] = value
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
    else if (field.kind === "boolean") params[field.key] = value === "true";
    else if (!field.kind && !field.options && Number.isFinite(Number(value)))
      params[field.key] = Number(value);
    else params[field.key] = value;
  }
  return params;
}
