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
- clones_brain and dream_server use uvicorn; install via pkgsrc or pip after
  Python 3.10 is available.
- The licd allowlist should include the hostnames of all nodes in your
  Distributed Cognition network.
