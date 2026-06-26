#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Please run as root: sudo ./macos/install-macos.sh" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COGUSER="${COGUSER:-${SUDO_USER:-gross}}"

if ! id "$COGUSER" >/dev/null 2>&1; then
  echo "User $COGUSER does not exist. Use: sudo COGUSER=<user> ./macos/install-macos.sh" >&2
  exit 1
fi

COGDIR="${COGDIR:-$(dscl . -read "/Users/$COGUSER" NFSHomeDirectory 2>/dev/null | awk '{print $2}') }"
COGDIR="${COGDIR% }"
if [[ -z "$COGDIR" || ! -d "$COGDIR" ]]; then
  COGDIR="/Users/$COGUSER"
fi

PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"
GIT_BIN="${GIT_BIN:-/usr/bin/git}"

if [[ ! -x "$GIT_BIN" ]]; then
  echo "git not found at $GIT_BIN. Install Apple Command Line Tools first: xcode-select --install" >&2
  exit 1
fi
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "python3 not found at $PYTHON_BIN. Install Apple Command Line Tools or set PYTHON_BIN." >&2
  exit 1
fi

install -d -m 755 /etc/cognition-os
install -d -m 755 /usr/local/lib/cognition-os
install -d -m 755 /usr/local/var/log/cognition-os
chown "$COGUSER:staff" /usr/local/var/log/cognition-os 2>/dev/null || chown "$COGUSER" /usr/local/var/log/cognition-os
install -d -m 755 /Library/LaunchDaemons
install -m 644 "$SCRIPT_DIR/etc/cognition-os.env" /etc/cognition-os/cognition-os.env

# Patch env defaults for the selected macOS user/home.
perl -0pi -e "s#^COGUSER=.*#COGUSER=$COGUSER#m" /etc/cognition-os/cognition-os.env
perl -0pi -e "s#^COGDIR=.*#COGDIR=$COGDIR#m" /etc/cognition-os/cognition-os.env
perl -0pi -e "s#^COG_SRC=.*#COG_SRC=$COGDIR/src#m" /etc/cognition-os/cognition-os.env
perl -0pi -e "s#^COG_VENV=.*#COG_VENV=$COGDIR/venvs/cognition-os#m" /etc/cognition-os/cognition-os.env
perl -0pi -e "s#^PYTHON_BIN=.*#PYTHON_BIN=$PYTHON_BIN#m" /etc/cognition-os/cognition-os.env
perl -0pi -e "s#^GIT_BIN=.*#GIT_BIN=$GIT_BIN#m" /etc/cognition-os/cognition-os.env
perl -0pi -e "s#^COGBOOT_SSH_KEY=.*#COGBOOT_SSH_KEY=$COGDIR/.ssh/id_ed25519#m" /etc/cognition-os/cognition-os.env
perl -0pi -e "s#^COGBOOT_KNOWN_HOSTS=.*#COGBOOT_KNOWN_HOSTS=$COGDIR/.ssh/known_hosts#m" /etc/cognition-os/cognition-os.env

install -m 755 "$SCRIPT_DIR/bin/cogboot-macos" /usr/local/lib/cognition-os/cogboot-macos
install -m 755 "$SCRIPT_DIR/bin/bootstrap-shared-venv-macos" /usr/local/lib/cognition-os/bootstrap-shared-venv-macos
install -m 755 "$SCRIPT_DIR/bin/run-service-macos" /usr/local/lib/cognition-os/run-service-macos
install -m 755 "$SCRIPT_DIR/bin/doctor-macos" /usr/local/lib/cognition-os/doctor-macos

# Keep this cognition_os checkout available at the path expected by the venv bootstrap.
install -d -o "$COGUSER" -g staff "$COGDIR/src"
if [[ "$REPO_ROOT" != "$COGDIR/src/cognition_os" ]]; then
  rm -rf "$COGDIR/src/cognition_os"
  cp -a "$REPO_ROOT" "$COGDIR/src/cognition_os"
  chown -R "$COGUSER:staff" "$COGDIR/src/cognition_os" 2>/dev/null || chown -R "$COGUSER" "$COGDIR/src/cognition_os"
fi

for plist in "$SCRIPT_DIR/launchd/"*.plist; do
  dest="/Library/LaunchDaemons/$(basename "$plist")"
  install -m 644 "$plist" "$dest"
  perl -0pi -e "s#<key>UserName</key>\n  <string>[^<]+</string>#<key>UserName</key>\n  <string>$COGUSER</string>#g" "$dest"
  chown root:wheel "$dest"
  chmod 644 "$dest"
done

# Unload any already-loaded copies so the edited plists are the source of truth.
for label in \
  com.cognition-os.licd \
  com.cognition-os.tortoised \
  com.cognition-os.clones-brain \
  com.cognition-os.dream-server \
  com.cognition-os.cogboot; do
  launchctl bootout system "/Library/LaunchDaemons/${label}.plist" >/dev/null 2>&1 || true
done

echo
echo "Installed Cognition OS macOS launchd files."
echo
echo "Next steps:"
echo "  1. Verify SSH access as $COGUSER to blues.clonesvr.com."
echo "  2. Edit /etc/cognition-os/cognition-os.env if needed."
echo "  3. Run: sudo launchctl bootstrap system /Library/LaunchDaemons/com.cognition-os.cogboot.plist"
echo "  4. Run: sudo launchctl kickstart -k system/com.cognition-os.cogboot"
echo "  5. Run: sudo /usr/local/lib/cognition-os/bootstrap-shared-venv-macos"
echo "  6. Load services:"
echo "     sudo launchctl bootstrap system /Library/LaunchDaemons/com.cognition-os.licd.plist"
echo "     sudo launchctl bootstrap system /Library/LaunchDaemons/com.cognition-os.tortoised.plist"
echo "     sudo launchctl bootstrap system /Library/LaunchDaemons/com.cognition-os.clones-brain.plist"
echo "     sudo launchctl bootstrap system /Library/LaunchDaemons/com.cognition-os.dream-server.plist"
echo "  7. Start/check one service, for example:"
echo "     sudo launchctl kickstart -k system/com.cognition-os.licd"
echo "     sudo launchctl print system/com.cognition-os.licd"
echo
echo "Logs go to /usr/local/var/log/cognition-os/<service>.log and <service>.err.log"
