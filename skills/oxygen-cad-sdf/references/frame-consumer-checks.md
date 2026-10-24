# SDFormat frame and consumer checks

Primary specification: https://sdformat.org/tutorials/specification/pose_frame_semantics/1.7/ and https://sdformat.org/spec.

Pose `relative_to` chooses the frame expressing an initial pose; frame `attached_to` chooses the body/frame attachment. They are different graphs. A joint's default pose reference differs from an ordinary link's model-relative pose. Use explicit nontrivial references and axis `expressed_in` after checking the target specification version. Nested scoped names need full libsdformat resolution; the local lint deliberately does not implement that resolver.

Keep simulator inputs in m/kg/s/rad with inertias in kg*m^2. Supply measured mass and geometry; target defaults and automatic inertia need explicit target evaluation. A plugin filename/sensor rate is not validated merely because its XML exists.

After local lint and `gz sdf --check`, load a disposable model/world in the chosen simulator version. Review resource resolution, collision meshes/contact, gravity/rest behavior, joint direction/limits, sensor streams and plugin startup. Capture which tests ran; do not label skipped tests passing. Rendering alone does not exercise plugins, dynamics or control.
