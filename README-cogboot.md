# Cognition OS — NetBSD rc.d Service Scripts

These rc.d scripts implement the Cognition OS boot model: a NetBSD VM that
clones its cognitive services from blues.clonesvr.com at boot and starts
them automatically, presenting /health endpoints before the system is
considered ready.

## Architecture

    cogboot         — clones/updates all service repositories from blues
        └── licd          — Local Inference Controller Daemon (port 8777)
        └── tortoised     — inference service (port 8767)
        └── clones_brain  — local SQLite/FastAPI memory (port 8765)
        └── dream_server  — Dream capsule storage DCCP/RFC-1976 (port 8181)

## Prerequisites

- Python 3.10 installed via pkgsrc (lang/python310)
- SSH key at ~/.ssh/id_ed25519 with access to blues.clonesvr.com
- blues.clonesvr.com in ~/.ssh/known_hosts
- git installed via pkgsrc (devel/git-base)
- a shared virtualenv at `/home/gross/venvs/cognition-os`

## Provisioning Strategy

For deployable images, do not rely on every node downloading the full pkgsrc
 tree just to get `git`.

Preferred approaches:

- Build a base NetBSD image with `git` and Python preinstalled.
- Prebuild the shared virtualenv used by the rc.d services so FastAPI/Uvicorn
  and related Python modules are present before first boot.
- Use pkgsrc binary packages with `pkg_add` or `pkgin` instead of building
  from a full local pkgsrc checkout.
- Optionally host your own small binary package set if you want reproducible
  installs across nodes.

Example NetBSD binary package bootstrap:

    PKG_PATH="https://cdn.NetBSD.org/pub/pkgsrc/packages/NetBSD/$(uname -p)/$(uname -r|cut -f '1 2' -d.)/All/"
    export PKG_PATH
    pkg_add pkgin
    pkgin install git python310

Example shared runtime bootstrap:

    /home/gross/pkg/bin/python3.10 -m venv /home/gross/venvs/cognition-os
    /home/gross/venvs/cognition-os/bin/pip install -r /home/gross/src/cognition_os/requirements-shared-venv.txt

Licensing note:

- `pkgsrc` itself is BSD-licensed, but each packaged program keeps its own
  upstream license.
- For this project, distributing `git` is generally straightforward, but
  redistribution should still be reviewed per package if you expand the base
  image contents.

## Installation

As root, copy scripts to /etc/rc.d and set permissions:

    cp cogboot licd tortoised clones_brain dream_server /etc/rc.d/
    chmod 555 /etc/rc.d/cogboot /etc/rc.d/licd /etc/rc.d/tortoised
    chmod 555 /etc/rc.d/clones_brain /etc/rc.d/dream_server

Add to /etc/rc.conf:

    cogboot=YES
    licd=YES
    tortoised=YES
    clones_brain=YES
    dream_server=YES

    # Optional licd knobs
    licd_host="0.0.0.0"
    licd_port="8777"
    licd_allowlist="peace,blues,jazz,optiplex310,m2-arm64,xps-arm64"

    # Optional cogboot knobs
    blues_host="blues.clonesvr.com"
    cogboot_git_user="gross"
    cogboot_git_branch="main"
    cogboot_git_bin="/home/gross/pkg/bin/git"
    cogboot_ssh_key="/home/gross/.ssh/id_ed25519"
    cogboot_known_hosts="/home/gross/.ssh/known_hosts"
    cogboot_retry_count="5"
    cogboot_retry_delay="5"

    # Optional Python paths
    licd_python_bin="/home/gross/venvs/cognition-os/bin/python"
    tortoised_python_bin="/home/gross/venvs/cognition-os/bin/python"
    clones_brain_python_bin="/home/gross/venvs/cognition-os/bin/python"
    dream_server_python_bin="/home/gross/venvs/cognition-os/bin/python"
    dream_server_health_path="/server_status"

## Service Management

    service cogboot start        # clone/update all repositories
    service licd start|stop|status
    service tortoised start|stop|status
    service clones_brain start|stop|status
    service dream_server start|stop|status

## Logs

    /var/log/licd.log
    /var/log/tortoised.log
    /var/log/clones_brain.log
    /var/log/dream_server.log

    # syslog for cogboot
    grep cogboot /var/log/messages

## Health Checks

    curl http://localhost:8777/health    # licd
    curl http://localhost:8767/health    # tortoised
    curl http://localhost:8765/health    # clones-brain
    curl http://localhost:8181/health    # dream_server

## Adding a New Node

1. Flash the base NetBSD aarch64 VM image
2. Copy the shared SSH key to ~/.ssh/id_ed25519
3. Add blues.clonesvr.com to ~/.ssh/known_hosts
4. Install these rc.d scripts
5. Set hostname in /etc/rc.conf (e.g. hostname=newnode-arm64)
6. Reboot — the node clones itself and boots into cognitive readiness

## Notes

- cogboot uses git pull --ff-only on subsequent boots, so local changes
  on the VM will not be clobbered but will block the pull if they conflict.
- cogboot clones the Dream service from the `dream` repository into the local
  `dream_server` checkout path.
- If a target source directory already exists but is not a Git checkout,
  cogboot moves it aside with a timestamped `.pre-git-YYYYMMDD-HHMMSS` suffix
  before cloning the managed repository.
- The service wrappers assume a shared virtualenv at
  `/home/gross/venvs/cognition-os` unless overridden in `rc.conf`.
- The shared virtualenv is currently intended to use the curated
  `requirements-shared-venv.txt` from this repository rather than service-local
  requirement files, to avoid platform-specific compiled dependency problems
  during early deployments.
- clones_brain and dream_server use uvicorn; install via pkgsrc or pip after
  Python 3.10 is available.
- The licd allowlist should include the hostnames of all nodes in your
  Distributed Cognition network.
