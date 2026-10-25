FROM node:22-bookworm-slim AS frontend
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
COPY examples/ /examples/
RUN npm run build

FROM rust:1.95-slim-trixie AS engine
WORKDIR /build
COPY engine/Cargo.toml engine/Cargo.lock engine/
COPY engine/src/ engine/src/
COPY third_party/solidcraft/ third_party/solidcraft/
RUN cargo build --release --locked --manifest-path engine/Cargo.toml --jobs 2

FROM python:3.12-slim-trixie AS contact-solver
RUN apt-get update && apt-get install -y --no-install-recommends gcc gfortran make libc6-dev curl ca-certificates libarpack2t64 liblapack3 libblas3 libopenmpi40 \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY scripts/build_calculix.py scripts/build_calculix.py
COPY third_party/calculix/ third_party/calculix/
# Pinned SPOOLES ABI for the target architecture. Only the native amd64 build is
# qualified locally; arm64 source/build support still requires numerical CI.
RUN set -eu; ccx_arch="$(dpkg --print-architecture)"; \
 case "$ccx_arch" in \
 amd64) ccx_dev_sha=e7a35399668318eb3deab23280b92ab763500e052b0f3b691123cbff4211e0e1; ccx_lib_sha=e82c4173706d831a490439bf5b31dae5194dca07482ac8632bc219514372ac3c ;; \
 arm64) ccx_dev_sha=04e824bebe86b68a6f80bfe6837f441b4bca965d40f4ebd553bee8a39d158066; ccx_lib_sha=f23799a895887bdb8d375b5e6f82cf72ae2246faff1edb00ba181ca5999bb819 ;; \
 *) echo "Unqualified CalculiX build architecture: $ccx_arch" >&2; exit 1 ;; \
 esac; \
 curl -fsSL "https://deb.debian.org/debian/pool/main/s/spooles/libspooles-dev_2.2-16+b1_${ccx_arch}.deb" -o /tmp/spooles-dev.deb; \
 curl -fsSL "https://deb.debian.org/debian/pool/main/s/spooles/libspooles2.2t64_2.2-16+b1_${ccx_arch}.deb" -o /tmp/spooles.deb; \
 echo "$ccx_dev_sha  /tmp/spooles-dev.deb" | sha256sum -c -; \
 echo "$ccx_lib_sha  /tmp/spooles.deb" | sha256sum -c -; \
 dpkg-deb -x /tmp/spooles-dev.deb /opt/solver; dpkg-deb -x /tmp/spooles.deb /opt/solver; \
 ccx_triplet="$(gcc -dumpmachine)"; mkdir -p /opt/solver/lib; \
 cp "/opt/solver/usr/lib/$ccx_triplet/libspooles.so.2.2" /opt/solver/lib/; \
 python scripts/build_calculix.py --output /opt/contact-solver \
   --spooles-include /opt/solver/usr/include/spooles \
   --library /opt/solver/lib/libspooles.so.2.2 \
   --library "/usr/lib/$ccx_triplet/libarpack.so.2" \
   --library "/usr/lib/$ccx_triplet/liblapack.so.3" \
   --library "/usr/lib/$ccx_triplet/libblas.so.3" --jobs 2 --retain-verification-archive

COPY scripts/reference_contact_kernel.py scripts/contact_kernel_probe.f90 scripts/native_tangent_limits.c scripts/
RUN python scripts/reference_contact_kernel.py --source-archive /opt/contact-solver/ccx_2.23.src.tar.bz2 --output /opt/contact-kernel
COPY scripts/reference_bulk_kernel.py scripts/bulk_kernel_probe.f90 scripts/material_kernel_probe.f90 scripts/
RUN python scripts/reference_bulk_kernel.py --calculix-build /opt/contact-solver --output /opt/bulk-kernel
RUN mkdir /opt/native-tangent-probe \
 && gcc -O2 -I third_party/calculix scripts/native_tangent_limits.c -lm -o /opt/native-tangent-probe/probe \
 && cp scripts/native_tangent_limits.c third_party/calculix/oxygen_contact_telemetry.h third_party/calculix/oxygen_native_tangent.h third_party/calculix/oxygen_accepted_bulk.h third_party/calculix/COPYING /opt/native-tangent-probe/ \
 && cd /opt/native-tangent-probe \
 && sha256sum probe native_tangent_limits.c oxygen_contact_telemetry.h oxygen_native_tangent.h oxygen_accepted_bulk.h COPYING > SHA256SUMS

FROM python:3.12-slim-trixie
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OMP_NUM_THREADS=2 O3D_MESH_PYTHON=/opt/mesh/bin/python O3D_FRONTEND_DIST=/app/frontend/dist
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates libglu1-mesa libgl1 libxrender1 libxcursor1 libxfixes3 libxext6 libxft2 libxinerama1 libx11-6 libarpack2t64 libopenmpi40 liblapack3 libgfortran5 ngspice kicad fonts-freefont-ttf cura-engine python3-opencamlib \
 && cd /tmp && apt-get download cura \
 && dpkg-deb -x cura_*.deb /tmp/cura-profile \
 && mkdir -p /usr/share/cura/resources/definitions /usr/share/doc/oxygen-cura-profile \
 && cp /tmp/cura-profile/usr/share/cura/resources/definitions/fdmprinter.def.json /usr/share/cura/resources/definitions/ \
 && cp /tmp/cura-profile/usr/share/cura/resources/definitions/fdmextruder.def.json /usr/share/cura/resources/definitions/ \
 && cp /tmp/cura-profile/usr/share/doc/cura/copyright /usr/share/doc/oxygen-cura-profile/ \
 && rm -rf /tmp/cura-profile /tmp/cura_*.deb /var/lib/apt/lists/*
ENV O3D_DRAWING_FONT=/usr/share/fonts/truetype/freefont/FreeSerif.ttf \
    O3D_CURA_ENGINE=/usr/bin/CuraEngine \
    O3D_CURA_DEFINITIONS=/usr/share/cura/resources/definitions \
    O3D_CAM_PYTHON=/usr/bin/python3
COPY --from=contact-solver /opt/contact-solver /opt/contact-solver
COPY --from=contact-solver /opt/solver/lib /opt/solver/lib
COPY --from=contact-solver /opt/solver/usr/share/doc/libspooles2.2t64/copyright /usr/share/doc/oxygen-spooles/copyright
COPY --from=contact-solver /opt/contact-kernel /usr/share/oxygen-cad-verification/contact-kernel
COPY --from=contact-solver /opt/bulk-kernel /usr/share/oxygen-cad-verification/bulk-kernel
COPY --from=contact-solver /opt/native-tangent-probe /usr/share/oxygen-cad-verification/native-tangent
ENV CCX_BINARY=/opt/contact-solver/ccx LD_LIBRARY_PATH=/opt/solver/lib
ENV O3D_CONTACT_KERNEL_EVIDENCE=/usr/share/oxygen-cad-verification/contact-kernel/result.json
ENV O3D_BULK_KERNEL_EVIDENCE=/usr/share/oxygen-cad-verification/bulk-kernel/result.json
ENV O3D_NATIVE_TANGENT_PROBE=/usr/share/oxygen-cad-verification/native-tangent/probe
COPY --from=engine /build/engine/target/release/oxygen-engine /usr/local/bin/oxygen-engine
COPY third_party/solidcraft/LICENSE-MIT third_party/solidcraft/LICENSE-APACHE third_party/solidcraft/NOTICE third_party/solidcraft/THIRD-PARTY-LICENSES.txt /usr/share/doc/oxygen-solidcraft/
COPY third_party/solidcraft/assets/fonts/liberation-sans/OFL.txt /usr/share/doc/oxygen-solidcraft/OFL.txt
WORKDIR /app
COPY requirements.lock mesh-requirements.lock skill-requirements.lock pyproject.toml ./
RUN python -m venv /opt/cad && /opt/cad/bin/pip install --no-cache-dir -r requirements.lock -r skill-requirements.lock \
 && python -m venv /opt/mesh && /opt/mesh/bin/pip install --no-cache-dir -r mesh-requirements.lock \
 && /opt/mesh/bin/python -c 'import gmsh; gmsh.initialize(["oxygen-headless", "-nopopup"]); gmsh.finalize()'
COPY src/ src/
RUN /opt/cad/bin/pip install --no-deps -e .
COPY examples/ examples/
COPY o3d ./
COPY scripts/agent_skills.py scripts/qualify_mesh.py scripts/reference_fem.py scripts/reference_thermoelastic.py scripts/reference_buckling.py scripts/reference_bonded.py scripts/reference_contact_kernel.py scripts/reference_contact_global.py scripts/reference_bulk_kernel.py scripts/bulk_kernel_probe.f90 scripts/material_kernel_probe.f90 scripts/contact_kernel_probe.f90 scripts/native_tangent_limits.c scripts/
COPY verification-requirements.lock ./
COPY skills/ skills/
COPY .claude-plugin/ .claude-plugin/
COPY .codex-plugin/ .codex-plugin/
COPY tests/ tests/
COPY --from=frontend /web/dist frontend/dist/
ENV PATH=/opt/cad/bin:$PATH O3D_PROJECT_DIR=/projects/demo
EXPOSE 8787
CMD ["python", "-m", "o3d.cli", "studio", "--host", "0.0.0.0"]
