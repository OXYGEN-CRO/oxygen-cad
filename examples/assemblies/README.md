# Slider mechanism

`slider.json` is a source document for a grounded base and a sliding carriage. Copy it to a new disposable project's `o3d.json`; do not overwrite an existing project. The canonical SolidCraft document contains the two component definitions, stable occurrence IDs, face-attached slider, ±30 mm travel limits, contact set, 100-step study, part properties and saved exploded view.

In the Assembly workspace:

1. Select the Base and Carriage occurrences. Inspect the Rail joint and its face attachments.
2. Drive Rail from 10 toward −30 mm. The enabled contact set stops the carriage before it passes through the base, even though the requested endpoint is clear.
3. Move away, disable contact, and compare the unrestricted travel.
4. Play Travel, export poses or produce the duration-scaled kinematics report. The report is retained with native state provenance; later native edits mark it stale.
5. Show Service, then restore the assembled display. All-pairs fit reports use engineering placements rather than exploded display offsets.
6. Reconcile the BOM, edit a definition's part number, export CSV and reopen the project.

Motion units are millimeters and degrees. Studies solve positions and finite-difference kinematics; they do not simulate force, preload, friction, impact or material compliance. Tolerance stacks use explicitly entered dimensional intervals and acceptance limits. No standard fit class or thread strength is inferred.
