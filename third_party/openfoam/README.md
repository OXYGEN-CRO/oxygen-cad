# Optional separate OpenCFD flow runtime

The MIT application invokes unmodified OpenCFD OpenFOAM `simpleFoam` and
`checkMesh` as separate GPL-3.0-or-later executables. This repository does not
contain their binaries. The original COPYING notice is retained here.

The pinned Debian binary/source identities are in
[`src/o3d/data/openfoam-laminar.json`](../../src/o3d/data/openfoam-laminar.json).
That contract names both the upstream corresponding source and Debian packaging
source, with download URLs and SHA-256 digests. Package copyright notices remain
in the extracted runtime's `usr/share/doc` tree. This distribution is distinct
from the OpenFOAM Foundation distribution and its version numbers.

Use `scripts/prepare_openfoam.py` with the exact packages listed in the contract;
it verifies each archive before extraction into a new isolated directory. It
writes a receipt binding libraries and native configuration. No system package
installation or shell startup file is required. See the simulation ledger for
current physics and validation limits. In particular, pressure-field spatial
accuracy is unresolved and all flow results remain inconclusive.
