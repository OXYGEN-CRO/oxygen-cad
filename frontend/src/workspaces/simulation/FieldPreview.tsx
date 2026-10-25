import { useEffect, useRef } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

export interface FieldData {
  field_state?: string;
  field_time_s?: number;
  nodes?: Record<string, number[]>;
  displacements_mm?: Record<string, number[]>;
  temperature_k?: Record<string, number>;
  von_mises_mpa?: Record<string, number>;
  element_density?: Record<string, number>;
  nodal_extrapolated_von_mises_mpa?: Record<string, number>;
  boundary_triangles?: number[][];
  element_connectivity?: Record<string, number[]>;
  modes?: {
    shape?: Record<string, number[]>;
    mass_normalized_displacement?: Record<string, number[]>;
  }[];
}

export default function FieldPreview({
  field,
  quantity,
  scale,
  mode = 0,
}: {
  field: FieldData;
  quantity: string;
  scale: number;
  mode?: number;
}) {
  const host = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!host.current || !field.nodes || !field.boundary_triangles?.length)
      return;
    const container = host.current;
    const scene = new THREE.Scene();
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    } catch {
      container.textContent =
        "WebGL field preview unavailable. Download the numerical field.";
      return;
    }
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    container.replaceChildren(renderer.domElement);
    const camera = new THREE.PerspectiveCamera(40, 1, 0.001, 100000);
    const controls = new OrbitControls(camera, renderer.domElement);
    const displacement =
      quantity === "mode"
        ? (field.modes?.[mode]?.mass_normalized_displacement ??
          field.modes?.[mode]?.shape)
        : field.displacements_mm;
    const values: Record<string, number> =
      quantity === "temperature"
        ? (field.temperature_k ?? {})
        : quantity === "stress"
          ? (field.nodal_extrapolated_von_mises_mpa ?? {})
          : Object.fromEntries(
              Object.entries(displacement ?? {}).map(([id, v]) => [
                id,
                Math.hypot(...v),
              ]),
            );
    const elementValues =
      quantity === "density"
        ? field.element_density
        : quantity === "stress"
          ? field.von_mises_mpa
          : undefined;
    const owner = new Map<string, string>();
    if (elementValues) {
      for (const [id, nodes] of Object.entries(
        field.element_connectivity ?? {},
      )) {
        for (const face of [
          [0, 1, 2],
          [0, 1, 3],
          [0, 2, 3],
          [1, 2, 3],
        ])
          owner.set(
            face
              .map((i) => nodes[i])
              .sort((a, b) => a - b)
              .join(","),
            id,
          );
      }
    }
    const scalarValues = elementValues
      ? Object.values(elementValues)
      : Object.values(values);
    const minimum = scalarValues.reduce((a, b) => Math.min(a, b), Infinity),
      maximum = scalarValues.reduce((a, b) => Math.max(a, b), 1e-12);
    const positions: number[] = [],
      colors: number[] = [];
    for (const triangle of field.boundary_triangles) {
      const elementValue =
        elementValues?.[
          owner.get([...triangle].sort((a, b) => a - b).join(",")) ?? ""
        ];
      for (const n of triangle) {
        const point = field.nodes[n],
          delta = displacement?.[n] ?? [0, 0, 0];
        if (!point) continue;
        positions.push(...point.map((x, i) => x + delta[i] * scale));
        const value =
          elementValues && elementValue !== undefined
            ? elementValue
            : (values[n] ?? 0);
        const color = new THREE.Color().setHSL(
          0.65 * (1 - (value - minimum) / Math.max(maximum - minimum, 1e-12)),
          0.7,
          0.5,
        );
        colors.push(color.r, color.g, color.b);
      }
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute(
      "position",
      new THREE.Float32BufferAttribute(positions, 3),
    );
    geometry.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
    geometry.computeVertexNormals();
    const material = new THREE.MeshStandardMaterial({
      vertexColors: true,
      side: THREE.DoubleSide,
      roughness: 0.7,
    });
    scene.add(new THREE.Mesh(geometry, material));
    const lines = new THREE.LineSegments(
      new THREE.EdgesGeometry(geometry, 25),
      new THREE.LineBasicMaterial({
        color: 0x888888,
        transparent: true,
        opacity: 0.3,
      }),
    );
    scene.add(lines, new THREE.HemisphereLight(0xffffff, 0x777777, 2));
    const bounds = new THREE.Box3().setFromBufferAttribute(
      geometry.getAttribute("position") as THREE.BufferAttribute,
    );
    const center = bounds.getCenter(new THREE.Vector3()),
      size = Math.max(bounds.getSize(new THREE.Vector3()).length(), 1);
    camera.position.copy(center).add(new THREE.Vector3(size, size * 0.7, size));
    camera.near = size / 10000;
    camera.far = size * 100;
    camera.updateProjectionMatrix();
    controls.target.copy(center);
    controls.update();
    const resize = () => {
      const width = container.clientWidth || 320;
      renderer.setSize(width, 230);
      camera.aspect = width / 230;
      camera.updateProjectionMatrix();
      renderer.render(scene, camera);
    };
    controls.addEventListener("change", resize);
    const observer = new ResizeObserver(resize);
    observer.observe(container);
    resize();
    return () => {
      observer.disconnect();
      controls.dispose();
      geometry.dispose();
      material.dispose();
      lines.geometry.dispose();
      (lines.material as THREE.Material).dispose();
      renderer.dispose();
      container.replaceChildren();
    };
  }, [field, quantity, scale, mode]);
  return (
    <div
      className="sim-field"
      ref={host}
      aria-label="Numerical finite element field preview"
    />
  );
}
