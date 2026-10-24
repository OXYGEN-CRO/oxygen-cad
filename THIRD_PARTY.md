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
| Gmsh | Optional isolated CAD-aware mesher | GPL-2.0-or-later with upstream linking exception; installed package notices retained — https://gmsh.info/; distributed unmodified in the mesher environment, original application code remains separately licensed |
| CalculiX | External finite-element solver executable | GPL-2.0-or-later — https://www.dhondt.de/ and https://sources.debian.org/src/calculix-ccx/ |
| SPOOLES | Solver numerical dependency | Upstream terms included in Debian package — https://sources.debian.org/src/spooles/ |
| KiCad | External board checking and fabrication CLI | GPL and component-specific terms; installed Debian copyright notices retained — https://www.kicad.org/ and https://sources.debian.org/src/kicad/ |
| ngspice | External circuit simulation process | BSD-style core and accompanying component terms; installed notices retained — https://ngspice.sourceforge.io/ and https://sources.debian.org/src/ngspice/ |
| CuraEngine and Cura base definitions | External FFF slicer and its unmodified printer/extruder definitions | AGPL-3.0-or-later engine; profile-package notices copied into the runtime — https://github.com/Ultimaker/CuraEngine and https://sources.debian.org/src/cura/ |
| OpenCAMLib | Isolated ball-cutter surface sampling | LGPL-2.1-or-later; installed Debian notices retained — https://github.com/aewallin/opencamlib |
| GNU FreeFont | Unicode drawing publication font | GPL-3.0-or-later with font exception; package notices retained — https://savannah.gnu.org/projects/freefont/ |
| NumPy, SciPy, Shapely/GEOS, ezdxf, NetworkX, FontTools | Numerical, polygon, mesh and vector-publication dependencies | See installed upstream package notices; GEOS retains its separate LGPL terms |
| pypdf and Gerbonara | Independent verification readers, installed only for checks | BSD-3-Clause and Apache-2.0 respectively; verification lock and upstream notices retained — https://github.com/py-pdf/pypdf and https://git.jaseg.de/gerbonara.git/ |
| Radix Primitives | Accessible dropdown menu foundation | MIT — https://github.com/radix-ui/primitives; installed package notices retained |
| React, Three.js, Vite, FastAPI, Pydantic, Typer, Trimesh | UI and application/runtime libraries | See installed package license files and upstream projects |
| ReportLab | Standalone engineering-sheet PDF renderer | BSD-style license; installed package license retained; https://www.reportlab.com/opensource/ |
| charset-normalizer | ReportLab text dependency | MIT; installed package notices retained; https://github.com/jawah/charset_normalizer |
| Geist Sans / Geist Mono | Locally served variable fonts | SIL Open Font License — `frontend/public/fonts/Geist-LICENSE.txt` and `Geist-Mono-LICENSE.txt`; https://fontsource.org/fonts/geist |

The Dockerfile downloads unmodified CalculiX 2.23 and SPOOLES binary packages from Debian and verifies their SHA-256 checksums. Their copyright/license notices are retained under `/opt/solver/usr/share/doc/`. Debian's corresponding source packages and upstream source are available through the links above. Other installed dependencies retain their package notices.

No proprietary CAD kernel, private research wiki, or private component catalog is included. Imported user geometry remains in the user's project directory and is not covered by the application license.
