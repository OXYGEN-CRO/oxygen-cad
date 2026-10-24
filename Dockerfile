FROM node:22-bookworm-slim AS frontend
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM rust:1.95-slim-trixie AS engine
WORKDIR /build
COPY engine/Cargo.toml engine/Cargo.lock engine/
COPY engine/src/ engine/src/
COPY third_party/solidcraft/ third_party/solidcraft/
RUN cargo build --release --locked --manifest-path engine/Cargo.toml --jobs 2

FROM python:3.12-slim-trixie
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OMP_NUM_THREADS=2 O3D_MESH_PYTHON=/opt/mesh/bin/python O3D_FRONTEND_DIST=/app/frontend/dist
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates libglu1-mesa libgl1 libarpack2t64 libopenmpi40 liblapack3 libgfortran5 ngspice kicad fonts-freefont-ttf cura-engine python3-opencamlib \
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
# Unmodified Debian solver packages: exact binary checksums, isolated from application code.
RUN curl -fsSL https://deb.debian.org/debian/pool/main/c/calculix-ccx/calculix-ccx_2.23-1_amd64.deb -o /tmp/ccx.deb \
 && echo '709b2d994699168f2754bd16117d51cef3241e268a7dfab185b8f78c98bd02aa  /tmp/ccx.deb' | sha256sum -c - \
 && curl -fsSL https://deb.debian.org/debian/pool/main/s/spooles/libspooles2.2t64_2.2-16+b1_amd64.deb -o /tmp/spooles.deb \
 && echo 'e82c4173706d831a490439bf5b31dae5194dca07482ac8632bc219514372ac3c  /tmp/spooles.deb' | sha256sum -c - \
 && dpkg-deb -x /tmp/ccx.deb /opt/solver && dpkg-deb -x /tmp/spooles.deb /opt/solver && rm /tmp/*.deb
ENV CCX_BINARY=/opt/solver/usr/bin/ccx LD_LIBRARY_PATH=/opt/solver/usr/lib/x86_64-linux-gnu
COPY --from=engine /build/engine/target/release/oxygen-engine /usr/local/bin/oxygen-engine
COPY third_party/solidcraft/LICENSE-MIT third_party/solidcraft/LICENSE-APACHE third_party/solidcraft/NOTICE third_party/solidcraft/THIRD-PARTY-LICENSES.txt /usr/share/doc/oxygen-solidcraft/
COPY third_party/solidcraft/assets/fonts/liberation-sans/OFL.txt /usr/share/doc/oxygen-solidcraft/OFL.txt
WORKDIR /app
COPY requirements.lock mesh-requirements.lock skill-requirements.lock pyproject.toml ./
RUN python -m venv /opt/cad && /opt/cad/bin/pip install --no-cache-dir -r requirements.lock -r skill-requirements.lock \
 && python -m venv /opt/mesh && /opt/mesh/bin/pip install --no-cache-dir -r mesh-requirements.lock
COPY src/ src/
RUN /opt/cad/bin/pip install --no-deps -e .
COPY examples/ examples/
COPY o3d ./
COPY scripts/agent_skills.py scripts/
COPY skills/ skills/
COPY .claude-plugin/ .claude-plugin/
COPY .codex-plugin/ .codex-plugin/
COPY tests/ tests/
COPY --from=frontend /web/dist frontend/dist/
ENV PATH=/opt/cad/bin:$PATH O3D_PROJECT_DIR=/projects/demo
EXPOSE 8787
CMD ["python", "-m", "o3d.cli", "studio", "--host", "0.0.0.0"]
