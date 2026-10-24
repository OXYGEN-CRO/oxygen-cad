# Third-party components

The MIT license in this repository applies to original Oxygen CAD code. It does not replace the licenses of dependencies or imported projects.

| Component | Role | Upstream license/source |
|---|---|---|
| SolidCraft core and its truck patches | Vendored Rust geometry, sketch solver, parametric document and command engine | MIT OR Apache-2.0; original notices, attribution and embedded dependency licences in `third_party/solidcraft/`; pinned source/provenance in `UPSTREAM.json`; https://github.com/tangency-labs/solidcraft |
| Liberation Sans | Native sketch text fallback font | SIL OFL-1.1; `third_party/solidcraft/assets/fonts/liberation-sans/OFL.txt` |
| Tauri | Rust desktop window and launcher | MIT OR Apache-2.0; https://github.com/tauri-apps/tauri |
| build123d | Independent geometry verification and explicit OCCT compatibility engine | Apache-2.0 — https://github.com/gumyr/build123d |
| Open CASCADE Technology / OCP | Exact geometry kernel and bindings | LGPL-2.1 with OCCT exception / binding notices — https://github.com/Open-Cascade-SAS/OCCT and https://github.com/CadQuery/OCP |
| Netgen | Isolated tetrahedral mesher | LGPL-2.1 — https://github.com/NGSolve/netgen |
| CalculiX | External finite-element solver executable | GPL-2.0-or-later — https://www.dhondt.de/ and https://sources.debian.org/src/calculix-ccx/ |
| SPOOLES | Solver numerical dependency | Upstream terms included in Debian package — https://sources.debian.org/src/spooles/ |
| React, Three.js, Vite, FastAPI, Pydantic, Typer, Trimesh | UI and application/runtime libraries | See installed package license files and upstream projects |
| Geist Sans / Geist Mono | Locally served variable fonts | SIL Open Font License — `frontend/public/fonts/Geist-LICENSE.txt` and `Geist-Mono-LICENSE.txt`; https://fontsource.org/fonts/geist |

The Dockerfile downloads unmodified CalculiX 2.23 and SPOOLES binary packages from Debian and verifies their SHA-256 checksums. Their copyright/license notices are retained under `/opt/solver/usr/share/doc/`. Debian's corresponding source packages and upstream source are available through the links above. Other installed dependencies retain their package notices.

No proprietary CAD kernel, private research wiki, or private component catalog is included. Imported user geometry remains in the user's project directory and is not covered by the application license.
