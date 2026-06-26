#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Please run as root: sudo ./ubuntu/install-ubuntu.sh" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COGUSER="${COGUSER:-gross}"
COGDIR="${COGDIR:-/home/$COGUSER}"

if ! id "$COGUSER" >/dev/null 2>&1; then
  echo "User $COGUSER does not exist. Create it first or run: sudo COGUSER=<user> ./ubuntu/install-ubuntu.sh" >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y git openssh-client python3 python3-venv python3-pip curl ca-certificates sudo

install -d -m 755 /etc/cognition-os
install -d -m 755 /usr/local/lib/cognition-os
install -m 644 "$SCRIPT_DIR/etc/cognition-os.env" /etc/cognition-os/cognition-os.env

# Adjust default user/home in the env file when installing for a non-default user.
sed -i "s#^COGUSER=.*#COGUSER=$COGUSER#" /etc/cognition-os/cognition-os.env
sed -i "s#^COGDIR=.*#COGDIR=$COGDIR#" /etc/cognition-os/cognition-os.env
sed -i "s#^COG_SRC=.*#COG_SRC=$COGDIR/src#" /etc/cognition-os/cognition-os.env
sed -i "s#^COG_VENV=.*#COG_VENV=$COGDIR/venvs/cognition-os#" /etc/cognition-os/cognition-os.env
sed -i "s#^COGBOOT_SSH_KEY=.*#COGBOOT_SSH_KEY=$COGDIR/.ssh/id_ed25519#" /etc/cognition-os/cognition-os.env
sed -i "s#^COGBOOT_KNOWN_HOSTS=.*#COGBOOT_KNOWN_HOSTS=$COGDIR/.ssh/known_hosts#" /etc/cognition-os/cognition-os.env

install -m 755 "$SCRIPT_DIR/bin/cogboot-linux" /usr/local/lib/cognition-os/cogboot-linux
install -m 755 "$SCRIPT_DIR/bin/bootstrap-shared-venv-linux" /usr/local/lib/cognition-os/bootstrap-shared-venv-linux

# Keep this cognition_os checkout available at the path expected by the venv bootstrap.
install -d -o "$COGUSER" -g "$COGUSER" "$COGDIR/src"
if [[ "$REPO_ROOT" != "$COGDIR/src/cognition_os" ]]; then
  rm -rf "$COGDIR/src/cognition_os"
  cp -a "$REPO_ROOT" "$COGDIR/src/cognition_os"
  chown -R "$COGUSER:$COGUSER" "$COGDIR/src/cognition_os"
fi

install -m 644 "$SCRIPT_DIR/systemd/"*.service /etc/systemd/system/

# Patch systemd units for non-default user/home.
for unit in /etc/systemd/system/licd.service /etc/systemd/system/tortoised.service /etc/systemd/system/clones-brain.service /etc/systemd/system/dream-server.service; do
  sed -i "s#^User=.*#User=$COGUSER#" "$unit"
  sed -i "s#/home/gross#$COGDIR#g" "$unit"
done

systemctl daemon-reload

echo
echo "Installed Cognition OS Ubuntu service files."
echo
echo "Next steps:"
echo "  1. Verify SSH access as $COGUSER to blues.clonesvr.com."
echo "  2. Edit /etc/cognition-os/cognition-os.env if needed."
echo "  3. Run: sudo systemctl start cogboot"
echo "  4. Run: sudo /usr/local/lib/cognition-os/bootstrap-shared-venv-linux"
echo "  5. Run: sudo systemctl enable --now licd tortoised clones-brain dream-server"
echo "  6. Check: systemctl status licd tortoised clones-brain dream-server"
echo
echo "For a full boot-time install after validation:"
echo "  sudo systemctl enable cogboot licd tortoised clones-brain dream-server"
