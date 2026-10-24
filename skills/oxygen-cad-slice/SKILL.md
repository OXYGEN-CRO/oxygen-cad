---
name: oxygen-cad-slice
description: Resolve OrcaSlicer preset inheritance and slice an Oxygen STL with explicit printer, process and filament settings. Does not contact printers.
---

# Slice

## Runtime and input files

These are workflows over Oxygen exports or external tools. They do not add native studio commands. Use the selected project's current export; inspect its revision/report first. Commands below run from this skill directory with an interpreter containing the stated dependencies. Quote absolute paths. A standalone install carries `runtime.json` pointing to the application checkout; plugin skills live below that checkout's `skills/`. Never run a helper on an old export and label it current.

Use a current exported STL and the user's actual printer/nozzle, process and filament profiles. Read [slicing inputs](references/slicing-inputs.md). The helper is stdlib-only; OrcaSlicer is an external dependency. Locate its installed executable and inspect that version's `--help` before using the command contract.

```sh
python3 scripts/slicer.py resolve /absolute/profile.json --profiles /absolute/profile-library --out /absolute/resolved.json
```

Repeat for printer/process/filament. The resolver merges explicit `inherits` chains and refuses missing/ambiguous/cyclic parents. It does not guess a profile library, normalize arbitrary preset variants or validate process/printer compatibility. Keep profile files local; only output paths/names are printed.

```sh
python3 scripts/slicer.py slice /absolute/part.stl --slicer /absolute/orca-slicer --printer /absolute/printer.json --process /absolute/process.json --filament /absolute/filament.json --out /absolute/new-slice-directory
```

This prints an argv plan. Add `--run` only for the requested slicing action after verifying profiles and CLI flags. It uses a fresh output directory and requires a successful process plus nonempty G-code, either standalone or inside the generated 3MF. Merely naming an unsliced project `.gcode.3mf` does not pass this check. It never supplies a printer host or upload command. The plan is not evidence that a slice completed.

Inspect generated metadata, dimensions/placement against bed limits, nozzle/material temperatures, plate and compatibility, warnings and toolpath preview in the actual slicer. It does not infer a printer or convert STEP itself. If profiles/tools are absent, report the missing inputs and provide the STL for manual slicing. Return the artifact, settings/provenance and checks actually performed; slicing is separate from printing.
