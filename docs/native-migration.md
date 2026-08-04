# Incremental native-core migration

Cognition OS remains a fabric of independent services. Native code should
replace one bounded implementation at a time while preserving published
process, HTTP, file-layout, and lifecycle interfaces. A component may prepare
or orchestrate another service through those interfaces; it must not absorb
that service's business logic or open its private database directly. During a
migration, old and new implementations coexist behind an explicit operator
selection until compatibility, failure, restart, and rollback tests pass.

## NetBSD-first cogboot rollout

1. Build and run `make test` on a fresh supported NetBSD installation with
   pkgsrc Git and both the base compiler and Clang where available.
2. Install `cogboot-native` and the manifest beside, not over, `/etc/rc.d/cogboot`.
3. Run status and dry-run as `gross` against `/home/gross/src`; resolve every
   dirty, ahead, malformed, or divergent checkout manually.
4. Exercise a disposable local-remote fixture, network loss, reboot, timeout,
   SIGTERM, and the restored-image divergence scenario. Confirm no merge,
   reset, deletion, or out-of-root write occurs.
5. Add a separate `cogboot_native` rc.d wrapper with an explicit enable knob,
   running as the checkout owner and retaining the old `cogboot=YES` rollback.
   Do not enable both boot jobs concurrently.
6. Canary one NetBSD node. Compare summaries and resulting revisions for
   several boot/update cycles, then expand gradually.
7. Only after target-native tests and operational observation pass should the
   native wrapper become the recommended NetBSD default. Keep the shell script
   packaged through at least one rollback window.

The next native component should be the small service-launch/health-check
wrapper, not LICD, memory, inference, or Dream business logic. It has a narrow
portable process/lifecycle contract and can reuse the process execution layer,
while leaving every service independent. This is a recommendation only; it is
not implemented here.

