---
name: oxygen-cad-urdf
description: Author or check URDF robot descriptions alongside Oxygen CAD exports, with frame, joint, mesh-unit and inertial evidence. Includes limited local lint.
---

# Robot descriptions

## Runtime and input files

These are workflows over Oxygen exports or external tools. They do not add native studio commands. Use the selected project's current export; inspect its revision/report first. Commands below run from this skill directory with an interpreter containing the stated dependencies. Quote absolute paths. A standalone install carries `runtime.json` pointing to the application checkout; plugin skills live below that checkout's `skills/`. Never run a helper on an old export and label it current.

URDF is an external robot-description artifact, not an Oxygen native project or arbitrary Python feature. Use XML as the maintained description. Before editing record a compact design ledger: target consumer, link frames, joint origins/axes/limits, mesh units/scale, masses/centres/inertias and provenance. Read [frames and inertias](references/frames-and-inertias.md) for the unit boundary and computed quantities.

Use supplied kinematics and measured transforms; do not derive axes or joint signs from appearance alone. Prepare one visual/collision asset per link in its local frame. Oxygen exports millimetres; URDF uses metres, kg and radians. A mesh scale of 0.001 is appropriate only when the actual asset is millimetre-scaled. Physical links need sourced mass/inertia; frame-only links may omit them.

```sh
python3 scripts/robot_check.py /absolute/robot.urdf --package robot_description=/absolute/package
```

The stdlib helper checks tree/name integrity, common joint/limit fields, finite origins, local mesh references and supplied mass/inertial conditions. It reports warnings separately and `--strict` makes them nonpassing. This is limited lint; it does not expand xacro, validate every extension, locate meshes in unknown ROS packages, prove spatial placement or run dynamics. `ok` only means the covered checks passed. Review `unverified` and do not label the model consumer-validated.

Run installed `check_urdf` on the expanded file and load it in the actual RViz/simulator target when available. Sweep each moving joint, check axis/sign/limits, collision meshes and link origin alignment. Preserve missing-tool and skipped-motion checks in the handoff. No robot viewer or motion controller is added to the Oxygen studio by this skill.
