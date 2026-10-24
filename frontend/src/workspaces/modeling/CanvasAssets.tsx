import { useState } from "react";
import { Button, Input, SelectControl } from "../../components/ui";
import {
  errorText,
  type Execute,
  type NativeCanvas,
  type NativeSketch,
} from "./native";

export default function CanvasAssets({
  canvases,
  sketch,
  execute,
  busy,
}: {
  canvases: NativeCanvas[];
  sketch: NativeSketch;
  execute: Execute;
  busy: boolean;
}) {
  const [canvas, setCanvas] = useState("");
  const [a, setA] = useState("[0,0]");
  const [b, setB] = useState("[10,0]");
  const [length, setLength] = useState("10");
  const [error, setError] = useState("");
  const [width, setWidth] = useState("100");
  return (
    <details>
      <summary>Reference image and calibration</summary>
      <label>
        Image width (mm)
        <Input
          type="number"
          min="0.001"
          step="any"
          value={width}
          onChange={(e) => setWidth(e.target.value)}
        />
      </label>
      <label>
        Insert PNG or JPEG
        <Input
          type="file"
          accept="image/png,image/jpeg"
          disabled={busy}
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (!file) return;
            if (file.size > 512 * 1024) {
              setError(
                "Reference images must be at most 512 KiB for the native transaction limit.",
              );
              return;
            }
            setError("");
            const reader = new FileReader();
            reader.onerror = () =>
              setError("The reference image could not be read.");
            reader.onload = () => {
              const data = String(reader.result).split(",")[1];
              void execute("canvas.insert", {
                data,
                name: file.name,
                plane: sketch.plane,
                width: Number(width),
              }).catch((e) => setError(errorText(e)));
            };
            reader.readAsDataURL(file);
            event.target.value = "";
          }}
        />
      </label>
      {canvases.length > 0 && (
        <form
          className="modeling-form"
          onSubmit={(event) => {
            event.preventDefault();
            setError("");
            try {
              void execute("canvas.calibrate", {
                canvas: Number(canvas),
                a: JSON.parse(a),
                b: JSON.parse(b),
                distance: Number(length),
              }).catch((e) => setError(errorText(e)));
            } catch (e) {
              setError(errorText(e));
            }
          }}
        >
          <label>
            Reference image
            <SelectControl
              aria-label="Reference image"
              value={canvas}
              onChange={(e) => setCanvas(e.target.value)}
            >
              <option value="">Choose reference image</option>
              {canvases.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name} · {c.width.toFixed(3)} mm
                </option>
              ))}
            </SelectControl>
          </label>
          <label>
            First calibration point (plane mm)
            <Input value={a} onChange={(e) => setA(e.target.value)} />
          </label>
          <label>
            Second calibration point (plane mm)
            <Input value={b} onChange={(e) => setB(e.target.value)} />
          </label>
          <label>
            True distance (mm)
            <Input
              type="number"
              min="0.001"
              step="any"
              value={length}
              onChange={(e) => setLength(e.target.value)}
            />
          </label>
          <Button type="submit" disabled={busy || !canvas}>
            Calibrate reference image
          </Button>
          <div className="modeling-row">
            <Button
              disabled={busy || !canvas}
              onClick={() =>
                void execute("canvas.edit", {
                  canvas: Number(canvas),
                  visible: !canvases.find((c) => c.id === Number(canvas))
                    ?.visible,
                }).catch(() => {})
              }
            >
              Show / hide image
            </Button>
            <Button
              disabled={busy || !canvas}
              variant="danger"
              onClick={() =>
                void execute("canvas.delete", { canvas: Number(canvas) })
                  .then(() => setCanvas(""))
                  .catch(() => {})
              }
            >
              Remove image
            </Button>
          </div>
        </form>
      )}
      {error && (
        <p className="modeling-danger" role="alert">
          {error}
        </p>
      )}
      <p className="modeling-muted">
        Image bytes and calibrated scale are saved in the native design.
        Root-component images on this sketch plane appear behind the geometry.
      </p>
    </details>
  );
}
