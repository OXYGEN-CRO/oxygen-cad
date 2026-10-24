FROM node:22-bookworm-slim AS frontend
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-trixie
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OMP_NUM_THREADS=2 O3D_MESH_PYTHON=/opt/mesh/bin/python O3D_FRONTEND_DIST=/app/frontend/dist
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates libglu1-mesa libgl1 libarpack2t64 libopenmpi40 liblapack3 libgfortran5 && rm -rf /var/lib/apt/lists/*
# Unmodified Debian solver packages: exact binary checksums, isolated from application code.
RUN curl -fsSL https://deb.debian.org/debian/pool/main/c/calculix-ccx/calculix-ccx_2.23-1_amd64.deb -o /tmp/ccx.deb \
 && echo '709b2d994699168f2754bd16117d51cef3241e268a7dfab185b8f78c98bd02aa  /tmp/ccx.deb' | sha256sum -c - \
 && curl -fsSL https://deb.debian.org/debian/pool/main/s/spooles/libspooles2.2t64_2.2-16+b1_amd64.deb -o /tmp/spooles.deb \
 && echo 'e82c4173706d831a490439bf5b31dae5194dca07482ac8632bc219514372ac3c  /tmp/spooles.deb' | sha256sum -c - \
 && dpkg-deb -x /tmp/ccx.deb /opt/solver && dpkg-deb -x /tmp/spooles.deb /opt/solver && rm /tmp/*.deb
ENV CCX_BINARY=/opt/solver/usr/bin/ccx LD_LIBRARY_PATH=/opt/solver/usr/lib/x86_64-linux-gnu
WORKDIR /app
COPY requirements.lock mesh-requirements.lock pyproject.toml ./
RUN python -m venv /opt/cad && /opt/cad/bin/pip install --no-cache-dir -r requirements.lock \
 && python -m venv /opt/mesh && /opt/mesh/bin/pip install --no-cache-dir -r mesh-requirements.lock
COPY src/ src/
RUN /opt/cad/bin/pip install --no-deps -e .
COPY examples/ examples/
COPY tests/ tests/
COPY --from=frontend /web/dist frontend/dist/
ENV PATH=/opt/cad/bin:$PATH O3D_PROJECT_DIR=/projects/demo
EXPOSE 8787
CMD ["python", "-m", "o3d.cli", "studio", "--host", "0.0.0.0"]
