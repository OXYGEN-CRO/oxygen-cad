import { useEffect, useMemo, useRef, useState, type PointerEvent } from "react";
import { Button, Input, SelectControl } from "../../components/ui";
import type { JsonObject } from "../contracts";
import {
  distance,
  point,
  type Execute,
  type NativeSketch,
  type NativeCanvas,
  type Point2,
  type SketchCurve,
} from "./native";

type Tool =
  | "select"
  | "line"
  | "rectangle"
  | "rounded"
  | "circle"
  | "arc"
  | "point"
  | "spline"
  | "trim"
  | "trim_path"
  | "extend"
  | "break";
const toolNames: [Tool, string][] = [
  ["select", "Select / drag"],
  ["line", "Line"],
  ["rectangle", "Rectangle"],
  ["rounded", "Rounded rectangle"],
  ["circle", "Circle"],
  ["arc", "3-point arc"],
  ["point", "Point"],
  ["spline", "Fit spline"],
  ["trim", "Trim"],
  ["trim_path", "Trim path preview"],
  ["extend", "Extend"],
  ["break", "Break"],
];
const safe = (promise: Promise<unknown>) => {
  void promise.catch(() => {});
};
const at = (p: Point2) => `${p[0]},${-p[1]}`;

export function curvePath(curve: SketchCurve): string | null {
  const a = point(curve.start_at),
    b = point(curve.end_at),
    c = point(curve.center_at);
  if (curve.type === "line") return `M${at(a)} L${at(b)}`;
  if (curve.type === "arc") {
    const radius = Number(curve.radius);
    const angle =
      (Math.atan2(b[1] - c[1], b[0] - c[0]) -
        Math.atan2(a[1] - c[1], a[0] - c[0]) +
        Math.PI * 2) %
      (Math.PI * 2);
    return `M${at(a)} A${radius},${radius} 0 ${angle > Math.PI ? 1 : 0},0 ${at(b)}`;
  }
  // The inspector returns spline fit/control points, not evaluated samples. Show their
  // editable polygon explicitly, instead of claiming it is the evaluated curve.
  if (curve.type === "spline")
    return (curve.points_at as unknown[])
      .map((p, i) => `${i ? "L" : "M"}${at(point(p))}`)
      .join(" ");
  return null;
}

export default function SketchEditor({
  sketch,
  active,
  busy,
  execute,
  onProfiles,
  canvases = [],
  nativeSelection = [],
}: {
  sketch: NativeSketch;
  active: boolean;
  busy: boolean;
  execute: Execute;
  onProfiles: (indices: number[]) => void;
  canvases?: NativeCanvas[];
  nativeSelection?: JsonObject[];
}) {
  const svg = useRef<SVGSVGElement>(null);
  const [tool, setTool] = useState<Tool>("select");
  const [selected, setSelected] = useState<string[]>([]);
  const [profileIds, setProfileIds] = useState<number[]>([]);
  const [clicks, setClicks] = useState<Point2[]>([]);
  const [hover, setHover] = useState<Point2 | null>(null);
  const [drag, setDrag] = useState<{
    id: string;
    from: Point2;
    to: Point2;
  } | null>(null);
  const [snap, setSnap] = useState(true);
  const [zoom, setZoom] = useState(1);
  const [fitRevision, setFitRevision] = useState(0);
  const [radius, setRadius] = useState("2 mm");
  const [dimensionType, setDimensionType] = useState("auto");
  const [dimension, setDimension] = useState("10 mm");
  const [referenceDimension, setReferenceDimension] = useState(false);
  const [constraint, setConstraint] = useState("horizontal_vertical");
  const [pointX, setPointX] = useState("0");
  const [pointY, setPointY] = useState("0");
  const [trimPath, setTrimPath] = useState<Point2[] | null>(null);
  const [trimPreview, setTrimPreview] = useState<{
    points: Point2[];
    removed: Point2[][];
  } | null>(null);
  const [glyphs, setGlyphs] = useState<
    {
      id: string;
      name: string;
      dimension: boolean;
      value?: number;
      at: unknown;
      failing?: boolean;
      param?: string;
    }[]
  >([]);
  useEffect(() => {
    const entities = nativeSelection
      .filter((s) => ["sketch_curve", "sketch_point"].includes(String(s.type)))
      .map((s) => String(s.id));
    const profiles = nativeSelection
      .filter((s) => s.type === "profile" && s.sketch === sketch.id)
      .map((s) => Number(s.index));
    setSelected(entities);
    setProfileIds(profiles);
    onProfiles(profiles);
  }, [sketch.id, JSON.stringify(nativeSelection)]);
  const bounds = useMemo(() => {
    const pts = sketch.points.map((p) => point(p.at));
    canvases
      .filter((canvas) => canvas.visible && !canvas.component)
      .forEach((canvas) => {
        const center = point(canvas.center);
        const halfWidth = canvas.width / 2;
        const halfHeight = (halfWidth * canvas.pixels[1]) / canvas.pixels[0];
        pts.push(
          [center[0] - halfWidth, center[1] - halfHeight],
          [center[0] + halfWidth, center[1] + halfHeight],
        );
      });
    sketch.curves
      .filter((c) => c.type === "circle")
      .forEach((c) => {
        const center = point(c.center_at);
        const r = Number(c.radius);
        pts.push(
          [center[0] - r, center[1] - r],
          [center[0] + r, center[1] + r],
        );
      });
    const xs = pts.map((p) => p[0]),
      ys = pts.map((p) => p[1]);
    const minX = Math.min(-10, ...xs),
      maxX = Math.max(10, ...xs),
      minY = Math.min(-10, ...ys),
      maxY = Math.max(10, ...ys);
    const size = Math.max(maxX - minX, maxY - minY, 40) * 1.35;
    return { x: (minX + maxX) / 2, y: (minY + maxY) / 2, size };
  }, [sketch.id, fitRevision]); // Keep the camera stable while dragging; Fit updates it explicitly.
  const size = bounds.size / zoom;
  const stroke = size / 200;
  const screenPoint = (event: PointerEvent<SVGSVGElement>): Point2 => {
    const root = svg.current!;
    const p = root.createSVGPoint();
    p.x = event.clientX;
    p.y = event.clientY;
    const local = p.matrixTransform(root.getScreenCTM()!.inverse());
    const grid = snap ? 1 : 0.01;
    return [
      Math.round(local.x / grid) * grid,
      -Math.round(local.y / grid) * grid,
    ];
  };
  const select = (id: string, add: boolean) => {
    const next = add
      ? selected.includes(id)
        ? selected.filter((s) => s !== id)
        : [...selected, id]
      : [id];
    setSelected(next);
    const p = sketch.points.find((p) => p.id === id);
    if (p) {
      const [x, y] = point(p.at);
      setPointX(String(x));
      setPointY(String(y));
    }
    safe(
      execute("select.set", {
        items: next.map((entity) => ({
          type: sketch.points.some((p) => p.id === entity)
            ? "sketch_point"
            : "sketch_curve",
          id: entity,
        })),
      }),
    );
  };
  const selectProfiles = (indices: number[]) => {
    setProfileIds(indices);
    onProfiles(indices);
    safe(
      execute("select.set", {
        items: indices.map((index) => ({
          type: "profile",
          sketch: sketch.id,
          index,
        })),
      }),
    );
  };
  const draw = async (points: Point2[]) => {
    const [a, b, c] = points;
    let command: string, params: JsonObject;
    if (tool === "point") {
      command = "sketch.point";
      params = { point: a };
    } else if (tool === "line") {
      command = "sketch.line";
      params = { points, infer: true };
    } else if (tool === "rectangle" || tool === "rounded") {
      command =
        tool === "rounded"
          ? "sketch.rectangle.rounded"
          : "sketch.rectangle.two_point";
      params = { p0: a, p1: b, ...(tool === "rounded" ? { radius } : {}) };
    } else if (tool === "circle") {
      command = "sketch.circle.center";
      params = { center: a, radius: distance(a, b) };
    } else if (tool === "arc") {
      command = "sketch.arc.three_point";
      params = { start: a, end: b, through: c };
    } else {
      command = "sketch.spline.fit_point";
      params = { points };
    }
    await execute(command, params);
    setClicks([]);
  };
  const chooseTool = (next: Tool) => {
    setTool(next);
    setClicks([]);
    setDrag(null);
    setTrimPath(null);
    setTrimPreview(null);
  };
  const down = (event: PointerEvent<SVGSVGElement>) => {
    if (!active || busy || event.button !== 0) return;
    const p = screenPoint(event);
    if (tool === "trim_path") {
      setTrimPreview(null);
      setTrimPath([p]);
      event.currentTarget.setPointerCapture(event.pointerId);
      return;
    }
    const target = (event.target as Element)
      .closest("[data-entity]")
      ?.getAttribute("data-entity");
    if (tool === "select") {
      if (target) {
        select(target, event.shiftKey);
        setDrag({ id: target, from: p, to: p });
        event.currentTarget.setPointerCapture(event.pointerId);
      } else {
        setSelected([]);
        safe(execute("select.clear"));
      }
      return;
    }
    if (["trim", "extend", "break"].includes(tool)) {
      if (target && sketch.curves.some((c) => c.id === target))
        safe(execute(`sketch.${tool}`, { curve: target, at: p }));
      return;
    }
    const next = [...clicks, p];
    setClicks(next);
    if (
      next.length === (tool === "point" ? 1 : tool === "arc" ? 3 : 2) &&
      tool !== "spline"
    )
      safe(draw(next));
  };
  const constraintParams = (): JsonObject => {
    if (constraint === "fix") return { entities: selected };
    if (constraint === "horizontal_vertical") return { line: selected[0] };
    return { a: selected[0], b: selected[1] };
  };
  const preview = [...clicks, ...(hover ? [hover] : [])];
  const selectedPoint = sketch.points.find((p) => p.id === selected[0]);
  useEffect(() => {
    if (selectedPoint) {
      const [x, y] = point(selectedPoint.at);
      setPointX(String(x));
      setPointY(String(y));
    }
  }, [selectedPoint?.id, JSON.stringify(selectedPoint?.at)]);
  return (
    <div className="modeling-sketch">
      <div className="modeling-row">
        <strong>{sketch.name}</strong>
        <span
          className={
            sketch.failing?.length
              ? "modeling-danger"
              : sketch.dof
                ? "modeling-warning"
                : "modeling-success"
          }
          role="status"
        >
          {sketch.failing?.length
            ? `Conflicts: ${sketch.failing.join(", ")}`
            : sketch.dof === 0
              ? "Fully constrained · 0 DOF"
              : `${sketch.dof} degrees of freedom`}
        </span>
      </div>
      <div className="modeling-row">
        <label>
          Sketch tool
          <SelectControl
            aria-label="Sketch tool"
            value={tool}
            disabled={!active || busy}
            onChange={(e) => chooseTool(e.target.value as Tool)}
          >
            {toolNames.map(([id, name]) => (
              <option key={id} value={id}>
                {name}
              </option>
            ))}
          </SelectControl>
        </label>
        <label className="modeling-check">
          <input
            type="checkbox"
            checked={snap}
            onChange={(e) => setSnap(e.target.checked)}
          />
          1 mm grid snap
        </label>
      </div>
      {tool === "rounded" && (
        <label>
          Corner radius
          <Input value={radius} onChange={(e) => setRadius(e.target.value)} />
        </label>
      )}
      <svg
        ref={svg}
        className={`modeling-sketch-canvas modeling-tool-${tool}`}
        aria-label={`Sketch canvas ${sketch.name}`}
        role="img"
        viewBox={`${bounds.x - size / 2} ${-bounds.y - size / 2} ${size} ${size}`}
        onPointerDown={down}
        onPointerMove={(event) => {
          const p = screenPoint(event);
          setHover(p);
          if (drag) setDrag({ ...drag, to: p });
          if (trimPath && distance(trimPath.at(-1)!, p) > 0.2)
            setTrimPath([...trimPath, p]);
        }}
        onPointerUp={() => {
          if (drag && distance(drag.from, drag.to) > 0.05)
            safe(
              execute("sketch.drag", {
                entity: drag.id,
                from: drag.from,
                to: drag.to,
              }),
            );
          setDrag(null);
          if (trimPath && trimPath.length > 1) {
            const points = trimPath;
            safe(
              execute("sketch.trim.path", { points, dry_run: true }).then(
                (result) =>
                  setTrimPreview({
                    points,
                    removed: ((result as JsonObject).removed ??
                      []) as Point2[][],
                  }),
              ),
            );
          }
          setTrimPath(null);
        }}
        onPointerCancel={() => {
          setDrag(null);
          setTrimPath(null);
        }}
      >
        {canvases
          .filter((canvas) => {
            if (!canvas.visible || canvas.component) return false;
            let canvasPlane = canvas.plane.plane;
            if (canvas.plane.type === "origin" && canvas.plane.name === "XY")
              canvasPlane = {
                origin: [0, 0, 0],
                x_dir: [1, 0, 0],
                y_dir: [0, 1, 0],
              };
            return (
              canvasPlane &&
              ["origin", "x_dir", "y_dir"].every(
                (key) =>
                  JSON.stringify((canvasPlane as JsonObject)[key]) ===
                  JSON.stringify(sketch.plane[key]),
              )
            );
          })
          .map((canvas) => {
            const [x, y] = point(canvas.center);
            const height = (canvas.width * canvas.pixels[1]) / canvas.pixels[0];
            return (
              <image
                key={canvas.id}
                href={`data:image/${canvas.format};base64,${canvas.data}`}
                x={x - canvas.width / 2}
                y={-y - height / 2}
                width={canvas.width}
                height={height}
                opacity={canvas.opacity}
                transform={`rotate(${(-canvas.angle * 180) / Math.PI} ${x} ${-y})${canvas.flip ? ` translate(${2 * x},0) scale(-1,1)` : ""}`}
                pointerEvents="none"
              />
            );
          })}
        <defs>
          <pattern
            id={`modeling-grid-${sketch.id}`}
            width="5"
            height="5"
            patternUnits="userSpaceOnUse"
          >
            <path
              d="M5 0 H0 V5"
              fill="none"
              stroke="var(--border-bright)"
              strokeWidth={stroke / 3}
            />
          </pattern>
        </defs>
        <rect
          x={bounds.x - size / 2}
          y={-bounds.y - size / 2}
          width={size}
          height={size}
          fill={`url(#modeling-grid-${sketch.id})`}
        />
        <path
          d={`M${bounds.x - size / 2},0 H${bounds.x + size / 2} M0,${-bounds.y - size / 2} V${-bounds.y + size / 2}`}
          className="modeling-axis"
          strokeWidth={stroke / 2}
        />
        {sketch.curves.map((curve) => {
          const className = `modeling-curve ${curve.construction ? "is-construction" : ""} ${curve.link ? "is-linked" : ""} ${curve.fully_constrained ? "is-constrained" : ""} ${selected.includes(curve.id) ? "is-selected" : ""} ${curve.type === "spline" ? "is-control-polygon" : ""}`;
          const path = curvePath(curve);
          const props = {
            "data-entity": curve.id,
            className,
            strokeWidth: stroke,
            fill: "none",
          };
          if (curve.type === "circle") {
            const [x, y] = point(curve.center_at);
            return (
              <circle
                key={curve.id}
                {...props}
                cx={x}
                cy={-y}
                r={Number(curve.radius)}
              >
                <title>{curve.id}</title>
              </circle>
            );
          }
          if (curve.type === "ellipse") {
            const c = point(curve.center_at),
              m = point(curve.major_at);
            return (
              <ellipse
                key={curve.id}
                {...props}
                cx={c[0]}
                cy={-c[1]}
                rx={distance(c, m)}
                ry={Number(curve.minor_radius)}
                transform={`rotate(${(-Math.atan2(m[1] - c[1], m[0] - c[0]) * 180) / Math.PI} ${c[0]} ${-c[1]})`}
              />
            );
          }
          return (
            path && (
              <path key={curve.id} {...props} d={path}>
                <title>
                  {curve.id}
                  {curve.type === "spline" ? " (fit/control polygon)" : ""}
                </title>
              </path>
            )
          );
        })}
        {sketch.points.map((p) => {
          const [x, y] = point(p.at);
          return (
            <circle
              key={p.id}
              data-entity={p.id}
              className={`modeling-point ${selected.includes(p.id) ? "is-selected" : ""} ${p.fully_constrained ? "is-constrained" : ""}`}
              cx={x}
              cy={-y}
              r={stroke * 1.5}
            >
              <title>
                {p.id} · {x}, {y} mm
              </title>
            </circle>
          );
        })}
        {sketch.profiles.map((p) => {
          const [x, y] = point(p.centroid_sketch);
          return (
            <g
              key={p.index}
              className="modeling-profile"
              role="button"
              aria-label={`Select profile ${p.index}`}
              tabIndex={0}
              onPointerDown={(e) => {
                if (tool !== "select") return;
                e.stopPropagation();
                const ids = profileIds.includes(p.index)
                  ? profileIds.filter((i) => i !== p.index)
                  : [...profileIds, p.index];
                selectProfiles(ids);
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  selectProfiles(
                    profileIds.includes(p.index)
                      ? profileIds.filter((i) => i !== p.index)
                      : [...profileIds, p.index],
                  );
                }
              }}
            >
              <circle
                cx={x}
                cy={-y}
                r={stroke * 3}
                className={profileIds.includes(p.index) ? "is-selected" : ""}
              />
              <text
                x={x}
                y={-y + stroke}
                textAnchor="middle"
                fontSize={stroke * 3}
              >
                {p.index}
              </text>
            </g>
          );
        })}
        {preview.length > 1 && ["line", "arc", "spline"].includes(tool) && (
          <polyline
            points={preview.map(at).join(" ")}
            className="modeling-placement"
            strokeWidth={stroke}
          />
        )}
        {preview.length === 2 && ["rectangle", "rounded"].includes(tool) && (
          <rect
            x={Math.min(preview[0][0], preview[1][0])}
            y={-Math.max(preview[0][1], preview[1][1])}
            width={Math.abs(preview[1][0] - preview[0][0])}
            height={Math.abs(preview[1][1] - preview[0][1])}
            className="modeling-placement"
            strokeWidth={stroke}
          />
        )}
        {preview.length === 2 && tool === "circle" && (
          <circle
            cx={preview[0][0]}
            cy={-preview[0][1]}
            r={distance(preview[0], preview[1])}
            className="modeling-placement"
            strokeWidth={stroke}
          />
        )}
        {drag && (
          <path
            d={`M${at(drag.from)} L${at(drag.to)}`}
            className="modeling-placement"
            strokeWidth={stroke}
          />
        )}
        {trimPath && (
          <polyline
            points={trimPath.map(at).join(" ")}
            className="modeling-placement"
            strokeWidth={stroke}
          />
        )}
        {trimPreview?.removed.map((polyline, index) => (
          <polyline
            key={index}
            points={polyline.map(at).join(" ")}
            className="modeling-trim-preview"
            strokeWidth={stroke * 2}
          />
        ))}
        {glyphs.map((glyph) => {
          const [x, y] = point(glyph.at);
          const constraint = sketch.constraints.find((c) => c.id === glyph.id);
          return (
            <text
              key={glyph.id}
              x={x + stroke * 3}
              y={-y - stroke * 3}
              fontSize={stroke * 4}
              className={`modeling-glyph ${glyph.failing ? "is-failing" : ""}`}
            >
              <title>
                {glyph.id} · {glyph.name}
              </title>
              {glyph.dimension
                ? (constraint?.expression ??
                  glyph.value?.toFixed(2) ??
                  glyph.name)
                : glyph.name}
            </text>
          );
        })}
      </svg>
      {trimPreview && (
        <div className="modeling-row">
          <span className="modeling-muted">
            Engine preview · {trimPreview.removed.length} removed pieces
          </span>
          <Button
            disabled={busy || !trimPreview.removed.length}
            onClick={() =>
              safe(
                execute("sketch.trim.path", {
                  points: trimPreview.points,
                }).then(() => setTrimPreview(null)),
              )
            }
          >
            Apply trim path
          </Button>
          <Button onClick={() => setTrimPreview(null)}>Cancel preview</Button>
        </div>
      )}
      <div className="modeling-row modeling-muted">
        <span>
          {hover ? `${hover[0]}, ${hover[1]} mm` : "Sketch coordinates · mm"}
        </span>
        <Button
          aria-label="Zoom sketch in"
          onClick={() => setZoom(Math.min(8, zoom * 1.4))}
        >
          +
        </Button>
        <Button
          aria-label="Zoom sketch out"
          onClick={() => setZoom(Math.max(0.2, zoom / 1.4))}
        >
          −
        </Button>
        <Button
          onClick={() => {
            setZoom(1);
            setFitRevision((value) => value + 1);
          }}
        >
          Fit
        </Button>
      </div>
      <p className="modeling-muted">
        {tool === "select"
          ? "Click geometry to select; Shift adds. Drag to solve a move. Numbered markers select closed profiles."
          : ["trim", "extend", "break"].includes(tool)
            ? "Click the curve segment to modify."
            : `Click ${tool === "arc" ? "start, end, then a through point" : tool === "circle" ? "center, then radius" : tool === "point" ? "to place a point" : "to place geometry"}. Dashed strokes show placement only.`}
        {sketch.curves.some((c) => c.type === "spline") &&
          " Dashed spline polygons show editable fit/control points."}
      </p>
      {clicks.length > 0 && (
        <div className="modeling-row">
          {tool === "spline" && (
            <Button
              disabled={busy || clicks.length < 2}
              onClick={() => safe(draw(clicks))}
            >
              Finish spline
            </Button>
          )}
          <Button onClick={() => setClicks([])}>Cancel placement</Button>
        </div>
      )}
      {!active && (
        <Button
          variant="primary"
          disabled={busy}
          onClick={() => safe(execute("sketch.edit", { sketch: sketch.id }))}
        >
          Edit this sketch
        </Button>
      )}
      {active && (
        <>
          <details>
            <summary>Entities and dimensions</summary>
            <div className="modeling-entity-list" aria-label="Sketch entities">
              {sketch.curves.map((c) => (
                <Button
                  key={c.id}
                  aria-pressed={selected.includes(c.id)}
                  onClick={(e) => select(c.id, e.shiftKey)}
                >
                  {c.id} · {c.type}
                </Button>
              ))}
              {sketch.points.map((p) => (
                <Button
                  key={p.id}
                  aria-pressed={selected.includes(p.id)}
                  onClick={(e) => select(p.id, e.shiftKey)}
                >
                  {p.id} · point
                </Button>
              ))}
            </div>
          </details>
          <div className="modeling-muted">
            Selection: {selected.join(", ") || "none"}
          </div>
          {selectedPoint && (
            <form
              className="modeling-inline-form"
              onSubmit={(e) => {
                e.preventDefault();
                safe(
                  execute("sketch.move_point", {
                    point: selectedPoint.id,
                    to: [Number(pointX), Number(pointY)],
                  }),
                );
              }}
            >
              <label>
                X
                <Input
                  aria-label="Point X"
                  type="number"
                  step="any"
                  required
                  value={pointX}
                  onChange={(e) => setPointX(e.target.value)}
                />
              </label>
              <label>
                Y
                <Input
                  aria-label="Point Y"
                  type="number"
                  step="any"
                  required
                  value={pointY}
                  onChange={(e) => setPointY(e.target.value)}
                />
              </label>
              <Button type="submit" disabled={busy}>
                Move point
              </Button>
            </form>
          )}
          <form
            className="modeling-form"
            onSubmit={(e) => {
              e.preventDefault();
              safe(
                execute("sketch.dimension", {
                  entities: selected,
                  type: dimensionType,
                  value: dimension,
                  ...(referenceDimension ? { if_redundant: "driven" } : {}),
                }),
              );
            }}
          >
            <label>
              Dimension type
              <SelectControl
                aria-label="Dimension type"
                value={dimensionType}
                onChange={(e) => setDimensionType(e.target.value)}
              >
                {[
                  "auto",
                  "distance",
                  "horizontal",
                  "vertical",
                  "length",
                  "radius",
                  "diameter",
                  "angle",
                ].map((name) => (
                  <option key={name}>{name}</option>
                ))}
              </SelectControl>
            </label>
            <label>
              Dimension value or equation
              <Input
                aria-label="Dimension value"
                value={dimension}
                onChange={(e) => setDimension(e.target.value)}
                placeholder="width = 40 mm"
              />
            </label>
            <Button type="submit" disabled={busy || !selected.length}>
              Add dimension
            </Button>
            <label className="modeling-check">
              <input
                type="checkbox"
                checked={referenceDimension}
                onChange={(e) => setReferenceDimension(e.target.checked)}
              />
              Use a reference dimension when already determined
            </label>
          </form>
          <div className="modeling-form">
            <label>
              Geometric constraint
              <SelectControl
                aria-label="Geometric constraint"
                value={constraint}
                onChange={(e) => setConstraint(e.target.value)}
              >
                {[
                  "horizontal_vertical",
                  "coincident",
                  "tangent",
                  "equal",
                  "parallel",
                  "perpendicular",
                  "concentric",
                  "collinear",
                  "fix",
                ].map((name) => (
                  <option key={name}>{name}</option>
                ))}
              </SelectControl>
            </label>
            <Button
              disabled={
                busy ||
                selected.length <
                  (["fix", "horizontal_vertical"].includes(constraint) ? 1 : 2)
              }
              onClick={() =>
                safe(
                  execute(
                    `sketch.constraint.${constraint}`,
                    constraintParams(),
                  ),
                )
              }
            >
              Apply constraint
            </Button>
          </div>
          <div className="modeling-row">
            <Button
              disabled={busy || !selected.length}
              onClick={() =>
                safe(execute("sketch.construction", { curves: selected }))
              }
            >
              Construction
            </Button>
            <Button
              disabled={busy || !selected.length}
              variant="danger"
              onClick={() =>
                safe(execute("sketch.delete", { entities: selected }))
              }
            >
              Delete selected
            </Button>
            <Button
              disabled={busy}
              onClick={() => safe(execute("sketch.auto_constrain"))}
            >
              Auto constrain
            </Button>
            <Button
              disabled={busy}
              onClick={() => safe(execute("sketch.solve"))}
            >
              Solve
            </Button>
            <Button
              disabled={busy}
              onClick={() =>
                safe(
                  execute("sketch.glyphs", { sketch: sketch.id }).then(
                    (result) =>
                      setGlyphs((result as { glyphs: typeof glyphs }).glyphs),
                  ),
                )
              }
            >
              Show constraint glyphs
            </Button>
          </div>
          <details>
            <summary>Constraints ({sketch.constraints.length})</summary>
            <div className="modeling-constraints">
              {sketch.constraints.map((c) => (
                <div
                  className={`modeling-row ${sketch.failing.includes(c.id) ? "modeling-danger" : ""}`}
                  key={c.id}
                >
                  <span>
                    {c.id} · {c.name}
                    {c.expression ? ` = ${c.expression}` : ""}
                  </span>
                  {c.param && (
                    <Button
                      disabled={busy}
                      onClick={() =>
                        safe(
                          execute("sketch.toggle_driven", { constraint: c.id }),
                        )
                      }
                    >
                      Driving / driven
                    </Button>
                  )}
                  <Button
                    aria-label={`Remove constraint ${c.id}`}
                    disabled={busy}
                    onClick={() =>
                      safe(execute("sketch.delete", { entities: [c.id] }))
                    }
                  >
                    ×
                  </Button>
                </div>
              ))}
            </div>
          </details>
        </>
      )}
      <div className="modeling-profile-list">
        {sketch.profiles.map((p) => (
          <label key={p.index} className="modeling-check">
            <input
              type="checkbox"
              checked={profileIds.includes(p.index)}
              onChange={(e) => {
                const ids = e.target.checked
                  ? [...profileIds, p.index]
                  : profileIds.filter((i) => i !== p.index);
                selectProfiles(ids);
              }}
            />
            Profile {p.index} · {p.area_mm2.toFixed(2)} mm² ·{" "}
            {p.holes?.length ?? 0} holes
          </label>
        ))}
      </div>
    </div>
  );
}
