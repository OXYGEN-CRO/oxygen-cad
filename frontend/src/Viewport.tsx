import { useEffect, useRef } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import {
  artifact,
  type Geometry,
  type Selection,
  type Project,
  type Face,
} from "./api";

type Props = {
  theme: "dark" | "light";
  geometry: Geometry | null;
  project: Project;
  selected: Selection | null;
  onSelect: (s: Selection) => void;
  section: boolean;
  field: "solid" | "stress" | "displacement";
  analysis: any;
  fit: number;
  deformation: number;
  measure: number[][];
  showStudy: boolean;
  isolate: boolean;
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
    const camera = new THREE.OrthographicCamera(-70, 70, 55, -55, 0.1, 10000);
    camera.up.set(0, 0, 1);
    camera.position.set(120, -150, 110);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.1;
    scene.add(new THREE.HemisphereLight(0xf2f2f2, 0x242424, 2.1));
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
      const previousAspect = camera.right / camera.top;
      const size =
        (camera.top * Math.min(previousAspect, 1)) /
        Math.max(Math.min(aspect, 1), 0.01);
      camera.top = size;
      camera.bottom = -size;
      camera.left = -size * aspect;
      camera.right = size * aspect;
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
      const hit = ray
        .intersectObjects(model.children, true)
        .find((h) => h.object.userData.face);
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
      framed: false,
    };
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
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
    r.model.traverse((o: THREE.Object3D) => {
      if (o instanceof THREE.Mesh || o instanceof THREE.LineSegments) {
        o.geometry.dispose();
        (Array.isArray(o.material) ? o.material : [o.material]).forEach((m) =>
          m.dispose(),
        );
      }
    });
    r.model.clear();
    const geometry = props.geometry;
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
    function addFace(face: Face, reference?: string) {
      const vertices = face.vertices.flat();
      const bg = new THREE.BufferGeometry();
      bg.setAttribute(
        "position",
        new THREE.Float32BufferAttribute(vertices, 3),
      );
      bg.setIndex(face.triangles.flat());
      bg.computeVertexNormals();
      const selected = props.selected?.id === face.id;
      const material = new THREE.MeshStandardMaterial({
        color: selected
          ? r.palette.selection
          : reference
            ? r.palette.reference
            : r.palette.part,
        metalness: reference ? 0.05 : 0.35,
        roughness: 0.4,
        transparent: !!reference,
        opacity: reference ? 0.3 : 1,
        side: THREE.DoubleSide,
        clippingPlanes: clip,
        depthWrite: !reference,
      });
      const mesh = new THREE.Mesh(bg, material);
      mesh.userData = { face, reference };
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
    geometry.faces.forEach((face) => addFace(face));
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
          const values = ids.map((id) =>
            props.field === "stress"
              ? (field.stress_mpa[id] ?? 0)
              : Math.hypot(...(field.displacements[id] ?? [0, 0, 0])),
          );
          const high = values.reduce((a, b) => Math.max(a, b), 1e-10);
          const color = new THREE.Color();
          for (const tri of field.boundary_triangles)
            for (const id of tri) {
              const point = field.nodes[id];
              const u = field.displacements[id] ?? [0, 0, 0];
              positions.push(
                ...point.map(
                  (v: number, i: number) => v + u[i] * props.deformation,
                ),
              );
              const value =
                props.field === "stress"
                  ? (field.stress_mpa[id] ?? 0)
                  : Math.hypot(...u);
              color.setHSL((1 - Math.min(value / high, 1)) * 0.65, 0.85, 0.52);
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
    props.deformation,
    props.measure,
    props.project.references,
    props.project.study,
    props.showStudy,
    props.isolate,
  ]);
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
    r.camera.position.copy(
      center.clone().add(new THREE.Vector3(1, -1.5, 1).multiplyScalar(d * 1.8)),
    );
    const aspect = host.current!.clientWidth / host.current!.clientHeight;
    r.camera.top = (d * 0.7) / Math.min(aspect, 1);
    r.camera.bottom = -r.camera.top;
    r.camera.left = -r.camera.top * aspect;
    r.camera.right = r.camera.top * aspect;
    r.camera.updateProjectionMatrix();
  }, [props.fit]);
  return (
    <div className="webgl" ref={host} aria-label="Interactive CAD viewport" />
  );
}
