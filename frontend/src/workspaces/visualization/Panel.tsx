import { useEffect, useRef, useState } from "react";
import { artifact } from "../../api";
import { Button, Input, SelectControl } from "../../components/ui";
import type { DomainPanelProps, JsonObject } from "../contracts";
import {
  appearanceDefault,
  cameraEvent,
  dataDefault,
  nativeFaces,
  previewEvent,
  sampleScene,
  type CameraState,
  type NativeScene,
  type Scene,
  type Storyboard,
  type Visualization,
} from "./scene";
import "./visualization.css";

export default function VisualizationPanel(props: DomainPanelProps) {
  const [data, setData] = useState<Visualization>(() =>
    Object.keys(props.domainData).length
      ? (props.domainData as unknown as Visualization)
      : dataDefault(),
  );
  const [geometry, setGeometry] = useState<NativeScene | null>(null);
  const [caps, setCaps] = useState<JsonObject>({});
  const [gallery, setGallery] = useState<any[]>([]);
  const [target, setTarget] = useState("*");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [boardIndex, setBoardIndex] = useState(0);
  const [time, setTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [callout, setCallout] = useState("");
  const [advanced, setAdvanced] = useState("");
  const [movieFormat, setMovieFormat] = useState("png_sequence");
  const scene =
    data.scenes.find((s) => s.name === data.active_scene) ?? data.scenes[0];
  const board = data.storyboards[boardIndex];
  const look = scene.appearances[target] ?? appearanceDefault();
  const cameraRef = useRef<CameraState | null>(null);
  const dataRef = useRef(data);
  dataRef.current = data;
  const sceneRef = useRef(scene);
  sceneRef.current = scene;
  const run = async (operation: string, input: JsonObject = {}) =>
    props.runDomain("visualization", operation, input);
  const act = async (fn: () => Promise<void>) => {
    setBusy(true);
    setStatus("");
    try {
      await fn();
    } catch (error) {
      props.setError(String(error));
      setStatus(String(error));
    } finally {
      setBusy(false);
    }
  };
  const change = (patch: Partial<Scene>) =>
    setData((d) => ({
      ...d,
      scenes: d.scenes.map((s) =>
        s.name === scene.name ? { ...s, ...patch } : s,
      ),
    }));
  const appearance = (patch: Partial<typeof look>) =>
    change({
      appearances: { ...scene.appearances, [target]: { ...look, ...patch } },
    });
  const editBoard = (patch: Partial<Storyboard>) =>
    setData((d) => ({
      ...d,
      storyboards: d.storyboards.map((b, i) =>
        i === boardIndex ? { ...b, ...patch } : b,
      ),
    }));
  const save = async () => {
    await props.saveDomain("visualization", data as unknown as JsonObject);
    setStatus("Scene and storyboards saved");
  };
  useEffect(() => {
    if (Object.keys(props.domainData).length)
      setData(props.domainData as unknown as Visualization);
  }, [props.domainData]);
  useEffect(() => {
    let alive = true;
    void act(async () => {
      const g = await run("scene");
      if (alive) setGeometry(g as unknown as NativeScene);
      const c = await run("capabilities");
      if (alive) setCaps(c);
      const results = await run("gallery");
      if (alive) setGallery(results.results as any[]);
    });
    const capture = (event: Event) => {
      cameraRef.current = (event as CustomEvent).detail;
    };
    window.addEventListener(cameraEvent, capture);
    return () => {
      alive = false;
      window.removeEventListener(cameraEvent, capture);
      window.dispatchEvent(new CustomEvent(previewEvent, { detail: null }));
    };
  }, [props.project.revision]);
  useEffect(() => {
    const sampled =
      board && board.scene === scene.name
        ? sampleScene(scene, board, time)
        : { scene, callout: "" };
    window.dispatchEvent(
      new CustomEvent(previewEvent, {
        detail: { ...sampled, geometry, assets: data.assets },
      }),
    );
  }, [scene, geometry, time, board, data.assets]);
  useEffect(() => {
    if (!playing || !board) return;
    const start = performance.now() - time * 1000;
    let frame = 0;
    const tick = () => {
      const t = (performance.now() - start) / 1000;
      setTime(Math.min(t, board.duration));
      if (t >= board.duration) setPlaying(false);
      else frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [playing, board?.duration]);
  const captureCamera = () => {
    window.dispatchEvent(new CustomEvent("o3d:visualization-capture"));
    return structuredClone(cameraRef.current ?? scene.camera);
  };
  const addBoard = () => {
    const name = `Storyboard ${data.storyboards.length + 1}`;
    setData((d) => ({
      ...d,
      storyboards: [
        ...d.storyboards,
        {
          name,
          scene: scene.name,
          duration: 3,
          fps: 10,
          keyframes: [
            {
              time: 0,
              camera: captureCamera(),
              transforms: structuredClone(scene.transforms),
              hidden: [...scene.hidden],
              callout: "",
            },
          ],
        },
      ],
    }));
    setBoardIndex(data.storyboards.length);
    setTime(0);
  };
  const publish = () =>
    act(async () => {
      const result = await run("publish", {
        data: data as unknown as JsonObject,
        storyboard: board.name,
        format: movieFormat,
      });
      setGallery((g) => [result, ...g]);
      setStatus(`Published ${result.frame_count} frames at ${result.fps} FPS`);
    });
  const number = (
    label: string,
    value: number,
    onChange: (n: number) => void,
    min?: number,
    max?: number,
    step = 1,
  ) => (
    <label>
      {label}
      <Input
        aria-label={label}
        type="number"
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </label>
  );
  const vector = (
    label: string,
    value: number[],
    onChange: (n: number[]) => void,
  ) => (
    <fieldset className="visualization-vector">
      <legend>{label}</legend>
      {value.map((v, i) => (
        <Input
          key={i}
          aria-label={`${label} ${"XYZ"[i]}`}
          type="number"
          step="0.1"
          value={v}
          onChange={(e) =>
            onChange(
              value.map((n, j) => (j === i ? Number(e.target.value) : n)),
            )
          }
        />
      ))}
    </fieldset>
  );
  return (
    <section
      className="visualization-panel"
      aria-label="Visualization workspace"
    >
      <div className="visualization-toolbar">
        <strong>Visualization</strong>
        <Button
          disabled={busy}
          variant="primary"
          onClick={() => void act(save)}
        >
          Save scene
        </Button>
      </div>
      <p>
        Product scenes preserve engineering geometry and physical materials. CPU
        raster uses direct lighting; optical effects depend on the selected
        backend.
      </p>
      <label>
        Saved scene
        <SelectControl
          value={scene.name}
          onChange={(e) => {
            setTime(0);
            setData((d) => ({ ...d, active_scene: e.target.value }));
          }}
        >
          {data.scenes.map((s) => (
            <option key={s.name}>{s.name}</option>
          ))}
        </SelectControl>
      </label>
      <div className="visualization-toolbar">
        <Button
          onClick={() => {
            let n = 2;
            let name = `${scene.name} ${n}`;
            while (data.scenes.some((s) => s.name === name))
              name = `${scene.name} ${++n}`;
            setData((d) => ({
              ...d,
              active_scene: name,
              scenes: [...d.scenes, { ...structuredClone(scene), name }],
            }));
          }}
        >
          Duplicate scene
        </Button>
        <Button onClick={() => change({ camera: captureCamera() })}>
          Capture camera
        </Button>
      </div>
      <label>
        Scene name
        <Input
          value={scene.name}
          onChange={(e) => {
            const name = e.target.value;
            setData((d) => ({
              ...d,
              active_scene: name,
              scenes: d.scenes.map((s) =>
                s.name === scene.name ? { ...s, name } : s,
              ),
              storyboards: d.storyboards.map((b) =>
                b.scene === scene.name ? { ...b, scene: name } : b,
              ),
            }));
          }}
        />
      </label>
      <details open>
        <summary>Camera and display</summary>
        <label>
          Standard view
          <SelectControl
            defaultValue="iso"
            onChange={(e) => {
              const poses: Record<string, number[]> = {
                iso: [-Math.PI / 4, 0.61548],
                front: [0, 0],
                back: [Math.PI, 0],
                left: [Math.PI / 2, 0],
                right: [-Math.PI / 2, 0],
                top: [0, Math.PI / 2],
                bottom: [0, -Math.PI / 2],
              };
              const [yaw, pitch] = poses[e.target.value];
              change({ camera: { ...scene.camera, yaw, pitch } });
            }}
          >
            {["iso", "front", "back", "left", "right", "top", "bottom"].map(
              (v) => (
                <option key={v}>{v}</option>
              ),
            )}
          </SelectControl>
        </label>
        <label>
          Projection
          <SelectControl
            value={scene.camera.fov ? "perspective" : "orthographic"}
            onChange={(e) =>
              change({
                camera: {
                  ...scene.camera,
                  fov: e.target.value === "perspective" ? Math.PI / 4 : 0,
                },
              })
            }
          >
            <option>orthographic</option>
            <option>perspective</option>
          </SelectControl>
        </label>
        {number(
          "Camera distance mm",
          scene.camera.distance,
          (distance) => change({ camera: { ...scene.camera, distance } }),
          0.01,
          1e7,
        )}
        {vector("Camera target", scene.camera.target, (target) =>
          change({ camera: { ...scene.camera, target } }),
        )}
        {number(
          "Lens field of view degrees",
          (scene.camera.fov * 180) / Math.PI,
          (fov) =>
            change({ camera: { ...scene.camera, fov: (fov * Math.PI) / 180 } }),
          0,
          149,
        )}
        <label>
          Visual style
          <SelectControl
            value={scene.style}
            onChange={(e) =>
              change({ style: e.target.value as Scene["style"] })
            }
          >
            {["shaded_edges", "shaded", "wireframe"].map((v) => (
              <option key={v}>{v}</option>
            ))}
          </SelectControl>
        </label>
        <Button
          onClick={() =>
            window.dispatchEvent(new CustomEvent("o3d:visualization-fit"))
          }
        >
          Fit visible bodies
        </Button>
      </details>
      <details open>
        <summary>Appearance, visibility and exploded pose</summary>
        <label>
          Appearance target
          <SelectControl
            value={target}
            onChange={(e) => setTarget(e.target.value)}
          >
            <option value="*">All bodies (default)</option>
            {geometry?.bodies.map((b) => (
              <option key={b.id} value={b.id}>
                {b.name}
              </option>
            ))}
            {props.selection && (
              <option value={props.selection.id}>Selected face</option>
            )}
          </SelectControl>
        </label>
        <div className="visualization-row">
          <label>
            Color
            <Input
              type="color"
              aria-label="Appearance color"
              value={look.color}
              onChange={(e) => appearance({ color: e.target.value })}
            />
          </label>
          {number(
            "Metallic",
            look.metallic,
            (metallic) => appearance({ metallic }),
            0,
            1,
            0.05,
          )}
          {number(
            "Roughness",
            look.roughness,
            (roughness) => appearance({ roughness }),
            0.045,
            1,
            0.05,
          )}
        </div>
        {number(
          "Appearance opacity",
          look.opacity,
          (opacity) => appearance({ opacity }),
          0.01,
          1,
          0.05,
        )}
        {target !== "*" && (
          <>
            <div className="visualization-toolbar">
              <Button
                onClick={() =>
                  change({
                    hidden: scene.hidden.includes(target)
                      ? scene.hidden.filter((id) => id !== target)
                      : [...scene.hidden, target],
                  })
                }
              >
                {scene.hidden.includes(target) ? "Show target" : "Hide target"}
              </Button>
              <Button
                onClick={() =>
                  change({
                    isolated: scene.isolated.includes(target) ? [] : [target],
                  })
                }
              >
                Isolate target
              </Button>
              <Button onClick={() => change({ hidden: [], isolated: [] })}>
                Show all
              </Button>
            </div>
            {!target.includes(":face:") &&
              vector(
                "Presentation translation mm",
                scene.transforms[target] ?? [0, 0, 0],
                (translation) =>
                  change({
                    transforms: { ...scene.transforms, [target]: translation },
                  }),
              )}
          </>
        )}
        <Button onClick={() => change({ transforms: {} })}>
          Reset exploded pose
        </Button>
        <label>
          Project texture / decal
          <Input
            type="file"
            accept="image/png,image/jpeg,image/webp"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (!file) return;
              void act(async () => {
                if (file.size > 6_000_000)
                  throw new Error("Image exceeds 6 MiB");
                const raw = await file.arrayBuffer();
                let binary = "";
                new Uint8Array(raw).forEach(
                  (n) => (binary += String.fromCharCode(n)),
                );
                const reply = await run("import_image", {
                  base64: btoa(binary),
                });
                const asset = reply.asset as any;
                setData((d) => ({
                  ...d,
                  assets: { ...d.assets, [asset.sha256]: asset },
                  scenes: d.scenes.map((s) =>
                    s.name === scene.name
                      ? {
                          ...s,
                          appearances: {
                            ...s.appearances,
                            [target]: {
                              ...look,
                              texture: {
                                asset: asset.sha256,
                                projection: "xy",
                                origin: [0, 0, 0],
                                size_mm: [20, 20],
                                rotation: 0,
                                opacity: 1,
                                repeat: false,
                              },
                            },
                          },
                        }
                      : s,
                  ),
                }));
              });
            }}
          />
        </label>
        {look.texture && (
          <>
            <label>
              Texture projection
              <SelectControl
                value={look.texture.projection}
                onChange={(e) =>
                  appearance({
                    texture: {
                      ...look.texture!,
                      projection: e.target.value as any,
                    },
                  })
                }
              >
                {["xy", "yz", "zx", "cylindrical_z"].map((v) => (
                  <option key={v}>{v}</option>
                ))}
              </SelectControl>
            </label>
            {number(
              "Texture width mm",
              look.texture.size_mm[0],
              (v) =>
                appearance({
                  texture: {
                    ...look.texture!,
                    size_mm: [v, look.texture!.size_mm[1]],
                  },
                }),
              0.01,
            )}
            {number(
              "Texture height mm",
              look.texture.size_mm[1],
              (v) =>
                appearance({
                  texture: {
                    ...look.texture!,
                    size_mm: [look.texture!.size_mm[0], v],
                  },
                }),
              0.01,
            )}
            {vector("Texture origin mm", look.texture.origin, (origin) =>
              appearance({ texture: { ...look.texture!, origin } }),
            )}
            {number(
              "Texture rotation radians",
              look.texture.rotation,
              (rotation) =>
                appearance({ texture: { ...look.texture!, rotation } }),
              -1000,
              1000,
              0.1,
            )}
            {number(
              "Decal opacity",
              look.texture.opacity,
              (opacity) =>
                appearance({ texture: { ...look.texture!, opacity } }),
              0,
              1,
              0.1,
            )}
            <label>
              <input
                type="checkbox"
                checked={look.texture.repeat}
                onChange={(e) =>
                  appearance({
                    texture: { ...look.texture!, repeat: e.target.checked },
                  })
                }
              />{" "}
              Repeat texture
            </label>
            <Button onClick={() => appearance({ texture: null })}>
              Remove texture
            </Button>
          </>
        )}
      </details>
      <details>
        <summary>Named section planes</summary>
        <p>
          Inspection clipping preserves geometry. Cut surfaces are uncapped.
        </p>
        {scene.sections.map((s, i) => (
          <fieldset key={i}>
            <Input
              aria-label={`Section ${i + 1} name`}
              value={s.name}
              onChange={(e) =>
                change({
                  sections: scene.sections.map((v, j) =>
                    i === j ? { ...v, name: e.target.value } : v,
                  ),
                })
              }
            />
            {vector(`Section ${i + 1} normal`, s.normal, (normal) =>
              change({
                sections: scene.sections.map((v, j) =>
                  i === j ? { ...v, normal } : v,
                ),
              }),
            )}
            {number(`Section ${i + 1} offset mm`, s.offset_mm, (offset_mm) =>
              change({
                sections: scene.sections.map((v, j) =>
                  i === j ? { ...v, offset_mm } : v,
                ),
              }),
            )}
            <label>
              <input
                type="checkbox"
                checked={s.enabled}
                onChange={(e) =>
                  change({
                    sections: scene.sections.map((v, j) =>
                      i === j ? { ...v, enabled: e.target.checked } : v,
                    ),
                  })
                }
              />{" "}
              Enabled
            </label>
            <Button
              onClick={() =>
                change({ sections: scene.sections.filter((_, j) => i !== j) })
              }
            >
              Remove section
            </Button>
          </fieldset>
        ))}
        <Button
          disabled={scene.sections.length >= 6}
          onClick={() =>
            change({
              sections: [
                ...scene.sections,
                {
                  name: `Section ${scene.sections.length + 1}`,
                  normal: [1, 0, 0],
                  offset_mm: 0,
                  enabled: true,
                },
              ],
            })
          }
        >
          Add section
        </Button>
      </details>
      <details>
        <summary>Scene lighting and background</summary>
        <label>
          Background
          <Input
            type="color"
            aria-label="Scene background"
            value={scene.background}
            onChange={(e) => change({ background: e.target.value })}
          />
        </label>
        {number(
          "Ambient intensity",
          scene.ambient,
          (ambient) => change({ ambient }),
          0,
          4,
          0.05,
        )}
        {number(
          "Exposure EV",
          scene.exposure_ev,
          (exposure_ev) => change({ exposure_ev }),
          -8,
          8,
          0.25,
        )}
        {number(
          "Environment light rotation radians",
          scene.environment_rotation,
          (environment_rotation) => change({ environment_rotation }),
          -1000,
          1000,
          0.1,
        )}
        {scene.lights.map((light, i) => {
          const patch = (p: Partial<typeof light>) =>
            change({
              lights: scene.lights.map((v, j) =>
                i === j ? { ...v, ...p } : v,
              ),
            });
          return (
            <fieldset key={i}>
              <legend>{light.name}</legend>
              <label>
                Light type
                <SelectControl
                  value={light.kind}
                  onChange={(e) => patch({ kind: e.target.value as any })}
                >
                  <option>directional</option>
                  <option>point</option>
                  <option>spot</option>
                </SelectControl>
              </label>
              {vector(
                `Light ${i + 1} ${light.kind === "directional" ? "direction" : "position mm"}`,
                light.vector,
                (vector) => patch({ vector }),
              )}
              {light.kind === "spot" && (
                <>
                  {vector(`Light ${i + 1} target mm`, light.target, (target) =>
                    patch({ target }),
                  )}
                  {number(
                    `Light ${i + 1} cone degrees`,
                    light.cone_degrees,
                    (cone_degrees) => patch({ cone_degrees }),
                    1,
                    170,
                  )}
                </>
              )}
              {number(
                `Light ${i + 1} intensity`,
                light.intensity,
                (intensity) => patch({ intensity }),
                0,
                100,
                0.1,
              )}
              {light.kind !== "directional" &&
                number(
                  `Light ${i + 1} reference distance mm`,
                  light.reference_distance_mm,
                  (reference_distance_mm) => patch({ reference_distance_mm }),
                  0.01,
                )}
              <label>
                Light color
                <Input
                  type="color"
                  value={light.color}
                  onChange={(e) => patch({ color: e.target.value })}
                />
              </label>
              <Button
                onClick={() =>
                  change({ lights: scene.lights.filter((_, j) => i !== j) })
                }
              >
                Remove light
              </Button>
            </fieldset>
          );
        })}
        <Button
          disabled={scene.lights.length >= 8}
          onClick={() =>
            change({
              lights: [
                ...scene.lights,
                {
                  name: `Light ${scene.lights.length + 1}`,
                  kind: "point",
                  vector: [50, -50, 100],
                  target: [0, 0, 0],
                  intensity: 1,
                  color: "#ffffff",
                  cone_degrees: 40,
                  reference_distance_mm: 100,
                },
              ],
            })
          }
        >
          Add scene light
        </Button>
        <p>
          Intensity is relative; point and spot falloff uses the explicit
          reference distance. HDR and photometric files are unavailable in this
          renderer.
        </p>
      </details>
      <details open>
        <summary>Render and retained results</summary>
        <div className="visualization-row">
          {number(
            "Render width",
            scene.width,
            (width) => change({ width }),
            16,
            2048,
          )}
          {number(
            "Render height",
            scene.height,
            (height) => change({ height }),
            16,
            2048,
          )}
        </div>
        <label>
          Render backend
          <SelectControl
            value={scene.renderer}
            onChange={(e) =>
              change({ renderer: e.target.value as Scene["renderer"] })
            }
          >
            <option value="cpu-raster">CPU raster · direct lighting</option>
            {JSON.stringify(caps.native ?? {}).includes("cpu-raytrace") && (
              <option value="native-cpu-raytrace">
                Native CPU raytrace · preview
              </option>
            )}
          </SelectControl>
        </label>
        <label>
          <input
            type="checkbox"
            checked={scene.transparent}
            onChange={(e) => change({ transparent: e.target.checked })}
          />{" "}
          Transparent PNG background
        </label>
        <Button
          disabled={busy}
          variant="primary"
          onClick={() =>
            void act(async () => {
              const result = await run("render", {
                data: data as unknown as JsonObject,
                scene: scene.name,
              });
              setGallery((g) => [result, ...g]);
              setStatus("Render completed");
            })
          }
        >
          Render image
        </Button>
        {gallery.map((result, i) => (
          <div className="visualization-result" key={result.manifest ?? i}>
            <span>
              Revision {result.revision} · {result.renderer}
              {result.revision !== props.project.revision ? " · stale" : ""}
            </span>
            {result.artifacts?.image && (
              <a
                href={artifact(result.artifacts.image)}
                target="_blank"
                rel="noreferrer"
              >
                <img
                  alt={`Product render revision ${result.revision}`}
                  src={artifact(result.artifacts.image)}
                />
              </a>
            )}
            {Object.entries(result.artifacts ?? {}).map(([kind, path]) => (
              <a key={kind} download href={artifact(path as string)}>
                Download {kind.replaceAll("_", " ")}
              </a>
            ))}
          </div>
        ))}
      </details>
      <details open>
        <summary>Storyboards and animation</summary>
        <Button onClick={addBoard}>New storyboard</Button>
        {board && (
          <>
            <label>
              Storyboard
              <SelectControl
                value={boardIndex}
                onChange={(e) => {
                  setPlaying(false);
                  setBoardIndex(Number(e.target.value));
                  setTime(0);
                }}
              >
                {data.storyboards.map((b, i) => (
                  <option value={i} key={i}>
                    {b.name}
                  </option>
                ))}
              </SelectControl>
            </label>
            <label>
              Storyboard name
              <Input
                value={board.name}
                onChange={(e) => editBoard({ name: e.target.value })}
              />
            </label>
            <div className="visualization-row">
              {number(
                "Duration seconds",
                board.duration,
                (duration) => editBoard({ duration }),
                0.1,
                120,
                0.1,
              )}
              {number(
                "Frames per second",
                board.fps,
                (fps) => editBoard({ fps }),
                1,
                30,
              )}
            </div>
            <label>
              Timeline {time.toFixed(2)} seconds
              <Input
                aria-label="Storyboard timeline"
                type="range"
                min={0}
                max={board.duration}
                step={0.01}
                value={time}
                onChange={(e) => {
                  setPlaying(false);
                  setTime(Number(e.target.value));
                }}
              />
            </label>
            <div className="visualization-toolbar">
              <Button
                onClick={() => {
                  if (time >= board.duration) setTime(0);
                  setPlaying((v) => !v);
                }}
              >
                {playing ? "Pause" : "Play"}
              </Button>
              <Button
                onClick={() => {
                  setTime(0);
                  setPlaying(false);
                }}
              >
                Rewind
              </Button>
            </div>
            <label>
              Callout text
              <Input
                value={callout}
                onChange={(e) => setCallout(e.target.value)}
              />
            </label>
            <Button
              onClick={() => {
                const key = {
                  time: Math.round(time * 100) / 100,
                  camera: captureCamera(),
                  transforms: structuredClone(scene.transforms),
                  hidden: [...scene.hidden],
                  callout,
                };
                editBoard({
                  keyframes: [
                    ...board.keyframes.filter((k) => k.time !== key.time),
                    key,
                  ].sort((a, b) => a.time - b.time),
                });
              }}
            >
              Capture keyframe at playhead
            </Button>
            {board.keyframes.map((key, i) => (
              <div className="visualization-toolbar" key={i}>
                <Button onClick={() => setTime(key.time)}>
                  {key.time.toFixed(2)}s · {key.callout || "Pose and camera"}
                </Button>
                {i > 0 && (
                  <Button
                    onClick={() =>
                      editBoard({
                        keyframes: board.keyframes.filter((_, j) => j !== i),
                      })
                    }
                  >
                    Remove keyframe
                  </Button>
                )}
              </div>
            ))}
            <label>
              Publication format
              <SelectControl
                value={movieFormat}
                onChange={(e) => setMovieFormat(e.target.value)}
              >
                <option value="png_sequence">
                  PNG image sequence + timing
                </option>
                <option value="gif">Animated GIF</option>
                {(caps.animation as string[] | undefined)?.includes("mp4") && (
                  <option value="mp4">MP4 H.264</option>
                )}
              </SelectControl>
            </label>
            <Button
              disabled={busy}
              variant="primary"
              onClick={() => void publish()}
            >
              Publish animation
            </Button>
            <p>
              Export uses endpoint poses and records FPS, dimensions, source
              revision and frame count. Local artifacts can be downloaded;
              hosted sharing is unavailable.
            </p>
          </>
        )}
      </details>
      <details>
        <summary>Reusable scene data</summary>
        <Button onClick={() => setAdvanced(JSON.stringify(data, null, 2))}>
          Load scene JSON
        </Button>
        <textarea
          aria-label="Visualization JSON"
          value={advanced}
          onChange={(e) => setAdvanced(e.target.value)}
        />
        <Button
          onClick={() =>
            void act(async () => {
              const candidate = JSON.parse(advanced);
              await props.saveDomain("visualization", candidate);
              setData(candidate);
              setStatus("Scene settings validated and saved");
            })
          }
        >
          Apply scene JSON
        </Button>
        <a
          download="visualization.json"
          href={`data:application/json;charset=utf-8,${encodeURIComponent(JSON.stringify(data, null, 2))}`}
        >
          Download settings
        </a>
      </details>
      <div role="status" aria-live="polite">
        {busy ? "Working…" : status}
      </div>
    </section>
  );
}
