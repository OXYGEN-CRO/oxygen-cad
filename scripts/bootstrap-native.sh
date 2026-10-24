#!/bin/sh
# Linux developer setup. Partner installs use the pinned Docker runtime.
set -eu
cd "$(dirname "$0")/.."
python3 -m venv .venv
.venv/bin/pip install -e '.[test,skills]'
python3 -m venv .mesh-venv
.mesh-venv/bin/pip install -r mesh-requirements.lock
cd frontend
npm ci
npm run build
cd ..
if [ "${O3D_INSTALL_AGENT_SKILLS:-1}" != "0" ]; then
    python3 scripts/agent_skills.py install --agent portable --json
fi
