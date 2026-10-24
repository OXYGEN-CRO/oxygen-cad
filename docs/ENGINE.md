# Rust geometry engine

Oxygen's React studio and JSON project remain the authoring/review interface. The default geometry backend is a source-built Rust engine in `engine/`, linked to the headless SolidCraft core vendored under `third_party/solidcraft/`. It includes the sketch constraint solver, parametric document, command registry and modified truck geometry crates. The upstream desktop and browser UI are excluded.

The pinned source revision is `0520910bfd4ff6d24c850f4a20a604f6128f87bf`. `UPSTREAM.json` records provenance, selected files, hashes and the minimal workspace-manifest adjustment. Existing notices and third-party/font licences are retained. Our wrapper and application remain MIT; vendored code retains its upstream terms.

## Build and inspect

```sh
cargo build --release --locked --manifest-path engine/Cargo.toml
O3D_NATIVE=1 ./o3d engine commands --json
O3D_NATIVE=1 ./o3d engine describe solid.shell
O3D_NATIVE=1 ./o3d doctor --json
```

Rust 1.95 is pinned in the Docker builder; Cargo.lock pins dependencies. `O3D_ENGINE_BINARY` overrides the executable for a native development installation. Runtime builds use the local source, not a downloaded SolidCraft CLI. The raw `o3d engine stdio` command is a persistent JSON-lines session for trusted local tooling.

## Projects and manual edits

Existing typed profiles, extrusions, holes/arrays, fillets and chamfers are translated to native commands. Coordinate frames, signed extrusion, placements and semantic/strict edge selections preserve the existing project conventions. Rust performs the geometry operations. OCCT imports the resulting STEP independently, checks validity/positive single-solid volume, compares volume within 0.1%, and generates review topology and mesh artifacts. The bounded Netgen/CalculiX workflow consumes the verified geometry.

A native feature can express additional operations directly:

```json
{
  "name": "Native enclosure",
  "geometry_engine": "solidcraft",
  "features": [
    {"id":"block","name":"Block","type":"native","command":"solid.box","params":{"length":30,"width":20,"height":10}},
    {"id":"shell","name":"Shell","type":"native","command":"solid.shell","params":{"faces":[[15,10,10]],"thickness":2}}
  ]
}
```

Apply the project through the existing revision-checked `o3d apply` flow. Selecting Shell in the studio opens its command and JSON arguments; edits use the same transaction service, and nested `$parameter` bindings retain user locks. Saved native features have a pinned modelling-command allowlist and reject filesystem/font/script access. Raw local engine sessions have a broader trusted surface.

Successful builds retain the original native STEP, a `.solidcraft` design, a replayable engine-command script and independent build measurements. Exports include the native design/script alongside the authoritative `o3d.json`, STEP/STL and engineering report. The native design is an interchange snapshot; `o3d.json` remains the shared editing source of truth.

`geometry_engine: "occt"` explicitly selects the previous compiler for compatibility. Native-command features require Rust. Missing/failed Rust execution never silently switches kernels. Engine identity and the compiler version marker participate in geometry/result cache keys.

## Scope and performance

The upstream core has a much broader command set than the original mounting-part editor. This integration adds a shared native-command surface; it does not establish that all upstream commands, topology changes or assemblies are validated. Projects and structural studies still require a single connected solid. Geometry runs in Rust; file exchange, independent checks and Python orchestration remain, so no end-to-end speedup is inferred from the language alone.
