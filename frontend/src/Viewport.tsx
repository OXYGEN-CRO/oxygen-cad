import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import {
  artifact,
  type Geometry,
  type Selection,
  type Project,
  type Face,
  type DisplayField,
} from "./api";
import {
  cameraEvent,
  nativeFaces,
  previewEvent,
  type Asset,
  type NativeScene,
  type Scene,
  type CameraState,
} from "./workspaces/visualization/scene";

export type ViewOrientation = "isometric" | "front" | "top" | "right";

type Props = {
  viewRequest: { name: ViewOrientation; version: number };
  theme: "dark" | "light";
  geometry: Geometry | null;
  project: Project;
  selected: Selection | null;
  onSelect: (s: Selection) => void;
  section: boolean;
  field: DisplayField;
  modeIndex: number;
  analysis: any;
  fit: number;
  deformation: number;
  measure: number[][];
  showStudy: boolean;
  isolate: boolean;
  nativeScene?: NativeScene | null;
};

function readPalette() {
  const computed = getComputedStyle(document.documentElement);
  const color = (token: string) => computed.getPropertyValue(token).trim();
  return {
    background: color("--viewport-bg"),
    part: color("--viewport-part"),
    edge: color("--viewport-edge"),
    reference: color("--viewport-reference"),
    referenceEdge: color("--viewport-reference-edge"),
    selection: color("--viewport-selection"),
    selectionEdge: color("--viewport-selection-edge"),
  };
}

export function Viewport(props: Props) {
  const [visualization, setVisualization] = useState<{
    scene: Scene;
    geometry: NativeScene | null;
    assets: Record<string, Asset>;
    callout?: string;
  } | null>(null);
  const host = useRef<HTMLDivElement>(null);
  const latest = useRef(props);
  latest.current = props;
  const runtime = useRef<any>(null);
  useEffect(() => {
    const element = host.current!;
    const palette = readPalette();
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(palette.background);
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    renderer.localClippingEnabled = true;
    renderer.setClearColor(palette.background);
    element.appendChild(renderer.domElement);
    let camera: THREE.OrthographicCamera | THREE.PerspectiveCamera =
      new THREE.OrthographicCamera(-70, 70, 55, -55, 0.1, 10000);
    camera.up.set(0, 0, 1);
    camera.position.set(120, -150, 110);
    const controls = new OrbitControls<
      THREE.OrthographicCamera | THREE.PerspectiveCamera
    >(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.1;
    const hemisphere = new THREE.HemisphereLight(0xf2f2f2, 0x242424, 2.1);
    scene.add(hemisphere);
    const light = new THREE.DirectionalLight(0xffffff, 2.6);
    light.position.set(60, -80, 160);
    scene.add(light);
    const rim = new THREE.DirectionalLight(0xffffff, 0.65);
    rim.position.set(-80, 60, 90);
    scene.add(rim);
    const grid = new THREE.GridHelper(
      500,
      50,
      getComputedStyle(document.documentElement)
        .getPropertyValue("--viewport-grid-major")
        .trim(),
      getComputedStyle(document.documentElement)
        .getPropertyValue("--viewport-grid")
        .trim(),
    );
    grid.material.transparent = true;
    grid.material.opacity = 0.4;
    grid.rotation.x = Math.PI / 2;
    grid.position.z = -0.1;
    scene.add(grid);
    const axes = new THREE.AxesHelper(14);
    axes.setColors(0x666666, 0x929292, 0xbcbcbc);
    scene.add(axes);
    const model = new THREE.Group();
    scene.add(model);
    const resize = () => {
      const w = element.clientWidth,
        h = element.clientHeight;
      renderer.setSize(w, h);
      const aspect = w / Math.max(h, 1);
      if (camera instanceof THREE.OrthographicCamera) {
        const previousAspect = camera.right / camera.top;
        const size =
          (camera.top * Math.min(previousAspect, 1)) /
          Math.max(Math.min(aspect, 1), 0.01);
        camera.top = size;
        camera.bottom = -size;
        camera.left = -size * aspect;
        camera.right = size * aspect;
      } else camera.aspect = aspect;
      camera.updateProjectionMatrix();
      // Resizing clears the drawing buffer; paint the model before the next frame.
      renderer.render(scene, camera);
    };
    const observer = new ResizeObserver(resize);
    observer.observe(element);
    resize();
    let down = [0, 0];
    const ray = new THREE.Raycaster();
    const pointerDown = (e: PointerEvent) => {
      down = [e.clientX, e.clientY];
    };
    const pointerUp = (e: PointerEvent) => {
      if (Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 4) return;
      const rect = renderer.domElement.getBoundingClientRect();
      ray.setFromCamera(
        new THREE.Vector2(
          ((e.clientX - rect.left) / rect.width) * 2 - 1,
          (-(e.clientY - rect.top) / rect.height) * 2 + 1,
        ),
        camera,
      );
      const hit = ray.intersectObjects(model.children, true).find((h) => {
        if (!h.object.visible || !h.object.userData.face) return false;
        const material = (h.object as THREE.Mesh)
          .material as THREE.MeshStandardMaterial;
        return !(material.clippingPlanes ?? []).some(
          (plane) => plane.distanceToPoint(h.point) < 0,
        );
      });
      if (hit) {
        const info = hit.object.userData;
        latest.current.onSelect({
          id: info.face.id,
          kind: info.reference ? "reference" : "face",
          point: hit.point.toArray(),
          face: info.face,
          reference: info.reference,
        });
      }
    };
    renderer.domElement.addEventListener("pointerdown", pointerDown);
    renderer.domElement.addEventListener("pointerup", pointerUp);
    const clock = new THREE.Clock();
    let frame = 0;
    function animate() {
      frame = requestAnimationFrame(animate);
      controls.update(clock.getDelta());
      renderer.render(scene, camera);
    }
    animate();
    runtime.current = {
      scene,
      renderer,
      camera,
      controls,
      model,
      grid,
      axes,
      palette,
      hemisphere,
      defaultLights: [light, rim],
      visualLights: [] as THREE.Light[],
      setCamera: (snapshot: CameraState) => {
        const aspect = element.clientWidth / Math.max(element.clientHeight, 1);
        const next = snapshot.fov
          ? new THREE.PerspectiveCamera(
              (snapshot.fov * 180) / Math.PI,
              aspect,
              0.001,
              1e9,
            )
          : new THREE.OrthographicCamera(
              -snapshot.distance * 0.4 * aspect,
              snapshot.distance * 0.4 * aspect,
              snapshot.distance * 0.4,
              -snapshot.distance * 0.4,
              0.001,
              1e9,
            );
        const back = new THREE.Vector3(
          -Math.sin(snapshot.yaw) * Math.cos(snapshot.pitch),
          -Math.cos(snapshot.yaw) * Math.cos(snapshot.pitch),
          Math.sin(snapshot.pitch),
        );
        const right = new THREE.Vector3(
          Math.cos(snapshot.yaw),
          -Math.sin(snapshot.yaw),
          0,
        );
        const up = back.clone().cross(right);
        next.up.copy(
          up
            .multiplyScalar(Math.cos(snapshot.roll))
            .addScaledVector(right, -Math.sin(snapshot.roll)),
        );
        next.position
          .fromArray(snapshot.target)
          .addScaledVector(back, snapshot.distance);
        controls.target.fromArray(snapshot.target);
        next.lookAt(controls.target);
        camera = next;
        controls.object = next;
        controls.update();
        runtime.current.camera = next;
      },
      framed: false,
    };
    const receive = (event: Event) =>
      setVisualization((event as CustomEvent).detail);
    const capture = () => {
      const delta = camera.position.clone().sub(controls.target);
      const distance = delta.length();
      const yaw = Math.atan2(-delta.x, -delta.y);
      const pitch = Math.asin(
        Math.max(-1, Math.min(1, delta.z / Math.max(distance, 1e-9))),
      );
      const idealRight = new THREE.Vector3(Math.cos(yaw), -Math.sin(yaw), 0);
      const idealUp = delta.clone().normalize().cross(idealRight);
      const actualUp = new THREE.Vector3(0, 1, 0).applyQuaternion(
        camera.quaternion,
      );
      const snapshot = {
        target: controls.target.toArray(),
        yaw,
        pitch,
        roll: Math.atan2(-actualUp.dot(idealRight), actualUp.dot(idealUp)),
        distance:
          camera instanceof THREE.OrthographicCamera
            ? camera.top / (0.4 * camera.zoom)
            : distance,
        fov:
          camera instanceof THREE.PerspectiveCamera
            ? (camera.fov * Math.PI) / 180
            : 0,
      };
      window.dispatchEvent(new CustomEvent(cameraEvent, { detail: snapshot }));
    };
    const fitVisible = () => {
      const bounds = new THREE.Box3();
      model.children
        .filter((o) => o.visible)
        .forEach((o) => bounds.expandByObject(o));
      if (bounds.isEmpty()) return;
      const center = bounds.getCenter(new THREE.Vector3());
      const diameter = bounds.getSize(new THREE.Vector3()).length();
      const delta = camera.position.clone().sub(controls.target).normalize();
      controls.target.copy(center);
      camera.position.copy(center).addScaledVector(delta, diameter * 2);
      if (camera instanceof THREE.OrthographicCamera) {
        const aspect = element.clientWidth / Math.max(element.clientHeight, 1);
        camera.top = (diameter * 0.65) / Math.min(aspect, 1);
        camera.bottom = -camera.top;
        camera.left = -camera.top * aspect;
        camera.right = camera.top * aspect;
        camera.zoom = 1;
      }
      camera.updateProjectionMatrix();
      controls.update();
      capture();
    };
    window.addEventListener(previewEvent, receive);
    window.addEventListener("o3d:visualization-capture", capture);
    window.addEventListener("o3d:visualization-fit", fitVisible);
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      window.removeEventListener(previewEvent, receive);
      window.removeEventListener("o3d:visualization-capture", capture);
      window.removeEventListener("o3d:visualization-fit", fitVisible);
      controls.dispose();
      model.traverse((o) => {
        if (o instanceof THREE.Mesh || o instanceof THREE.LineSegments) {
          o.geometry.dispose();
          const materials = Array.isArray(o.material)
            ? o.material
            : [o.material];
          materials.forEach((m) => m.dispose());
        }
      });
      renderer.dispose();
      renderer.domElement.remove();
      runtime.current = null;
    };
  }, []);

  useEffect(() => {
    const r = runtime.current;
    if (!r) return;
    r.palette = readPalette();
    r.scene.background.set(r.palette.background);
    r.renderer.setClearColor(r.palette.background);
    const computed = getComputedStyle(document.documentElement);
    const nextGrid = new THREE.GridHelper(
      500,
      50,
      computed.getPropertyValue("--viewport-grid-major").trim(),
      computed.getPropertyValue("--viewport-grid").trim(),
    );
    r.grid.geometry.dispose();
    r.grid.geometry = nextGrid.geometry;
    nextGrid.material.dispose();
    r.renderer.render(r.scene, r.camera);
  }, [props.theme]);

  useEffect(() => {
    const r = runtime.current;
    if (!r) return;
    let disposed = false;
    host.current!.dataset.fieldReady = "false";
    delete host.current!.dataset.fieldArtifact;
    r.model.traverse((o: THREE.Object3D) => {
      if (o instanceof THREE.Mesh || o instanceof THREE.LineSegments) {
        o.geometry.dispose();
        (Array.isArray(o.material) ? o.material : [o.material]).forEach((m) =>
          m.dispose(),
        );
      }
    });
    r.model.clear();
    const nativeBodies =
      props.field === "solid"
        ? (
            visualization?.geometry ??
            props.nativeScene ??
            (
              props.geometry as
                (Geometry & { native_scene?: NativeScene }) | null
            )?.native_scene ??
            ((props.project as Project & { native_document?: unknown })
              .native_document
              ? (props.geometry as (Geometry & { scene?: NativeScene }) | null)
                  ?.scene
              : undefined)
          )?.bodies
        : undefined;
    const nativePoints =
      nativeBodies?.flatMap(
        (b) =>
          b.mesh?.positions ?? b.mesh?.faces?.flatMap((f) => f.vertices) ?? [],
      ) ?? [];
    host.current!.dataset.visualBodies = String(nativeBodies?.length ?? 1);
    if (nativeBodies?.length === 0) {
      host.current!.dataset.fieldReady = "true";
      r.renderer.render(r.scene, r.camera);
      return;
    }
    const geometry = nativePoints.length
      ? {
          bounds: [0, 1].map((side) =>
            [0, 1, 2].map((axis) =>
              nativePoints.reduce(
                (v, p) => (side ? Math.max(v, p[axis]) : Math.min(v, p[axis])),
                side ? -Infinity : Infinity,
              ),
            ),
          ),
          faces: props.geometry?.faces ?? [],
          references: props.geometry?.references ?? [],
        }
      : props.geometry;
    if (!geometry) return;
    const min = new THREE.Vector3(
      ...(geometry.bounds[0] as [number, number, number]),
    );
    const max = new THREE.Vector3(
      ...(geometry.bounds[1] as [number, number, number]),
    );
    const center = min.clone().add(max).multiplyScalar(0.5);
    const diameter = max.clone().sub(min).length();
    if (!r.framed) {
      r.controls.target.copy(center);
      r.camera.position.copy(
        center
          .clone()
          .add(new THREE.Vector3(1, -1.5, 1).multiplyScalar(diameter * 1.8)),
      );
      const aspect = host.current!.clientWidth / host.current!.clientHeight;
      r.camera.top = (diameter * 0.7) / Math.min(aspect, 1);
      r.camera.bottom = -r.camera.top;
      r.camera.left = -r.camera.top * aspect;
      r.camera.right = r.camera.top * aspect;
      r.camera.updateProjectionMatrix();
      r.framed = true;
    }
    r.grid.position.z = min.z - 0.05;
    const clip = props.section
      ? [new THREE.Plane(new THREE.Vector3(-1, 0, 0), center.x)]
      : [];
    if (visualization)
      for (const plane of visualization.scene.sections)
        if (plane.enabled)
          clip.push(
            new THREE.Plane(
              new THREE.Vector3(...(plane.normal as [number, number, number]))
                .normalize()
                .negate(),
              plane.offset_mm,
            ),
          );
    function addFace(face: Face, reference?: string, bodyId?: string) {
      const visual = visualization?.scene;
      const target = bodyId ?? reference ?? "part";
      if (
        visual &&
        (visual.hidden.includes(target) ||
          visual.hidden.includes(face.id) ||
          (visual.isolated.length &&
            !visual.isolated.includes(target) &&
            !visual.isolated.includes(face.id)))
      )
        return;
      const vertices = face.vertices.flat();
      const bg = new THREE.BufferGeometry();
      bg.setAttribute(
        "position",
        new THREE.Float32BufferAttribute(vertices, 3),
      );
      bg.setIndex(face.triangles.flat());
      bg.computeVertexNormals();
      const selected = props.selected?.id === face.id;
      const look =
        visual?.appearances[face.id] ??
        visual?.appearances[target] ??
        (!reference ? visual?.appearances["*"] : undefined);
      let texture: THREE.Texture | null = null;
      if (look?.texture) {
        const mapping = look.texture;
        const uv = face.vertices.flatMap((p) => {
          const point = p.map((v, i) => v - mapping.origin[i]);
          let u: number, v: number;
          if (mapping.projection === "cylindrical_z") {
            u =
              ((Math.atan2(point[1], point[0]) + mapping.rotation) /
                (2 * Math.PI) +
                0.5) %
              1;
            v = point[2] / mapping.size_mm[1] + 0.5;
          } else {
            const axes = { xy: [0, 1], yz: [1, 2], zx: [2, 0] }[
              mapping.projection
            ];
            const a = point[axes[0]],
              b = point[axes[1]];
            u =
              (a * Math.cos(mapping.rotation) +
                b * Math.sin(mapping.rotation)) /
                mapping.size_mm[0] +
              0.5;
            v =
              (-a * Math.sin(mapping.rotation) +
                b * Math.cos(mapping.rotation)) /
                mapping.size_mm[1] +
              0.5;
          }
          return [u, v];
        });
        bg.setAttribute("uv", new THREE.Float32BufferAttribute(uv, 2));
        texture = new THREE.TextureLoader().load(
          artifact(`visualization-assets/${mapping.asset}.png`),
        );
        texture.colorSpace = THREE.SRGBColorSpace;
        texture.wrapS = texture.wrapT = mapping.repeat
          ? THREE.RepeatWrapping
          : THREE.ClampToEdgeWrapping;
      }
      const material = new THREE.MeshStandardMaterial({
        color: selected
          ? r.palette.selection
          : reference
            ? r.palette.reference
            : (look?.color ?? r.palette.part),
        metalness: look?.metallic ?? (reference ? 0.05 : 0.35),
        roughness: look?.roughness ?? 0.4,
        transparent: !!reference || (look?.opacity ?? 1) < 1 || !!texture,
        opacity: look?.opacity ?? (reference ? 0.3 : 1),
        map: texture,
        wireframe: visual?.style === "wireframe",
        side: THREE.DoubleSide,
        clippingPlanes: clip,
        depthWrite: !reference,
      });
      if (look?.texture) {
        const mapping = look.texture;
        material.onBeforeCompile = (shader) => {
          shader.uniforms.decalOpacity = { value: mapping.opacity };
          shader.fragmentShader =
            "uniform float decalOpacity;\n" +
            shader.fragmentShader.replace(
              "#include <map_fragment>",
              `#ifdef USE_MAP
            vec4 decalColor=texture2D(map,vMapUv);
            float decalMask=${mapping.repeat ? "1.0" : "step(0.0,vMapUv.x)*step(vMapUv.x,1.0)*step(0.0,vMapUv.y)*step(vMapUv.y,1.0)"};
            diffuseColor.rgb=mix(diffuseColor.rgb,decalColor.rgb,decalColor.a*decalOpacity*decalMask);
            #endif`,
            );
        };
        material.customProgramCacheKey = () =>
          `decal-${mapping.repeat}-${mapping.opacity}`;
      }
      const mesh = new THREE.Mesh(bg, material);
      mesh.userData = { face, reference, bodyId: target };
      if (visual?.transforms[target])
        mesh.position.fromArray(visual.transforms[target]);
      r.model.add(mesh);
      const edges = new THREE.LineSegments(
        new THREE.EdgesGeometry(bg, 25),
        new THREE.LineBasicMaterial({
          color: selected
            ? r.palette.selectionEdge
            : reference
              ? r.palette.referenceEdge
              : r.palette.edge,
          transparent: true,
          opacity: reference ? 0.3 : 0.65,
          clippingPlanes: clip,
        }),
      );
      edges.visible = !visual || visual.style === "shaded_edges";
      if (visual?.transforms[target])
        edges.position.fromArray(visual.transforms[target]);
      r.model.add(edges);
    }
    geometry.references
      .filter(
        (ref) =>
          !props.isolate &&
          props.project.references.find((v) => v.id === ref.id)?.visible !==
            false,
      )
      .forEach((ref) => ref.faces.forEach((face) => addFace(face, ref.id)));
    const beforeNative = new Set(r.model.children);
    if (nativeBodies)
      nativeBodies
        .filter((b) => b.visible !== false)
        .forEach((body) =>
          nativeFaces(body).forEach((face) =>
            addFace(face, undefined, body.id),
          ),
        );
    else geometry.faces.forEach((face) => addFace(face));
    host.current!.dataset.visualBodies = String(nativeBodies?.length ?? 1);
    const nativeObjects = r.model.children.filter(
      (object: THREE.Object3D) => !beforeNative.has(object),
    );
    if (props.field === "solid" || !props.analysis) {
      host.current!.dataset.fieldReady = "true";
    } else
      fetch(artifact(props.analysis.field_artifact))
        .then((res) => {
          if (!res.ok) throw new Error("Field unavailable");
          return res.json();
        })
        .then((field) => {
          if (disposed) return;
          const positions: number[] = [],
            colors: number[] = [];
          const ids = Object.keys(field.nodes).map(Number);
          const vectors =
            props.field === "mode"
              ? (field.modes?.[props.modeIndex]?.vectors ?? {})
              : (field.displacements ?? {});
          const values = ids.map((id) =>
            props.field === "stress"
              ? (field.stress_mpa?.[id] ?? 0)
              : props.field === "plastic_strain"
                ? field.equivalent_plastic_strain[id]
                : props.field === "temperature"
                  ? field.temperature_c[id]
                  : Math.hypot(...(vectors[id] ?? [0, 0, 0])),
          );
          const low =
            props.field === "temperature"
              ? values.reduce((a, b) => Math.min(a, b), Infinity)
              : 0;
          const high = values.reduce((a, b) => Math.max(a, b), -Infinity);
          const span = Math.max(high - low, 1e-10);
          const dimensions = geometry.bounds[1].map(
            (value, axis) => value - geometry.bounds[0][axis],
          );
          const modeScale =
            props.field === "mode"
              ? (Math.max(...dimensions) * 0.1) /
                Math.max(high, 1e-10)
              : 1;
          const color = new THREE.Color();
          for (const tri of field.boundary_triangles)
            for (const id of tri) {
              const point = field.nodes[id];
              const u = vectors[id] ?? [0, 0, 0];
              positions.push(
                ...point.map(
                  (v: number, i: number) =>
                    v + u[i] * props.deformation * modeScale,
                ),
              );
              const value =
                props.field === "stress"
                  ? (field.stress_mpa?.[id] ?? 0)
                  : props.field === "plastic_strain"
                    ? field.equivalent_plastic_strain[id]
                    : props.field === "temperature"
                      ? field.temperature_c[id]
                      : Math.hypot(...u);
              color.setHSL(
                (1 - Math.min(Math.max((value - low) / span, 0), 1)) * 0.65,
                0.85,
                0.52,
              );
              colors.push(color.r, color.g, color.b);
            }
          const bg = new THREE.BufferGeometry();
          bg.setAttribute(
            "position",
            new THREE.Float32BufferAttribute(positions, 3),
          );
          bg.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
          bg.computeVertexNormals();
          for (const object of nativeObjects) {
            r.model.remove(object);
            object.geometry.dispose();
            (Array.isArray(object.material)
              ? object.material
              : [object.material]
            ).forEach((material: THREE.Material) => material.dispose());
          }
          r.model.add(
            new THREE.Mesh(
              bg,
              new THREE.MeshStandardMaterial({
                vertexColors: true,
                side: THREE.DoubleSide,
                roughness: 0.7,
                clippingPlanes: clip,
              }),
            ),
          );
          r.renderer.render(r.scene, r.camera);
          host.current!.dataset.fieldReady = "true";
          host.current!.dataset.fieldArtifact = props.analysis.field_artifact;
        })
        .catch(() => {
          if (!disposed) host.current!.dataset.fieldReady = "error";
        });
    if (props.showStudy) {
      for (const selection of props.project.study.fixed_faces) {
        const face = geometry.faces.find((face) => face.id === selection);
        if (!face) continue;
        const bg = new THREE.BufferGeometry();
        bg.setAttribute(
          "position",
          new THREE.Float32BufferAttribute(face.vertices.flat(), 3),
        );
        bg.setIndex(face.triangles.flat());
        const marker = new THREE.Mesh(
          bg,
          new THREE.MeshBasicMaterial({
            color: "#949494",
            transparent: true,
            opacity: 0.3,
            side: THREE.DoubleSide,
            depthTest: false,
            depthWrite: false,
          }),
        );
        marker.renderOrder = 3;
        r.model.add(marker);
      }
      for (const load of props.project.study.forces) {
        const face = geometry.faces.find((face) => face.id === load.face);
        const direction = new THREE.Vector3(
          ...(load.vector_n as [number, number, number]),
        );
        if (!face || direction.length() === 0) continue;
        const arrow = new THREE.ArrowHelper(
          direction.normalize(),
          new THREE.Vector3(...(face.center as [number, number, number])),
          Math.max(8, diameter * 0.2),
          "#b0b0b0",
        );
        r.model.add(arrow);
      }
    }
    if (props.measure.length === 2) {
      const bg = new THREE.BufferGeometry().setFromPoints(
        props.measure.map(
          (p) => new THREE.Vector3(...(p as [number, number, number])),
        ),
      );
      r.model.add(
        new THREE.Line(
          bg,
          new THREE.LineBasicMaterial({
            color: r.palette.selectionEdge,
            depthTest: false,
          }),
        ),
      );
    }
    return () => {
      disposed = true;
    };
  }, [
    props.theme,
    props.geometry,
    props.selected,
    props.section,
    props.field,
    props.analysis,
    props.modeIndex,
    props.deformation,
    props.measure,
    props.project.references,
    props.project.study,
    props.showStudy,
    props.isolate,
    visualization,
    props.nativeScene,
  ]);
  useEffect(() => {
    const r = runtime.current;
    if (!r) return;
    r.visualLights.forEach((light: THREE.Light) => r.scene.remove(light));
    r.visualLights = [];
    if (visualization) {
      const visual = visualization.scene;
      r.scene.background = new THREE.Color(visual.background);
      r.defaultLights.forEach((light: THREE.Light) => (light.visible = false));
      r.hemisphere.intensity = visual.ambient * 2;
      r.grid.visible = false;
      r.axes.visible = false;
      r.renderer.toneMappingExposure = 2 ** visual.exposure_ev;
      for (const source of visual.lights) {
        let light: THREE.DirectionalLight | THREE.PointLight | THREE.SpotLight;
        if (source.kind === "directional")
          light = new THREE.DirectionalLight(
            source.color,
            source.intensity * 2,
          );
        else if (source.kind === "spot")
          light = new THREE.SpotLight(
            source.color,
            source.intensity * source.reference_distance_mm ** 2,
            0,
            (source.cone_degrees * Math.PI) / 360,
            0,
            2,
          );
        else
          light = new THREE.PointLight(
            source.color,
            source.intensity * source.reference_distance_mm ** 2,
            0,
            2,
          );
        light.position.fromArray(source.vector);
        if (source.kind === "directional")
          light.position.applyAxisAngle(
            new THREE.Vector3(0, 0, 1),
            visual.environment_rotation,
          );
        if (light instanceof THREE.SpotLight) {
          light.target.position.fromArray(source.target);
          r.scene.add(light.target);
          r.visualLights.push(light.target);
        }
        r.scene.add(light);
        r.visualLights.push(light);
      }
      r.setCamera(visual.camera);
      host.current!.dataset.projection = visual.camera.fov
        ? "perspective"
        : "orthographic";
      host.current!.dataset.visualStyle = visual.style;
    } else {
      r.scene.background = new THREE.Color(r.palette.background);
      r.defaultLights.forEach((light: THREE.Light) => (light.visible = true));
      r.hemisphere.intensity = 2.1;
      r.grid.visible = true;
      r.axes.visible = true;
      r.renderer.toneMappingExposure = 1;
    }
    r.renderer.render(r.scene, r.camera);
  }, [visualization]);
  useEffect(() => {
    const r = runtime.current;
    if (!r || !props.geometry) return;
    const bounds = props.geometry.bounds;
    const center = new THREE.Vector3(...(bounds[0] as [number, number, number]))
      .add(new THREE.Vector3(...(bounds[1] as [number, number, number])))
      .multiplyScalar(0.5);
    const d = new THREE.Vector3(...(bounds[1] as [number, number, number]))
      .sub(new THREE.Vector3(...(bounds[0] as [number, number, number])))
      .length();
    r.controls.target.copy(center);
    const directions = {
      isometric: new THREE.Vector3(1, -1.5, 1),
      front: new THREE.Vector3(0, -1, 0),
      top: new THREE.Vector3(0, -0.00001, 1),
      right: new THREE.Vector3(1, 0, 0),
    };
    r.camera.position.copy(center).add(
      directions[props.viewRequest.name]
        .clone()
        .normalize()
        .multiplyScalar(d * 3),
    );
    // Clear residual orbit damping so a chosen standard view lands precisely.
    r.camera.up.set(0, 0, 1);
    r.controls.enableDamping = false;
    r.controls.update();
    r.controls.enableDamping = true;
    const aspect = host.current!.clientWidth / host.current!.clientHeight;
    if (r.camera instanceof THREE.OrthographicCamera) {
      r.camera.top = (d * 0.7) / Math.min(aspect, 1);
      r.camera.bottom = -r.camera.top;
      r.camera.left = -r.camera.top * aspect;
      r.camera.right = r.camera.top * aspect;
    }
    r.camera.zoom = 1;
    r.camera.updateProjectionMatrix();
  }, [props.fit, props.viewRequest]);
  return (
    <>
      <div className="webgl" ref={host} aria-label="Interactive CAD viewport" />
      {visualization?.callout && (
        <div
          className="visualization-callout"
          style={{
            position: "absolute",
            bottom: 24,
            left: "25%",
            right: "25%",
            textAlign: "center",
            padding: 12,
            background: "var(--surface-glass)",
            color: "var(--text)",
            borderRadius: "var(--radius-card)",
            pointerEvents: "none",
          }}
        >
          {visualization.callout}
        </div>
      )}
    </>
  );
}
