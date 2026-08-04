# Native cogboot

## Current behavior and contracts

The original NetBSD `cogboot` is an `rc.d` one-shot bootstrap ordered after
`NETWORKING sshd` and before `licd`, `tortoised`, `clones_brain`, and
`dream_server`. It runs as root, invokes Git as user `gross`, prepares
`/home/gross/src`, and synchronizes four repositories. Linux and macOS ports
copy this behavior with systemd and launchd wrappers.

The repository mapping was not previously a file-backed manifest. Each shell
port embedded these records:

| Remote repository | Checkout | Required | Branch |
|---|---|---:|---|
| `licd` | `licd` | yes | `main` |
| `tortoised` | `tortoised` | yes | `main` |
| `clones-brain` | `clones-brain` | yes | `main` |
| `dream` | `dream_server` | yes | `main` |

The hardened shell revision retries failed operations, continues with other
repositories, aggregates failure, uses `git pull --ff-only`, and renames an
existing non-Git path before cloning. Its external contracts are the checkout
names consumed by service wrappers, the remote layout, success/failure exit
status, rc.d ordering, and its configuration defaults. It does not provide a
real status operation or distinguish dirty, ahead, and divergent histories.

Native cogboot preserves the mappings and fast-forward-only policy, but it is
more conservative: it never renames an existing path, invokes a shell, merges
divergent history, resets, or deletes. Production service definitions still
invoke the shell implementations; native cogboot is opt-in.

## Build and install

Portable C99 and POSIX process interfaces are used. Supported targets are
NetBSD, Linux, and macOS.

```sh
make
make test
make clean
make install PREFIX=/usr/local
```

`DESTDIR` is supported for staged packaging. Installation creates
`$PREFIX/bin/cogboot-native` and
`$PREFIX/share/cognition-os/cogboot.manifest`. Building requires a C99
compiler and testing requires Git plus POSIX shell utilities.

## Usage

```text
cogboot-native --manifest FILE --source-root ABSOLUTE_PATH [options]
```

Useful options are `--git PATH`, `--retry-count N`, `--retry-delay N`,
`--timeout N`, `--dry-run`, `--status`, `--help`, and `--version`.
The source root is mandatory and absolute, so behavior never depends on the
working directory. `--dry-run` and `--status` never fetch, clone, create, or
update; they inspect existing local and remote-tracking refs. Thus their view
may be stale until a real synchronization fetches.

For SSH remotes, configure noninteractive authentication in the account's SSH
configuration or agent. Native cogboot disables Git terminal prompts and does
not alter keys or known-host files.

## Manifest syntax

Version 1 is UTF-8/ASCII text with blank lines and lines beginning with `#`
ignored. Every record contains exactly five TAB-separated fields:

```text
required<TAB>name<TAB>checkout<TAB>repository-url<TAB>branch
```

The first field is `required` or `optional`. Checkout paths are relative to the
configured source root; absolute paths, empty components, `.` components, and
`..` components are rejected. Names and checkout paths must be unique. Empty,
duplicate, overlong, or malformed records reject the entire manifest rather
than being guessed. Tabs provide a minimally disruptive extension of the old
embedded `repo:checkout` pairs and allow spaces in paths and URLs. Migration is
mechanical: expand each pair with requirement, URL, and branch fields. The
shipped [`cogboot.manifest`](../cogboot.manifest) is that migration.

Manifest URLs and paths are passed as individual `exec` arguments. They are
never interpolated into shell commands. Checkout traversal and final-component
symlinks are rejected, inherited Git repository-routing environment variables
are removed, and captured Git output is not repeated where it could expose a
credential-bearing URL.

## Outcomes and failure semantics

Each record gets one final classification:

- `synchronized`: fetched and fast-forwarded;
- `already current`: fetched revision equals `HEAD`;
- `cloned`: absent repository cloned successfully;
- `skipped`: no-change mode found work that would be required, or processing
  stopped after interruption;
- `degraded`: repository remains usable but is dirty, ahead, or divergent;
- `failed`: repository is inaccessible, malformed, unsafe, or cannot sync.

All independent records are processed after ordinary failures. A required
repository in `degraded` or `failed` state makes the final exit status nonzero.
Optional failures are reported but do not fail the aggregate. Manifest/usage
errors use nonzero status before processing. SIGINT, SIGTERM, and SIGHUP stop
the active process group, skip remaining work, print a summary, and return
`128 + signal`.

Retries apply to clone and fetch failures, including network outages. Git
commands have a configurable timeout. Existing non-Git paths, dirty trees,
local-only commits, and divergence are never repaired automatically.

## Compatibility matrix

| Behavior | NetBSD shell | Linux/macOS shell | Native C |
|---|---|---|---|
| Four historical mappings | embedded | embedded | manifest |
| Clone absent checkout | yes | yes | yes |
| Fast-forward only | `pull --ff-only` | `pull --ff-only` | fetch + ancestry check + `merge --ff-only` |
| Continue after repo failure | yes | yes | yes |
| Aggregate required failure | yes | yes | yes |
| Rename non-Git path | yes | yes | no; failed |
| Dirty state classification | Git-dependent | Git-dependent | degraded; refused |
| Ahead/diverged classification | pull failure | pull failure | degraded; refused |
| Dry-run/status | no/effectively empty | no | yes |
| Process timeout/signal detail | no | no | yes |
| Shell interpolation exposure | yes | yes | no |
| Production default | yes | yes | no (opt-in) |

`tests/compat_shell.sh` is a controlled test reference for the common legacy
clone/fast-forward algorithm. The compatibility test runs it and native
cogboot against the same temporary local bare repository and compares checked
out revisions. It does not replace platform boot testing.

## Known limitations and risks

- NetBSD, Linux, and macOS compiler/runtime CI is not yet present; the suite
  must be run on each release target before activation.
- Status and dry-run intentionally do not test network reachability.
- Retry classification is conservative: all clone/fetch errors are retried,
  including permanent authentication and manifest mistakes.
- A crash during `git clone` can leave a partial directory. The next run
  refuses it instead of deleting it; an operator must inspect and remove or
  relocate it.
- The 1 MiB per-command capture limit is adequate for normal Git diagnostics;
  excessive output is discarded and reported through the command category.
- File ownership/user switching remains a service-wrapper concern. The native
  binary should be launched as the checkout owner.
- The historical shell scripts contain machine-specific defaults and shell
  command construction. They remain for reversibility, not as a security model.

