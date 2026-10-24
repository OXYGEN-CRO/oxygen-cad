# Simulation references

`axial-reference.json` is a complete disposable project with a matching 10 × 2 × 2 mm CAD rod and two explicit C3D4 meshes. The material is a **synthetic numerical reference**, not a recommended engineering material. It defines E = 200000 MPa, ν = 0.3 and a 100 N axial traction. Symmetry-plane transverse supports allow Poisson contraction. The analytical axial extension is 0.00125 mm, von Mises stress is 25 MPa, and strain energy is 0.0625 N·mm.

Open a copy in an isolated project directory. The Simulation workspace uses `Project.domains.simulation`; its solve operation runs all cases and retains exact inputs, fields, raw workers and numerical checks. Changes to geometry, mesh, material, boundaries, algorithm sources or solver binary invalidate the corresponding input signature. Old artifacts remain inspectable.

The source fixture uses the legacy OCCT geometry path so that its body ID is `part`. Native SolidCraft projects bind analysis models to the scene's persistent body IDs and exact per-body STEP files. Do not change a native body reference to `part` to silence a mapping error.

The automated cases in `tests/test_simulation_domain.py` also exercise conduction/convection/radiation, thermal expansion, axial modes, buckling refinement, plastic loading/unloading through CalculiX, transient energy/timestep behavior, gapped frictionless contact, bolt preload, filtered density optimization and deliberately invalid inputs. Their provenance is synthetic/analytical; none supplies physical test correlation.
