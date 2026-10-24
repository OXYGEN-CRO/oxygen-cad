# Bambu application handoff

Check current vendor documentation and the installed app before relying on an interface:

- https://wiki.bambulab.com/en/software/bambu-connect
- https://wiki.bambulab.com/en/software/third-party-integration
- https://github.com/bambulab/BambuStudio

The reference integration uses an `import-file` URL with encoded absolute path, display name and version `1.0.0`. Treat this as a versioned app interface, not a printer API. If current official docs cannot be accessed, keep a prepared plan explicitly unverified and use the app's normal Open dialog when requested. Do not present reference-repo instructions as independently verified current vendor behavior.

Unsliced models go to Studio; only suitable printer-specific sliced files go to Connect. A file opener returning successfully does not prove the model loaded or a printer received it. On Windows verify the file association or use the actual installed Studio executable. On macOS verify the application name; Linux availability can differ between Studio and Connect.

The user checks printer, plate, nozzle, material/AMS mapping and starts the job in the app. Report observed state; never infer print completion or printer status from local files.
