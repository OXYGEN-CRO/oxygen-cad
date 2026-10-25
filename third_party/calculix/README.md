# External CalculiX contact instrumentation

CalculiX 2.23 is a separate GPL-2-only program. This directory contains the
GPL-2-only read-only output addition and its licence. Original Oxygen application
code remains MIT licensed. Do not remove upstream notices from source bundles.

The builder downloads the [upstream source](https://www.dhondt.de/ccx_2.23.src.tar.bz2)
and requires SHA-256
`9c88385c10fb04f5dc6c4e98027a51bebdd8aee3920e05190d6c1dd08357d6e7`.
It accepts only the exact upstream `nonlingeo.c`, inserts the output header and
two accepted-output calls, and builds without changing numerical routines.
Calls run after `results()` and before its output buffers are freed. Failed
iterations/cutbacks are not exported as accepted states. Output is enabled only
by the child process flag `O3D_CCX_CONTACT_TELEMETRY=1`.

An existing GNU C/Fortran toolchain, GNU make, SPOOLES headers/shared library,
ARPACK, LAPACK and BLAS are required. The script installs no packages. Example
for an already provisioned Debian amd64 toolchain (paths must match your host):

```sh
python3 scripts/build_calculix.py --output /absolute/new/ccx-2.23-contact \
  --spooles-include /usr/include/spooles \
  --library /usr/lib/x86_64-linux-gnu/libspooles.so.2.2 \
  --library /usr/lib/x86_64-linux-gnu/libarpack.so.2 \
  --library /usr/lib/x86_64-linux-gnu/liblapack.so.3 \
  --library /usr/lib/x86_64-linux-gnu/libblas.so.3 --jobs 2
CCX_BINARY=/absolute/new/ccx-2.23-contact/ccx ./o3d doctor --json
```

`--source-archive` accepts an already downloaded file with the same required
hash. The output directory must be new. The complete release is published only
after a successful build, with these files:

- `ccx` and `build-manifest.json`: executable hash, source/patch/header identities,
  compiler versions, flags and exact linked numerical-library hashes.
- `ccx_2.23.src.tar.bz2`, `nonlingeo.c.patch`, `oxygen_contact_telemetry.h`,
  `build_calculix.py` and `COPYING`: corresponding source and build recipe.

The application recognizes only its pinned instrumentation contract. It verifies
the binary/receipt and checks the actual loaded libraries against the declared
numerical library identities before invocation. Keep those dependencies available
at the receipt's recorded locations; a changed runtime requires a fresh build.
Linux ELF library resolution uses `ldd`. Other source-built runtime platforms
remain unqualified. A stock external solver can still solve supported studies,
but it cannot establish integrated friction work from CONTACT PRINT/CELS alone.

`analysis.contact.jsonl` records accepted static face-contact states with
17-digit values and a 512 MiB output cap. It is evidence, not a numerical
algorithm or a stability certificate. Independent geometry, traction, spring
energy, plastic-slip work and energy balance checks are described in
[the simulation ledger](../../docs/SIMULATION.md#experimental-quadratic-separation-and-friction).
All contact studies remain inconclusive pending tangent stability qualification.
Numerical tests do not establish measured hardware accuracy.

The Docker build uses the same recipe and bundles the source distribution and
SPOOLES notice. Its native amd64 numerical path is checked independently of the
ARM64 package/hash preparation; an ARM64 build requires its own numerical run.
