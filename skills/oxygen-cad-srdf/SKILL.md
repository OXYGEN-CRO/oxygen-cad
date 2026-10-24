---
name: oxygen-cad-srdf
description: Author MoveIt SRDF semantics and cross-check them against an explicit URDF, including planning groups, states, joints and collision exclusions.
---

# MoveIt planning semantics

## Runtime and input files

These are workflows over Oxygen exports or external tools. They do not add native studio commands. Use the selected project's current export; inspect its revision/report first. Commands below run from this skill directory with an interpreter containing the stated dependencies. Quote absolute paths. A standalone install carries `runtime.json` pointing to the application checkout; plugin skills live below that checkout's `skills/`. Never run a helper on an old export and label it current.

Start from a supplied, valid URDF; extract exact robot/link/joint names and kinematic paths before writing semantics. Keep SRDF external to Oxygen's typed CAD model. Read [planning evidence](references/planning-evidence.md) for group/collision decisions.

```sh
python3 scripts/planning_check.py /absolute/robot.srdf --urdf /absolute/robot.urdf
```

This stdlib helper depends on the packaged oxygen-cad-urdf helper; keep the full bundle installed. It uses an explicit URDF path rather than inferring a same-folder match. It checks robot-name agreement, known names, chain ancestry/subgroup cycles, scalar named-state limits, passive/virtual joint names, basic end-effector membership and Adjacent collision reasons. See its `coverage`/`unverified` fields: IK, sampled collisions, complete named states and full end-effector link separation are not established.

Define groups for the requested planning task from topology, then named states in radians/metres consistent with the URDF. Fixed/mimic joints are not independent scalar controls. Choose TCP/parent links from actual model data. Collision disables need adjacency or documented sampling/user evidence; never disable all pairs to make planning appear successful.

Use MoveIt Setup Assistant or the target configuration to validate the generated semantics and exercise actual IK/planning when installed. Report target loads, planning tests, skipped checks and any inferred choices. A structural pass is not evidence that a robot can execute a trajectory.
