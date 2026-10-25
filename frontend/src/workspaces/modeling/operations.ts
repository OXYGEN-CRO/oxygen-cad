import type { JsonObject } from "../contracts";

export interface Field {
  key: string;
  label: string;
  kind?: "text" | "json" | "names" | "boolean" | "vector";
  value?: string;
  options?: string[];
  optional?: boolean;
}
export interface Operation {
  command: string;
  label: string;
  group: "Create" | "Modify" | "Construct" | "Sketch" | "Plastic";
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
const vector = (key: string, label: string): Field => ({
  key,
  label,
  value: "[0,0,0]",
  kind: "vector",
});
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
    command: "solid.rib",
    label: "Rib",
    group: "Create",
    fields: [
      sketch,
      names("curves", "Open sketch curves in order"),
      expr("thickness", "Thickness", "2 mm"),
      expr("depth", "Depth (empty = next face)", "", true),
      choice("direction", "Direction", ["in_plane", "normal"]),
    ],
  },
  {
    command: "solid.web",
    label: "Web",
    group: "Create",
    fields: [
      sketch,
      names("curves", "Open sketch curves"),
      expr("thickness", "Thickness", "2 mm"),
      expr("depth", "Depth (empty = next face)", "", true),
      choice("direction", "Direction", ["in_plane", "normal"]),
    ],
  },
  {
    command: "solid.coil",
    label: "Coil",
    group: "Create",
    fields: [
      expr("diameter", "Coil diameter", "20 mm"),
      expr("revolutions", "Revolutions", "3"),
      expr("pitch", "Pitch", "5 mm"),
      expr("section_size", "Section size", "2 mm"),
      choice("section", "Section", ["circular", "square"]),
      choice("section_position", "Section position", [
        "center",
        "inside",
        "outside",
      ]),
      data("base", "Base", "[0,0,0]"),
      operation,
    ],
  },
  {
    command: "solid.emboss",
    label: "Emboss / deboss",
    group: "Create",
    fields: [
      sketch,
      profiles,
      expr("depth", "Depth", "1 mm"),
      choice("mode", "Mode", ["emboss", "deboss"]),
      data("face", "Target face point", "", true),
    ],
  },
  {
    command: "solid.pattern.path",
    label: "Pattern along path",
    group: "Create",
    fields: [
      bodies,
      ...path,
      expr("count", "Quantity", "3"),
      expr("spacing", "Spacing", "10 mm"),
      choice("orientation", "Orientation", ["identical", "path"]),
    ],
  },
  {
    command: "solid.boundary_fill",
    label: "Boundary fill",
    group: "Create",
    fields: [
      names("tools", "Tool bodies"),
      data("cells", "Point inside each selected cell", "[[0,0,0]]"),
      operation,
      {
        key: "remove_tools",
        label: "Remove tool bodies",
        kind: "boolean",
        value: "false",
      },
    ],
  },
  {
    command: "solid.replace_face",
    label: "Replace faces",
    group: "Modify",
    fields: [faces, text("target", "Target plane", "XY"), body],
  },
  {
    command: "solid.align",
    label: "Align bodies",
    group: "Modify",
    fields: [
      bodies,
      data("from", "Source point or snap reference", "[0,0,0]"),
      data("to", "Target point or snap reference", "[10,0,0]"),
    ],
  },
  {
    command: "solid.remove",
    label: "Remove bodies",
    group: "Modify",
    fields: [bodies],
  },
  {
    command: "plastic.boss",
    label: "Plastic boss",
    group: "Plastic",
    fields: [
      data("position", "Position on face", "[20,15,2]"),
      expr("diameter", "Boss diameter", "8 mm"),
      expr("height", "Height", "8 mm"),
      expr("hole_diameter", "Hole diameter", "3 mm", true),
      expr("hole_depth", "Hole depth", "", true),
      expr("draft", "Draft angle", "0 deg", true),
      expr("fillet", "Root fillet radius", "", true),
      expr("ribs", "Rib quantity", "", true),
      expr("rib_thickness", "Rib thickness", "", true),
      expr("rib_length", "Rib length", "", true),
      body,
    ],
  },
  {
    command: "plastic.snap_fit",
    label: "Plastic snap fit",
    group: "Plastic",
    fields: [
      data("position", "Position on face", "[20,15,2]"),
      data("hook", "Catch direction", "[1,0,0]"),
      expr("length", "Cantilever length", "12 mm"),
      expr("thickness", "Thickness", "1 mm"),
      expr("width", "Width", "4 mm"),
      expr("catch_depth", "Catch depth", "1 mm"),
      expr("catch_length", "Catch length", "2 mm"),
      body,
    ],
  },
  {
    command: "plastic.lip",
    label: "Plastic lip / groove",
    group: "Plastic",
    fields: [
      data("face", "Rim face point", "[0,0,2]"),
      expr("width", "Lip width", "1 mm"),
      expr("height", "Lip height", "2 mm"),
      choice("type", "Type", ["lip", "groove"]),
      choice("side", "Rim side", ["inside", "outside"]),
      expr("gap", "Groove clearance", "", true),
      body,
    ],
  },
  {
    command: "plastic.rest",
    label: "Plastic rest",
    group: "Plastic",
    fields: [
      data("position", "Position on face", "[30,15,2]"),
      expr("width", "Width / round diameter", "6 mm"),
      expr("length", "Rectangle length (empty = round)", "", true),
      expr("height", "Height", "3 mm"),
      expr("draft", "Draft angle", "0 deg", true),
      expr("thickness", "Hollow wall thickness", "", true),
      body,
    ],
  },
  {
    command: "plastic.manage_rules",
    label: "Edit plastic rule",
    group: "Plastic",
    fields: [
      text("name", "Rule name", "Oxygen enclosure"),
      text("from", "Base rule", "ABS (1.5mm)"),
      expr("thickness", "Nominal thickness", "1.5 mm", true),
      expr("nominal_radius", "Nominal radius", "", true),
      expr("clearance", "Clearance", "0.2 mm", true),
      expr("draft", "Draft angle", "1 deg", true),
      {
        key: "active",
        label: "Set as active rule",
        kind: "boolean",
        value: "true",
      },
    ],
  },
  {
    command: "plastic.assign_rule",
    label: "Assign plastic rule",
    group: "Plastic",
    fields: [bodies, text("rule", "Rule name", "ABS (1.5mm)")],
  },
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
      expr("start_offset", "Start offset", "", true),
      data("to", "To-object point", "", true),
      data("from", "From-object point", "", true),
      {
        key: "thin",
        label: "Thin extrusion along sketch curves",
        kind: "boolean",
        value: "false",
      },
      expr("thickness", "Thin wall thickness", "", true),
      choice("side", "Thin wall side", [
        "center",
        "inside",
        "outside",
        "two_sides",
      ]),
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
      vector("center", "Center"),
      operation,
      text("name", "Feature name", "", true),
      text("body_name", "Body name", "", true),
    ],
  },
  {
    command: "solid.cylinder",
    label: "Cylinder",
    group: "Create",
    fields: [
      expr("diameter", "Diameter", "20 mm"),
      expr("height", "Height"),
      vector("base", "Base"),
      choice("axis", "Axis", ["Z", "X", "Y"]),
      operation,
      text("name", "Feature name", "", true),
      text("body_name", "Body name", "", true),
    ],
  },
  {
    command: "solid.sphere",
    label: "Sphere",
    group: "Create",
    fields: [
      expr("radius", "Radius"),
      vector("center", "Center"),
      operation,
      text("name", "Feature name", "", true),
      text("body_name", "Body name", "", true),
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

// Explicit participant bodies are shared by native constructive solid operations.
// Keep their expressions and target list in the same typed operation submission.
for (const op of operations) {
  if (
    op.command.startsWith("solid.") &&
    op.fields.some((field) => field.key === "operation") &&
    op.command !== "solid.combine"
  ) {
    op.fields.push(names("targets", "Participant bodies", "", true));
  }
}

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
    if (field.kind === "vector") {
      const coordinates = JSON.parse(value) as unknown;
      if (!Array.isArray(coordinates) || coordinates.length !== 3)
        throw new Error(`${field.label} needs X, Y and Z`);
      params[field.key] = coordinates.map((coordinate, index) => {
        const expression = String(coordinate).trim();
        if (!expression)
          throw new Error(
            `${field.label} ${["X", "Y", "Z"][index]} is required`,
          );
        return Number.isFinite(Number(expression))
          ? Number(expression)
          : expression;
      });
    } else if (field.kind === "json") params[field.key] = JSON.parse(value);
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
