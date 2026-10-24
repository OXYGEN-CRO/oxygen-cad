# OrcaSlicer inputs and handoff

Official CLI reference: https://www.orcaslicer.com/wiki/cli/cli_mode and https://www.orcaslicer.com/wiki/cli/cli_actions. Inspect the installed binary's help before using flags; the helper follows the documented load-settings/load-filaments/slice/export contract.

Select existing printer, process and filament profiles from the user's installation. Resolve inheritance with the complete applicable library; do not create a machine profile from a model name. Check nozzle/bed/plate, filament/material temperatures, printer compatibility and any process compatibility lists. The local resolver merges parent keys but does not interpret every Orca compatibility expression or system preset bundle variant.

A plan is only argv; `--run` must really execute a slicer before a generated slice can be claimed. Fresh output directories distinguish new artifacts from stale successful files. Open a toolpath preview and review warnings/settings. Do not automatically send, start or monitor a print as part of slicing.
