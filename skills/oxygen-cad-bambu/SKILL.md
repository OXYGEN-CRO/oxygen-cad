---
name: oxygen-cad-bambu
description: Hand Oxygen CAD models or sliced files to Bambu Studio/Connect for user review. Builds an application handoff without starting or controlling a printer.
---

# Bambu

## Runtime and input files

These are workflows over Oxygen exports or external tools. They do not add native studio commands. Use the selected project's current export; inspect its revision/report first. Commands below run from this skill directory with an interpreter containing the stated dependencies. Quote absolute paths. A standalone install carries `runtime.json` pointing to the application checkout; plugin skills live below that checkout's `skills/`. Never run a helper on an old export and label it current.

Use this only for a requested Bambu application handoff. Keep machine operation in the vendor application. An unsliced STEP/STL/3MF goes to Studio; a printer-specific `.gcode.3mf` or supported `.gcode` can go to Connect where the installed vendor version supports it. Read [vendor handoff](references/vendor-handoff.md) before opening files.

```sh
python3 scripts/handoff.py /absolute/part.stl --app studio
python3 scripts/handoff.py /absolute/job.gcode.3mf --app connect
```

The stdlib helper prints a platform-specific plan. `--open` launches that plan when the user requested opening it; the helper never starts a print. Connect uses a percent-encoded `bambu-connect://import-file` URL with a local file path and version. Platform availability and the installed application's import behavior must be checked against current vendor docs; Linux uses Studio in this helper. On Windows the Studio plan depends on the configured file association, which must point at Studio or be replaced with an explicit executable.

A successful opener exit is reported as `opener_returned_success`, not file loaded, printer contacted or print started. Observe the application to confirm import when tools are available. If not, report the limitation and provide the file path. Do not request printer access codes or change firmware/modes as part of a file handoff.

The user selects printer, plate, nozzle, material/AMS mapping and starts the job in the app. If the task was only handoff preparation, return the plan without opening an app. This skill does not implement printer telemetry, cloud login or autonomous machine operation.
