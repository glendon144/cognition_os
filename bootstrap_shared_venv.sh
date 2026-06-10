#!/bin/sh
#
# bootstrap_shared_venv.sh — create/update the shared Cognition OS Python runtime

set -eu

COGUSER="${COGUSER:-gross}"
COGDIR="${COGDIR:-/home/${COGUSER}}"
PYTHON_BIN="${PYTHON_BIN:-${COGDIR}/pkg/bin/python3.10}"
VENV_DIR="${VENV_DIR:-${COGDIR}/venvs/cognition-os}"

CLONES_REQ="${COGDIR}/src/clones-brain/requirements.txt"
DREAM_REQ="${COGDIR}/src/dream_server/requirements.txt"
TORTOISED_REQ="${COGDIR}/src/tortoised/requirements.txt"
SHARED_REQ="${COGDIR}/src/cognition_os/requirements-shared-venv.txt"

if [ ! -x "${PYTHON_BIN}" ]; then
    echo "bootstrap_shared_venv: python not executable at ${PYTHON_BIN}" >&2
    exit 1
fi

mkdir -p "${COGDIR}/venvs"

if [ ! -x "${VENV_DIR}/bin/python" ]; then
    "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi

"${VENV_DIR}/bin/python" -m ensurepip --upgrade >/dev/null 2>&1 || true
"${VENV_DIR}/bin/python" -m pip install --upgrade pip setuptools wheel

if [ ! -f "${SHARED_REQ}" ]; then
    echo "bootstrap_shared_venv: missing shared requirements at ${SHARED_REQ}" >&2
    exit 1
fi

"${VENV_DIR}/bin/python" -m pip install -r "${SHARED_REQ}"

echo "bootstrap_shared_venv: ready at ${VENV_DIR}"
