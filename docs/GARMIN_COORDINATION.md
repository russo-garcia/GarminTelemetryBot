# Phase 2B.2: development-only Garmin ownership coordination

Audited starting commit: `a1634372ecf912a9cfe6edefef857fd7f3b73abc`.
No deployment, service changes, timer, cron job, credential reads, live API calls,
production authentication writes, or prescription synchronization have been performed.
The separate ISP-monitor project is outside the Garmin migration inventory.

## Boundary and lifecycle

`garmin_auth.py` is import-safe and uses only the Python standard library on POSIX.
It is not Google OAuth's `auth.py`, which remains unchanged.

1. Validate absolute token-file and lock-file paths and finite acquisition timeout.
   Defaults in `collector_runtime` are anchored to that module's directory, never
   the invoking working directory. Production review must explicitly pin both
   paths to the existing token file and a common stable local lock.
2. Acquire a shared per-path process-local mutex, then an exclusive nonblocking
   `fcntl.flock`, retrying within one total monotonic acquisition deadline.
3. The writer may create the owner-only lock inode if absent. Never unlink it,
   delete a supposed stale lock, or put the lock on the token inode. Symlinks,
   lock/token hard-link identity, inappropriate lock permissions and changed lock
   inode identity fail closed. The containing directory must be trusted.
4. Only now call a factory to construct a NEW Garmin SDK client. Call its
   `login(absolute_token_file)` while owned. The deployed client's existing
   restoration/refresh/publication behavior stays inside the owner boundary.
5. Use the restricted acquisition facade on the acquiring thread and PID. It
   permits only activity listing, original activity download, stats and sleep.
   It returns detached plain data/bytes, not an SDK object or a lazy operation.
6. Finish all Garmin calls. Invalidate the facade, drop SDK references, release
   flock, close its descriptor, and release the thread mutex, including on errors.
   A saved facade or bound method rejects later use. A new lease constructs and
   loads again; no client/refresh credential cache is reused between leases.
7. Perform Renpho, Drive upload, local output and maintenance sleeps after release.

The stable lock inode is separate from the native client's atomically replaced
JSON token inode. Atomic JSON publication alone is not mutual exclusion. Kernel
locks release on process exit/termination. Fork hooks close inherited descriptors
without unlocking the parent's open-file description, reset child mutex state,
and inherited session facades reject use in the child. Forking an active SDK
client is not a supported acquisition workflow.

The 5-second default bounds waiting for ownership, **not HTTP request duration**.
Units are bounded by request count/date, not a new network deadline. Existing SDK
network behavior is unchanged. There is no guaranteed FIFO fairness. On timeout,
`GarminBusy` is raised before client construction; never fall back to direct login.

## Configuration

| Environment variable | Default | Validation |
|---|---|---|
| `GARMIN_TOKEN_STORE` | absolute module-directory `garmin_tokens.json` | absolute regular-file location, no symlink |
| `GARMIN_COORDINATOR_LOCK` | absolute module-directory `garmin_auth.lock` | absolute stable separate local file, owner-only |
| `GARMIN_LOCK_TIMEOUT_SECONDS` | `5` | finite, nonnegative seconds; zero is a nonblocking attempt |

The coordinator does not create parent directories, relocate credentials, create
credential backups or migrate a token format. It passes the explicit existing
store to the native SDK. A genuine SDK login/refresh remains the collector owner's
responsibility, not the prescription reader's. Existing installed library versions
remain unchanged; the audited deployed client is garminconnect 0.3.16.

`config.json`, Google `token.json`, and `synced_ids.txt` are also anchored to the
collector module directory instead of the launch CWD. They are accessed only at
runtime when needed. No private configuration or credential is included in tests.
The existing Renpho configuration location/selection policy is preserved.

## Migrated acquisition paths

| Entry point | Ownership unit | Non-Garmin work |
|---|---|---|
| `GarminTelemetry.init_garmin` | re-export of runtime context factory | returns a context, never bare SDK |
| Telegram activity handler | one list of latest 5; then one separate lease per unsynced original download | messages, Drive, sidecar writing, ledger outside lease |
| Telegram health handler | delegates to health_sync | no separate login |
| `health_sync.sync_health_data` | one date: get_stats + get_sleep_data on one fresh client | Renpho, health bundle, Drive after release |
| standalone health_sync | same function via main | same payload and default today |
| activity backfill | one 50-item metadata page per lease, each download its own lease | folder routing, upload, ledger, throttle sleeps outside lease |
| health_backfill | unchanged loop through coordinated health_sync | release after each date |
| renpho_backfill | unchanged loop through coordinated health_sync | remains an indirect Garmin caller despite name |

Backfills remain manual historical/bootstrap/maintenance tools, not scheduled
jobs or normal app missing-data recovery. Their starting dates and activity cutoff
are unchanged. No months-long loop owns one session. Busy activity backfill stops
and can be rerun using the existing ledger; health backfills retain their existing
boolean failure/backoff behavior. There is no unprotected retry.

## Minimal import refactor

`collector_runtime.py` contains the existing Drive and ledger helpers plus lazy
configuration/Drive construction. Dependency initialization has its own small
thread lock; it is not the Garmin lease. `GarminTelemetry.run_bot()` explicitly
creates Telegram and registers the existing five handlers. Health/backfill helpers
import runtime, not the entry script, eliminating import-time Telegram/Drive setup
and the __main__/module duplicate collector initialization.

Renpho's third-party import is deferred until its existing function is called.
Neither importing collector helpers nor running the test suite needs installed
Garmin, Telegram, Google or Renpho packages. Actual operation still needs the
existing production dependencies; nothing installs or upgrades them here.

## Future prescription reader: coordination contract, not a worker

`coordinator.read_only(guarded_loader)` acquires the SAME ownership before invoking
`guarded_loader(absolute_store)`. It opens an ALREADY PROVISIONED lock inode using
O_RDONLY; it cannot create it. That permits the Phase 2B.1 no-filesystem-writes
policy to remain active (local POSIX filesystem required). The facade exposes only
`get(path)`, is invalid after context exit, and returns plain evidence bytes/data.

The loader MUST be the existing audited Phase 2B.1 `deployed_reader`, or a tested
adapter with its same guards: disable login/refresh/persistence before loading
current credentials, constrain HTTP methods/hosts/paths, and prohibit authentication
filesystem changes. The coordinator provides mutual exclusion, **not** those
HTTP/refresh guards. Injecting an unguarded loader is outside the supported contract;
Python private attributes are not a sandbox against malicious application code.
No duplicate Garmin reader, auth store, token migration or scheduled worker is added.

Catch the reader's sanitized `REFRESH_REQUIRED` outside the read_only context.
Only after release may the application notify/await the collector owner. The reader
must not refresh independently and must never wait for the owner while holding its
lease. Reload a fresh reader after any new lease. Lock order is always:
**Garmin authentication lease -> prescription-state lease**. No code in this patch
acquires a prescription-state lock or modifies analytics/prescription projections.

## Preserved behavior and deliberate operational changes

Preserved: latest-5 selection, 50-item backfill pages, historical date boundaries,
activity skip rules, FIT bytes, activity JSON indent/content, health field bundle,
Renpho carry-forward selection, Drive names/type/year/month organization, existing
upload update-versus-create logic, and maintenance sleeps. No analytics changes.

Deliberate changes:
- init_garmin is now a context API; unknown external callers expecting a bare client
  must migrate rather than be given an unsafe compatibility fallback.
- Fresh client/load for each ownership unit can add SDK login/profile-read overhead.
- Busy requests return a controlled message/False or stop activity backfill.
- Configuration and service-construction errors move from import time to use time.
- Module-directory paths replace CWD-sensitive config/Google-token/ledger paths.
- Raw exception bodies are replaced by fixed error messages in acquisition and
  Renpho paths, avoiding accidental credential/response disclosure; less diagnostic
  detail is intentionally exposed. Backfill CLI errors are sanitized.

## Residual risks (not solved by auth ownership)

All Garmin-capable processes must participate. An old running collector or copied
old manual script can bypass advisory locking. Multi-file deployment requires a
coordinated stop/drain/restart, never hot-copy into a running process.

Drive's shared HTTP service and folder create/update operations, and the local
synced_ids ledger remain independently race-prone. Tests demonstrate that concurrent
activity requests can still select/upload the same activity. Do not mistake the
Garmin lock for a global job/idempotency lock. Fixing that race is separate scope.
The current bot's caller authorization policy is also unchanged.

The identical 46-test suite has now passed on the Pi's ARM/Linux Python 3.13.5
in credential-free staging, and installed garminconnect 0.3.16 passed static
interface/source-identity checks. Real SDK authentication and live responses remain
untested in this phase. See VALIDATION.md for the source-freeze and staging record;
production deployment and a bounded live smoke check require separate approvals.
No fairness guarantee or forced SDK-operation timeout is introduced. Library updates
require revalidation of refresh/persistence and guarded-reader semantics.

## Deployment review plan — NOT EXECUTED

1. Review this diff and agree on explicit existing token path, stable local lock path,
   service user and owner-only directory/lock permissions. Retain existing tokens;
   do not copy/read/back up them. Address loose Google/private-config permissions
   separately under approved hardening, without changing values.
2. Run this unittest suite in a separate source-only Pi staging copy with fake data,
   no runtime config, and no network credentials. Do not install/upgrade Garmin.
3. Arrange a maintenance window. Prevent manual Garmin backfills/new requests, stop
   and drain the existing garminbot process and any known active maintenance calls.
4. Deploy all changed/new source files together; retain previous CODE for rollback.
   Pin common absolute auth/lock configuration for service and manual tools. Provision
   the owner-only stable lock once. Do not replace/unlink it on each restart.
5. Start the collector once using the existing environment. With separate approval,
   perform one activity and one health smoke test, then a synthetic contention test;
   verify payload/Drive behavior and privacy-safe busy/error behavior.
6. Re-enable manual maintenance only on migrated entry points. Do not enable a
   prescription timer until the guarded reader integration and owner handoff receive
   their own review. A later lightweight oneshot worker may share this lease; full
   analytics/PDF processing remains off the Pi. No units/timers are supplied here.

## Rollback — code/service wiring only

Stop any new reader first if one is later introduced; drain Garmin calls and stop
collector/maintenance entry points. Restore the reviewed previous source and service
wiring together. Retain the CURRENT Garmin token file unchanged; never restore an
old token copy, create an alternate store, or delete the stable lock. Restart only
one legacy owner and disable all new shared-store readers until coordination is
restored. Retain immutable prescription evidence separately. No rollback action has
been executed by this development task.

## Development validation

Tests use only the standard library, fake clients and temporary synthetic files.
Run from repository root: `python3 -B -m unittest discover -s tests -v`.
Final execution counts/environment and repository checks are in VALIDATION.md.
