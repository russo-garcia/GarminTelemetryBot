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

## Successful Raspberry Pi credential-free staging and source freeze

The exact reviewed implementation was frozen, without source/test edits, in
`7adf3afb4af261d88b759c0d22497e7577c255e8`:
`Coordinate Garmin authentication across collector and maintenance paths`.
Its parent is the audited baseline `a1634372ecf912a9cfe6edefef857fd7f3b73abc`.
This validation update is a separate documentation-only commit.

User-returned live staging evidence established:

- Raspberry Pi architecture: **armv7l**; Python: **3.13.5**.
- Installed **garminconnect 0.3.16**: static source/interface compatibility PASS.
  The package was not imported and no real Garmin client was constructed.
- All **46 reviewed tests passed in 4.814 seconds**, including threads, spawned
  processes, contention timeout, SIGKILL release, fork/inherited descriptors,
  stable lock inode, O_RDONLY reader, fresh loading, expired facade, activity/health
  payloads, all backfill entry paths and import-side-effect checks.
- All **11 reviewed file hashes** matched before and after execution. The two
  unchanged backfill support files matched the audited baseline. Ten Python source
  files compiled; **zero bytecode files** were created.
- No production credential/config contents were opened, no authentication/API call
  was made, and no production services/schedulers were invoked or changed.
- Diagnostic writes were confined to the unique isolated staging run directory.
  Ordinary SSH/OS bookkeeping and independent existing production operations are
  outside that assertion.

Evidence identifiers (no private runtime material is included in Git):

- Original review bundle SHA-256:
  `c05cf9010c03370194caba3d3c37e55f9ea2663f71c6c17c97874546414c49b0`.
- Complete user-returned successful staging transcript SHA-256:
  `25b59d2d6a661403a8cbf4c4c3e9ae43da0b76c3574af75b68f16bc9736a5a2f`.
- Implementation commit file blobs are the preserved source-hash reference.

An initial Pi run passed 45 tests but the temporary validation harness blocked
`os.posix_spawn` used by the intentional fresh-Python import test. No implementation
assertion failed. Only the temporary harness and its safety tests were corrected:
its subprocess/posix_spawn exception requires the exact staging interpreter,
arguments, inherited environment and SHA-256 of that reviewed child code. Twenty-five
harness safety tests passed, explicitly exercising posix_spawn and denial of arbitrary
processes, private reads, outside writes, network and real SDK construction. The
unchanged 46-test suite then passed locally and on the Pi. Temporary harness files
are not collector source and are not part of this repository.

## Limitations / release gate

Credential-free ARM/Linux staging is complete. This is not a production deployment
or live Garmin integration test; static result annotations cannot prove live API
payload behavior. No packages were installed/upgraded on the Pi.

Source freeze and this documentation commit do not authorize deployment, service
restart, credential access, live smoke tests, pushes or a prescription worker/timer.
Current production metadata and maintenance quiescence must be checked for the
controlled rollout. The old collector has no reliable read-only in-flight-request
indicator: service state, thread counts or a closed token descriptor cannot prove
that a request is idle. Do not describe process termination as graceful SDK drain.

All collector/manual entrants must migrate together. Keep the current token store
in place and preserve the separate stable lock inode across deployment/rollback.
See GARMIN_COORDINATION.md for the still-separate Drive/ledger races and API changes.
The next operational boundary is explicit controlled-deployment approval, followed
by separate approval for any live Garmin smoke test.

## Daily health finalization — local development validation (2026-10-04)

This later phase is a local, uncommitted review patch based on
`6d550ee7cefa89395fb1f519acbe5168767b2b6c`. It does not alter the previously frozen
Phase 2B.2 source or its historical Pi test evidence. The user reports Phase 2B.2
activity and health live smoke tests passed; no new live operations were performed
for this health-finalization phase.

- macOS Python 3.9.6: **94/94 tests pass**.
- Independent source-only directory, fresh stdlib-only venv (no pip/packages/private
  configuration/runtime files): **94/94 tests pass**.
- Original 46 collector/coordinator test cases retained. `test_garmin_auth.py` is
  byte-for-byte unchanged. Three existing entry-path tests now use explicit past
  ranges/Berlin clock injection: the removed no-argument through-today behavior is
  intentionally no longer accepted. Both-date acquisition/login/lease assertions
  remain; COMPLETE/exit assertions were added. Test runtime directories are synthetic.
- Added **46 health-state/workflow tests** and **2 timer-contract/DST tests**.
- Coverage includes strict/corrupt state, contiguous prefix, first failure, resume,
  Drive failure, GarminBusy, today exclusion, already-caught-up no-op, downtime,
  repair, job bounds/pacing, Berlin DST/UTC boundaries, private filesystem checks,
  file/directory fsync and replace failures, a killed synthetic process between
  upload and state commit, spawned-process/thread exclusion, fork descriptor safety,
  stable lock inode, expired handles, lock order and provisional/no-state paths.
- Import tests preserve the original fresh-process no-configuration/no-service
  check and add guarded imports of the new modules. No production SDK client is
  constructed. Garmin, Drive, Renpho and Telegram operations use fakes/mocks.
- AST comparison confirms `health_sync.sync_health_data` itself is unchanged.
  Its standalone today wrapper changes; existing health payload assertions pass.
- `garmin_auth.py`, `collector_runtime.py`, activity `backfill.py`, `renpho_sync.py`
  and `tests/test_garmin_auth.py` remain unchanged against the baseline.
- All Python source is compiled in memory without writing bytecode.
- Timer syntax/behavior was researched in upstream systemd v252 documentation.
  The two local timer tests check the template contract and Berlin DST conversion;
  they are **not** a systemd parser or Pi validation. Installed Pi systemd version,
  rendered-unit verification and calendar/DST parser checks remain for separately
  approved credential-free staging.

No production source/configuration/service was touched. No SSH, credential reads,
Garmin/Drive/Renpho calls, historical acquisition, deployment, timer installation,
activation or push was performed. Synthetic ranges are fixtures, not personal data.
The implementation is ready for local review before Pi staging, not approved for
activation. See [HEALTH_FINALIZATION.md](HEALTH_FINALIZATION.md) for invariants,
limits, concurrency, bootstrap/repair and the staged migration/rollback procedure.
