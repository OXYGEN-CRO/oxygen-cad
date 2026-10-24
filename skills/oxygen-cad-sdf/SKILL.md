---
name: oxygen-cad-sdf
description: Author SDFormat model/world files and check local frames, resources and supplied inertia before Gazebo/libsdformat handoff. Excludes signed-distance-field modelling.
---

# Simulator descriptions

## Runtime and input files

These are workflows over Oxygen exports or external tools. They do not add native studio commands. Use the selected project's current export; inspect its revision/report first. Commands below run from this skill directory with an interpreter containing the stated dependencies. Quote absolute paths. A standalone install carries `runtime.json` pointing to the application checkout; plugin skills live below that checkout's `skills/`. Never run a helper on an old export and label it current.

Use SDFormat XML for simulator/world descriptions. Select a version supported by the actual target consumer; do not assume the newest specification works everywhere. Keep a source ledger of frames, units, geometry, joints, sensor/plugin names and physical data. Read [frame and consumer checks](references/frame-consumer-checks.md) before transforms or plugin changes.

```sh
python3 scripts/simulator_check.py /absolute/model.sdf --gz-check auto
python3 scripts/simulator_check.py /absolute/model.sdf --gz-check required
```

The stdlib helper uses the bundled URDF helper for XML/numeric/inertia routines. It checks common direct model entity names, frame/attachment cycles, joints, axes, local mesh paths and supplied inertial tensors. It records nested/scoped/remote features as unverified; it is not a full SDFormat validator. With installed `gz`, auto invokes `gz sdf --check`; required refuses to pass when that tool is absent. External result state is reported separately. `ok` does not prove dynamics or plugin execution.

Write nontrivial `relative_to` and axis `expressed_in` deliberately. Pose reference and frame attachment serve different purposes. Use SI units and computed inertias, not converted numbers typed by eye. When starting from a known URDF, preserve its frame/geometry data and use the target conversion tool rather than independently guessing an equivalent robot.

Load in the target simulator when available; verify joint movement, collision geometry, gravity/contact response, sensors/plugins and resource resolution. Record exactly which checks ran. Rendering or lint does not execute the simulated hardware and does not establish Fusion simulation parity.
