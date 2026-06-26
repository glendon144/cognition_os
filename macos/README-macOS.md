# Cognition OS macOS Port

This directory ports the current NetBSD/Ubuntu Cognition OS service harness to macOS using `launchd`.

The port keeps the same high-level service set:

- `cogboot` repository bootstrap
- `licd` local inference controller
- `tortoised` inference service
- `clones-brain` memory service
- `dream-server` dream capsule storage service

## What this port does

The macOS installer:

1. Installs shared configuration at `/etc/cognition-os/cognition-os.env`.
2. Installs helper scripts under `/usr/local/lib/cognition-os/`.
3. Copies this `cognition_os` checkout to `$COGDIR/src/cognition_os`.
4. Installs `launchd` daemon plists in `/Library/LaunchDaemons`.
5. Leaves service loading/startup as an explicit operator step.

The actual application repositories are still cloned from the existing remote layout:

```text
ssh://$COGBOOT_GIT_USER@$BLUES_HOST/home/$COGUSER/git/<repo>
```

That matches the original NetBSD `cogboot` behavior.

## Install

From the root of the unpacked tarball:

```bash
sudo ./macos/install-macos.sh
```

By default, the installer uses the invoking sudo user. You can override this:

```bash
sudo COGUSER=gross ./macos/install-macos.sh
```

If you use Homebrew or MacPorts Python instead of Apple `/usr/bin/python3`, pass it explicitly:

```bash
sudo PYTHON_BIN=/opt/homebrew/bin/python3 ./macos/install-macos.sh
```

## First run

Verify SSH first:

```bash
ssh -i ~/.ssh/id_ed25519 gross@blues.clonesvr.com
```

Then run the bootstrap:

```bash
sudo launchctl bootstrap system /Library/LaunchDaemons/com.cognition-os.cogboot.plist
sudo launchctl kickstart -k system/com.cognition-os.cogboot
```

Build the shared Python environment:

```bash
sudo /usr/local/lib/cognition-os/bootstrap-shared-venv-macos
```

Load services:

```bash
sudo launchctl bootstrap system /Library/LaunchDaemons/com.cognition-os.licd.plist
sudo launchctl bootstrap system /Library/LaunchDaemons/com.cognition-os.tortoised.plist
sudo launchctl bootstrap system /Library/LaunchDaemons/com.cognition-os.clones-brain.plist
sudo launchctl bootstrap system /Library/LaunchDaemons/com.cognition-os.dream-server.plist
```

Start or restart a service:

```bash
sudo launchctl kickstart -k system/com.cognition-os.licd
```

Check a service:

```bash
sudo launchctl print system/com.cognition-os.licd
```

View logs:

```bash
tail -f /var/log/com.cognition-os.licd.log
tail -f /var/log/com.cognition-os.licd.err.log
```

## Stop/unload

```bash
sudo launchctl bootout system /Library/LaunchDaemons/com.cognition-os.licd.plist
sudo launchctl bootout system /Library/LaunchDaemons/com.cognition-os.tortoised.plist
sudo launchctl bootout system /Library/LaunchDaemons/com.cognition-os.clones-brain.plist
sudo launchctl bootout system /Library/LaunchDaemons/com.cognition-os.dream-server.plist
sudo launchctl bootout system /Library/LaunchDaemons/com.cognition-os.cogboot.plist
```

## Notes

macOS `launchd` does not provide systemd-style dependency ordering. For that reason, this port keeps bootstrap, venv creation, and service loading as explicit first-run steps. The service wrapper also waits briefly for expected files to appear, which helps after a fresh boot or repository sync.

For a development Mac, the safest workflow is to run `cogboot`, then the shared venv bootstrap, then load only the services you actually want on that machine.


## Troubleshooting `Bootstrap failed: 5: Input/output error`

Run the installer again from this patched tree, then run:

```sh
sudo /usr/local/lib/cognition-os/doctor-macos
plutil -lint /Library/LaunchDaemons/com.cognition-os.dream-server.plist
ls -l /usr/local/lib/cognition-os/run-service-macos
ls -ld /usr/local/var/log/cognition-os
```

This version writes logs under `/usr/local/var/log/cognition-os/` instead of `/var/log/`, because LaunchDaemons running as the non-root Cognition OS user may fail during bootstrap if launchd cannot open the configured stdout/stderr paths.
