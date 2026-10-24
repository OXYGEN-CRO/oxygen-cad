# Project and artifact ownership

Keep the authoritative o3d.json and managed history in the selected project, imported immutable assets under its asset store, and full proposals separate from live source. Keep a concise provenance note beside externally acquired source inputs. Avoid source/output feedback loops and manually modifying `.o3d/` to pretend an artifact is fresh.

Drawings, DXF sections, robot XML, manufacturing reports and slices are separate deliverables. Name source export/revision/hash, transform/units, relevant tool version and source requirements so they can be rebuilt when the design changes. Preserve a maintained source script/XML when the external workflow uses one.

A current standard release ZIP carries the native part/export report but does not automatically carry every supplier reference asset or new external deliverable. For an editable project handoff include original source assets and their usage terms deliberately. Do not commit private projects or downloaded assets into the public application repo as part of a user design task.

A STEP solid, printable mesh, reviewed drawing, robot description, slice and manufactured part have distinct acceptance criteria. Report which artifacts and checks actually exist. Files from external helpers are not added to the studio feature graph merely by placing them near o3d.json.
