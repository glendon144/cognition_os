# Cognition OS — Ubuntu Linux Port

This directory ports the NetBSD `rc.d` boot model to Ubuntu `systemd`.

The original NetBSD model uses `/etc/rc.d/*` wrappers. The Ubuntu model uses:

- `cogboot.service` — clones/updates the service repositories from `blues.clonesvr.com`.
- `licd.service` — Local Inference Controller Daemon on port 8777.
- `tortoised.service` — inference service on port 8767.
- `clones-brain.service` — SQLite/FastAPI memory service on port 8765.
- `dream-server.service` — Dream capsule storage server on port 8181.

## Install

From the top of the `cognition_os` checkout:

```bash
sudo ./ubuntu/install-ubuntu.sh
```

For a user other than `gross`:

```bash
sudo COGUSER=$USER ./ubuntu/install-ubuntu.sh
```

## Configure

Edit:

```bash
sudo nano /etc/cognition-os/cognition-os.env
```

Important defaults:

- services bind to `127.0.0.1` by default for safety;
- repositories are fetched from `blues.clonesvr.com` using SSH;
- the SSH key is expected at `/home/gross/.ssh/id_ed25519` unless changed;
- the shared virtualenv is `/home/gross/venvs/cognition-os` unless changed.

## First run

```bash
sudo systemctl start cogboot
sudo /usr/local/lib/cognition-os/bootstrap-shared-venv-linux
sudo systemctl enable --now licd tortoised clones-brain dream-server
```

## Health checks

```bash
curl http://127.0.0.1:8777/health
curl http://127.0.0.1:8767/health
curl http://127.0.0.1:8765/health
curl http://127.0.0.1:8181/server_status
```

## Logs

Ubuntu uses journald instead of `/var/log/*.log` files:

```bash
journalctl -u cogboot -n 100 --no-pager
journalctl -u licd -f
journalctl -u tortoised -f
journalctl -u clones-brain -f
journalctl -u dream-server -f
```

## Security notes

The Ubuntu port intentionally defaults most services to `127.0.0.1`. If a service must be reachable from another host, prefer an SSH tunnel, VPN, nginx reverse proxy, or firewall-restricted bind rather than changing it casually to `0.0.0.0`.

Use `LICD_REQUIRE_TOKEN=1` and set a real `LICD_TOKEN` before exposing LICD beyond localhost.
