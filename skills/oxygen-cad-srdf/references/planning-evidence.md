# Planning decisions and evidence

Reference: https://moveit.picknik.ai/main/doc/examples/urdf_srdf/urdf_srdf_tutorial.html.

Extract exact names and parent-child paths from the selected URDF. Define a group by the actual task's chain or deliberate joint/link/subgroup collection. A TCP link is a real model entity, not the last name that looks like a gripper. Keep end-effector and parent group link membership separate; the local helper does not exhaustively prove that separation.

Named states use radians for angular joints and metres for prismatic joints, with the actual joint limits and mimic/passive constraints. Document partial states explicitly; a partial valid state is not a complete home configuration.

Collision exclusions need evidence: adjacency from the link graph, a user-provided reason, or actual MoveIt Setup Assistant sampling under documented coverage. Static separation at one pose is insufficient. Store the evidence and sampling scope beside any disabled pair. Do not claim collision-free planning without target tests.

Run consumer setup/load and representative IK/path requests when available, and record failures as failures. SRDF does not define geometry, mass, joint origins or control drivers.
