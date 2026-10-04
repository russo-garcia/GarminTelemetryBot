# Phase 2B.2 development validation

Date: 2026-10-04. Audited development baseline: main at
`a1634372ecf912a9cfe6edefef857fd7f3b73abc`, initially clean.

## Executed development checks

- Python 3.9.6 on macOS, POSIX flock implementation.
- Coordinator suite: **27 passed**.
- Collector/import/payload suite: **19 passed**.
- Complete suite: **46 passed**.
- Independent source-only copy with a fresh stdlib-only venv, no private configuration,
  no third-party packages, no source data and no prior outputs: **46 passed**.
- Python source compile checks passed without source execution or bytecode writes.
- Development repository final full-suite rerun: **46 passed**.

No real Garmin, Google, Telegram or Renpho clients were constructed. SDK behavior,
authentication and API payloads were simulated with fake clients. Every token-shaped
file used by tests was synthetic in a temporary directory. No production credential
contents were read, copied, displayed or backed up. No live network test was run.

## Concurrency evidence

Threads using separate coordinator instances serialize on the same absolute lock.
Spawned processes contend with each other and with the parent. A waiting caller
hits the bounded timeout before constructing a client or attempting login. Normal
exit, exception, construction/login failures and reader refresh-required failure
release ownership. SIGKILL releases kernel ownership without deleting the file;
the next process retains the stable lock inode. A forked child cannot use the
parent's facade or unlock its lease. Atomic replacement of the synthetic token file
does not replace the separate lock inode, and the next lease reloads new content.

Read-only readers open the existing lock O_RDONLY without creating it, never invoke
login through the coordinator, and must use the existing guarded Phase 2B.1 adapter.
The tests validate that adapter interface/handoff contract with fakes, not the real
native SDK guard implementation. No new reader worker or scheduler is implemented.

## Acquisition regression evidence

Synthetic activities preserve latest-5 selection, already-synced filtering, original
FIT download format/bytes, sidecar JSON (including indent), and sport/year/month
Drive paths. Missing activity dates preserve Unknown_Year/Unknown_Month behavior.

Health tests preserve the exact daily_stats/sleep_data/weight_metrics/date bundle,
including genuine numeric zero and null values. Renpho latest-on-or-before-date
carry-forward selection is tested independently with fictional measurements.

Activity backfill retains page size 50 and its existing June 26, 2026 date boundary.
Both health/backfill date loops retain their existing start-date semantics and enter
Garmin only through coordinated health_sync. Standalone and Telegram health paths
are exercised. Each download/date releases before Drive, Renpho and maintenance
sleeps. Threaded Telegram tests explicitly demonstrate the still-separate upload/
ledger duplication race rather than claiming auth coordination fixes it.

No canonical analytics, correction, threshold, health-semantic, forecast,
prescription projection/matching or report implementation was modified. No personal
analytics replay/report regeneration is required for this source-only collector
coordination patch, and none was performed.

## Limitations / release gate

This is development validation, not deployed ARM/Linux or real SDK integration
validation. The Pi's Python 3.13.5 / garminconnect 0.3.16 environment remains untouched.
A credential-free staging test there and separately approved bounded live smoke
checks are required before production rollout. The auth lock is advisory and requires
all collector/manual Garmin entrants to migrate together. See GARMIN_COORDINATION.md
for residual Drive/ledger races, API compatibility changes, rollout and rollback.

No commit, push, service change, timer or deployment is part of this pass.
