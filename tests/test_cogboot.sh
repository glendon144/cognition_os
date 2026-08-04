#!/bin/sh
set -u

COGBOOT=${COGBOOT:-./build/cogboot}
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/cogboot-test.XXXXXX") || exit 1
trap 'rm -rf "$ROOT"' EXIT HUP INT TERM
export GIT_AUTHOR_NAME='Cogboot Test' GIT_AUTHOR_EMAIL='cogboot@example.invalid'
export GIT_COMMITTER_NAME="$GIT_AUTHOR_NAME" GIT_COMMITTER_EMAIL="$GIT_AUTHOR_EMAIL"
tests=0 failures=0

ok() { tests=$((tests + 1)); printf 'ok %d - %s\n' "$tests" "$1"; }
not_ok() { tests=$((tests + 1)); failures=$((failures + 1)); printf 'not ok %d - %s\n' "$tests" "$1"; }
assert() { name=$1; shift; if "$@"; then ok "$name"; else not_ok "$name"; fi; }
contains() { grep -F "$2" "$1" >/dev/null 2>&1; }
run_cb() { "$COGBOOT" --retry-count 0 --retry-delay 0 --timeout 5 "$@"; }

make_remote() {
    id=$1
    bare="$ROOT/$id.git" seed="$ROOT/$id-seed"
    git init --bare -q "$bare"
    git init -q -b main "$seed"
    printf 'one\n' > "$seed/file"
    git -C "$seed" add file && git -C "$seed" commit -qm initial
    git -C "$seed" remote add origin "$bare" && git -C "$seed" push -q -u origin main
    git -C "$bare" symbolic-ref HEAD refs/heads/main
}
manifest_one() { printf 'required\t%s\t%s\t%s\tmain\n' "$1" "$2" "$3" > "$4"; }

make_remote base
manifest_one base checkout "$ROOT/base.git" "$ROOT/valid.manifest"
mkdir -p "$ROOT/src"
run_cb -m "$ROOT/valid.manifest" -r "$ROOT/src" >"$ROOT/out" 2>"$ROOT/err"
assert 'valid manifest and successful clone' test -d "$ROOT/src/checkout/.git"
assert 'clone classification' contains "$ROOT/out" 'cloned'

run_cb -m "$ROOT/valid.manifest" -r "$ROOT/src" >"$ROOT/out" 2>"$ROOT/err"
assert 'already-current repository' contains "$ROOT/out" 'already current'

printf 'two\n' >> "$ROOT/base-seed/file"
git -C "$ROOT/base-seed" commit -qam update && git -C "$ROOT/base-seed" push -q
run_cb -m "$ROOT/valid.manifest" -r "$ROOT/src" >"$ROOT/out" 2>"$ROOT/err"
assert 'successful fast-forward' contains "$ROOT/out" 'synchronized'

printf 'dirty\n' >> "$ROOT/src/checkout/file"
if run_cb -m "$ROOT/valid.manifest" -r "$ROOT/src" >"$ROOT/out" 2>"$ROOT/err"; then not_ok 'dirty tree exits nonzero'; else ok 'dirty tree exits nonzero'; fi
assert 'dirty tree degraded' contains "$ROOT/out" 'degraded'
git -C "$ROOT/src/checkout" reset -q --hard

printf 'local\n' >> "$ROOT/src/checkout/file" && git -C "$ROOT/src/checkout" commit -qam local
printf 'remote\n' >> "$ROOT/base-seed/file" && git -C "$ROOT/base-seed" commit -qam remote && git -C "$ROOT/base-seed" push -q
if run_cb -m "$ROOT/valid.manifest" -r "$ROOT/src" >"$ROOT/out" 2>"$ROOT/err"; then not_ok 'diverged branch exits nonzero'; else ok 'diverged branch exits nonzero'; fi
assert 'divergence is degraded and refused' contains "$ROOT/out" 'has diverged'

printf 'required\tonlyfour\tpath\turl\n' > "$ROOT/bad.manifest"
if run_cb -m "$ROOT/bad.manifest" -r "$ROOT/src" >"$ROOT/out" 2>"$ROOT/err"; then not_ok 'malformed manifest rejected'; else ok 'malformed manifest rejected'; fi
assert 'malformed diagnostic includes line' contains "$ROOT/err" ':1:'

printf 'required\ta\t../escape\t%s\tmain\n' "$ROOT/base.git" > "$ROOT/traversal.manifest"
if run_cb -m "$ROOT/traversal.manifest" -r "$ROOT/src" >/dev/null 2>&1; then not_ok 'path traversal rejected'; else ok 'path traversal rejected'; fi

printf 'required\ta\tx\t%s\tmain\nrequired\ta\ty\t%s\tmain\n' "$ROOT/base.git" "$ROOT/base.git" > "$ROOT/duplicate.manifest"
if run_cb -m "$ROOT/duplicate.manifest" -r "$ROOT/src" >/dev/null 2>&1; then not_ok 'duplicate manifest rejected'; else ok 'duplicate manifest rejected'; fi

mkdir -p "$ROOT/dry"
run_cb --dry-run -m "$ROOT/valid.manifest" -r "$ROOT/dry" >"$ROOT/out" 2>"$ROOT/err"
assert 'dry-run does not clone' test ! -e "$ROOT/dry/checkout"
assert 'dry-run reports skipped' contains "$ROOT/out" 'skipped'
run_cb --status -m "$ROOT/valid.manifest" -r "$ROOT/dry" >/dev/null 2>&1
assert 'status mode does not clone' test ! -e "$ROOT/dry/checkout"

mkdir -p "$ROOT/space root"
manifest_one space 'path with spaces' "$ROOT/base.git" "$ROOT/space.manifest"
run_cb -m "$ROOT/space.manifest" -r "$ROOT/space root" >/dev/null 2>&1
assert 'paths containing spaces' test -d "$ROOT/space root/path with spaces/.git"

manifest_one missing nope "$ROOT/no-such-remote" "$ROOT/unreachable.manifest"
if run_cb -m "$ROOT/unreachable.manifest" -r "$ROOT/dry" >"$ROOT/out" 2>"$ROOT/err"; then not_ok 'unreachable remote fails'; else ok 'unreachable remote fails'; fi
assert 'unreachable remote summarized' contains "$ROOT/out" 'failed=1'

if run_cb --git "$ROOT/no-git" -m "$ROOT/unreachable.manifest" -r "$ROOT/dry" >"$ROOT/out" 2>"$ROOT/err"; then not_ok 'missing Git executable fails'; else ok 'missing Git executable fails'; fi
assert 'missing Git classified' contains "$ROOT/err" 'executable not found'

make_remote retry
manifest_one retry retry "$ROOT/retry.git" "$ROOT/retry.manifest"
export COGBOOT_FLAKY_STATE="$ROOT/flaky-count" COGBOOT_FLAKY_FAILURES=1
"$COGBOOT" --git "$PWD/tests/helpers/flaky-git" --retry-count 1 --retry-delay 0 --timeout 5 -m "$ROOT/retry.manifest" -r "$ROOT/dry" >/dev/null 2>&1
assert 'retry succeeds after transient failure' test -d "$ROOT/dry/retry/.git"
assert 'retry attempted twice' test "$(cat "$ROOT/flaky-count")" -ge 2

make_remote good
printf 'required\tbad\tbad\t%s\tmain\nrequired\tgood\tgood\t%s\tmain\n' "$ROOT/nope" "$ROOT/good.git" > "$ROOT/mixed.manifest"
if run_cb -m "$ROOT/mixed.manifest" -r "$ROOT/dry" >"$ROOT/out" 2>"$ROOT/err"; then not_ok 'aggregate exit is nonzero'; else ok 'aggregate exit is nonzero'; fi
assert 'continues after independent failure' test -d "$ROOT/dry/good/.git"
assert 'summary aggregates clone and failure' contains "$ROOT/out" 'cloned=1 skipped=0 degraded=0 failed=1'

run_cb -m "$ROOT/retry.manifest" -r "$ROOT/dry" >/dev/null 2>&1
head1=$(git -C "$ROOT/dry/retry" rev-parse HEAD)
run_cb -m "$ROOT/retry.manifest" -r "$ROOT/dry" >/dev/null 2>&1
head2=$(git -C "$ROOT/dry/retry" rev-parse HEAD)
assert 'idempotent repeated execution' test "$head1" = "$head2"

mkdir -p "$ROOT/shell-src" "$ROOT/native-src"
sh tests/compat_shell.sh "$ROOT/retry.manifest" "$ROOT/shell-src"
run_cb -m "$ROOT/retry.manifest" -r "$ROOT/native-src" >/dev/null 2>&1
assert 'shell/C compatibility: same revision' test "$(git -C "$ROOT/shell-src/retry" rev-parse HEAD)" = "$(git -C "$ROOT/native-src/retry" rev-parse HEAD)"

export COGBOOT_SLEEP_SECONDS=10
"$COGBOOT" --git "$PWD/tests/helpers/sleep-git" --retry-count 0 --timeout 1 -m "$ROOT/retry.manifest" -r "$ROOT/native-src" >"$ROOT/out" 2>"$ROOT/err" & pid=$!
wait "$pid"; rc=$?
assert 'process timeout returns failure' test "$rc" -ne 0
assert 'timeout is classified' contains "$ROOT/err" 'timeout'

"$COGBOOT" --git "$PWD/tests/helpers/sleep-git" --retry-count 0 --timeout 30 -m "$ROOT/retry.manifest" -r "$ROOT/native-src" >"$ROOT/out" 2>"$ROOT/err" & pid=$!
sleep 1; kill -TERM "$pid"; wait "$pid"; rc=$?
assert 'signal interruption returns 143' test "$rc" -eq 143

printf '1..%d\n' "$tests"
[ "$failures" -eq 0 ]
