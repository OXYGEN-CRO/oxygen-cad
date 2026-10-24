# Inspection and repair

Inspect current project/context before interpreting a selected face. Preserve feature IDs and requirements. Results have input keys/revision state; `last_build` is historical evidence. Rebuild current inputs before treating a measurement or rendered face signature as current.

For failed geometry read the exact operation/arguments and kernel result. Check units, profile closure/planarity, operation direction/depth, intersecting bodies, valid parameter ranges and feature order as relevant. Use a small disposable reproducer if needed. Repair source inputs, then remeasure the actual regenerated output; a changed exception or a visually similar render is not proof of a fix.

For missing/ambiguous face references, identify the intended functional region from current geometry and the original design/study intent. Repair explicitly. Never choose an arbitrary face index, widen a support region, remove a requirement or switch actor/engine to make checks pass.

A revision conflict means the document changed: re-inspect and reconcile the original task with current user edits. A lost reference after shape change means the old identity is no longer reliable. Keep these distinct from runtime/dependency errors; rebuilding containers is not a geometry repair.
