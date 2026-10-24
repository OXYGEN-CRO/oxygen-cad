# Robot frames and computed physics

Authoritative consumer references: https://docs.ros.org/en/rolling/Tutorials/Intermediate/URDF/URDF-Main.html and https://moveit.picknik.ai/main/doc/examples/urdf_srdf/urdf_srdf_tutorial.html. Verify the target ROS/consumer version.

A joint origin places its child link frame relative to the parent. Joint axis is expressed in the joint frame. Visual, collision and inertial origins are local offsets within a link. Record transforms as actual source matrices or measurements with units; do not turn viewer coordinates into a new frame by intuition.

Oxygen length is mm. URDF link/joint translations and mesh dimensions are metres. Mass is kg; inertia is kg*m^2 about the centre of mass and expressed in the inertial frame. Converting mm^2 inertia values into m^2 uses a factor of 1e-6, independently of mass conversion. Do not apply a mesh scale twice.

Compute primitive inertia rather than typing guessed tensors. A box with full side lengths x,y,z metres has diagonal mass*(y*y+z*z)/12, mass*(x*x+z*z)/12, mass*(x*x+y*y)/12. Use measured mass/geometry for actual links. Rotate tensors with R I R^T and apply a parallel-axis shift only to the correct frame/reference point; maintain the original COM provenance. The lint checks supplied tensors' physical matrix conditions but cannot establish that they match the real part.

A useful ledger names root and link frames, joint type/axis/sign/range, CAD-to-link transform, asset units/scale, mass/inertia provenance, target consumers and unresolved assumptions. Include it beside the XML or as a comment without replacing canonical model data. Visual and collision geometry can differ deliberately; verify each against its intended role.
