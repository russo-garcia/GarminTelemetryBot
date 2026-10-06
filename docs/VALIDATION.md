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


## Daily health finalization — successful Raspberry Pi staging and source freeze (2026-10-05)

The earlier local-development section is a historical checkpoint. Credential-free
Pi staging is now complete, including the corrected calendar-only verification.
This record does not authorize deployment, acquisition, historical bootstrap,
service changes, timer installation/activation or a push.

Exact Pi-validated implementation commit:
`9465dc06da1431a07862679ee4f6183e5eeaf657` — `Add contiguous daily health finalization`.

All 16 reviewed feature files were committed without edits after staging. The
implementation commit's full file bytes match both the review-bundle SHA-256
manifest and the returned Pi staging manifest. Five supporting baseline components
remain unchanged: `garmin_auth.py`, `collector_runtime.py`, activity `backfill.py`,
`renpho_sync.py`, and `tests/test_garmin_auth.py`. This appended record is made in a
separate documentation-only commit; it does not alter the validated implementation.

### User-returned Pi evidence

- Architecture: **armv7l**.
- Python: **3.13.5**.
- systemd: **257**.
- Complete reviewed suite: **94/94 tests passed** (46 retained collector/coordinator,
  46 health-state/workflow, 2 timer-contract/DST tests).
- Additional synthetic filesystem/state checks: **17/17 passed**.
- Temporary rendered-unit verification: **passed**, return code 0. No unit installed
  or activated; generators and man invocation disabled.
- Source hash parity before and after validation: **true**; all 21 source/support
  files matched. Bytecode persistence: disabled; zero bytecode files reported.
- Authentication calls: **0**.
- External API calls: **0**.
- Production credentials opened: **false**.
- Production services invoked: **false**.
- Production source modified: **false**.
- Initial staging writes remained under its isolated temporary staging tree.
- Calendar-only rerun diagnostic writes: **0**; application imports: **0**;
  production files opened: **false**; service actions: **0**.

### Calendar parser correction and verified UTC results

The initial overall staging result was blocked solely by the temporary harness:
its parser recognized `Iter. #N`, whereas systemd 257 returned `Iteration #N`.
Only the first occurrence was retained, so the expected three-occurrence checks
failed despite all calendar commands returning zero. No application, reviewed test,
service template or timer file changed. The temporary parser now accepts both label
forms; 47 local harness safety/regression tests passed. Original staging evidence
was preserved.

The corrected credential-free calendar-only rerun revalidated the 21 staged file
hashes before/after and passed on the same Pi with systemd 257. The original 94-test,
17-check and temporary-unit results were retained rather than unnecessarily rerun.
All three calendar commands returned 0 with three occurrences. The exact expression
`*-*-* 10:00:00 Europe/Berlin` is **validated**.

| Check | Verified occurrences (UTC) |
|---|---|
| Current-date sequence at rerun | 2026-10-05 08:00; 2026-10-06 08:00; 2026-10-07 08:00 |
| March transition | 2026-03-28 09:00; 2026-03-29 08:00; 2026-03-30 08:00 |
| October transition | 2026-10-24 08:00; 2026-10-25 09:00; 2026-10-26 09:00 |

These values preserve 10:00 Europe/Berlin on both sides of each DST change.
The March command used base time 2026-03-27 12:00:00 UTC; October used
2026-10-23 12:00:00 UTC. Both requested three iterations. Final corrected result:

**HEALTH FINALIZATION PI STAGING VALIDATED — READY FOR SOURCE FREEZE/DEPLOYMENT REVIEW**

### Validation provenance and scope

- Required prior Git baseline: `6d550ee7cefa89395fb1f519acbe5168767b2b6c`.
- `HEALTH_FINALIZATION_REVIEW.md` SHA-256:
  `7b0a55b1545a488e7df6a81016c27a37945a7aa175b8f6d225de4cf9cf2d52ac`.
- Original user-returned full Pi staging transcript SHA-256:
  `d160b696d3bddb9d8d94d9b1e2e8357253e935f88c92df00070495d9f4fdceb0`.
- Corrected calendar-only evidence: user-returned terminal JSON in the source-freeze
  request, confirming all calendar gates passed and source parity remained true.
- Private transcripts and temporary staging harnesses remain outside Git. The
  unrelated `.DS_Store` is excluded, untracked and untouched.

This is a source freeze and validation record only. No production changes, live
Garmin/Drive/Renpho/Telegram operations, historical backfill, timer activation or
push took place during source freeze. Deployment planning awaits separate approval.


## Garmin-only optional weight — local development validation, 2026-10-06

Baseline: `22f7126d414adaf64a26c6819637be47975c5034`. Uncommitted local
implementation only; no Pi access, deployment, bootstrap, repair, timer activation,
live API calls, commit or push in this validation. The unrelated .DS_Store remains
untracked and untouched. Prior phase counts above are historical, not this patch.

Commands from the canonical collector repository:

- `python3 -B -m unittest discover -s tests -v`: **142/142 PASS**, macOS Python 3.9.6.
- Same command in an independent source-only copy with a new stdlib-only venv:
  **142/142 PASS**. Only tracked source/tests/docs/unit templates plus this patch's
  new source/tests/doc were copied; no credentials, runtime files, private data or
  prior outputs. Source parity verified before/after.
- In-memory `compile(source_bytes, filename, 'exec')` for every Python source/test:
  PASS, no bytecode persistence. Fresh-process import-safety tests pass with file
  opening/network construction forbidden and no real provider SDK imported.
- `git diff --check`: PASS.

Test accounting: prior 94 test purposes retained; the obsolete Renpho carry-forward
fixture is migrated to Garmin carry-in, expected BMI is now null, and the health
payload/lease assertion reflects weight inside Garmin ownership. Added 48 tests:
25 versioned parser/window tests, 21 health integration/retirement tests, and two
owner-facade tests. No authentication concurrency/state/timer guarantees are removed.

Synthetic coverage includes exact envelope; previous carry-in; Garmin latestWeight
selection with multiple records; date/ID consistency; duplicate summaries; invalid
units/schema/nonfinite mass; no future look-ahead; carry barriers; 45/46-day query
bounds and resume; optional error caching; no per-day fan-out; kg conversion and
BMI null; discarded IDs/impedance; core/Drive/busy stop behavior; repair/provisional
state identity; no Renpho import/call from bootstrap/daily/repair/today/legacy alias;
retired entry no config/SDK access; and exception/payload redaction. Tests use fake
providers and temporary synthetic state only. Existing configuration/credentials
are not modified or read by these validation runs.

EnduranceAnalytics compatibility: **3/3 synthetic end-to-end cases PASS** on its
canonical Python 3.12 environment, without analytical source changes. For each of
measured, carried and missing optional weight, four fictional health files flowed
through the actual canonical engine, health normalization, metric catalog, report
projection, and Report v2 build_model/validate_model. BMI remained MISSING, never
zero; measurement dates/carry flags survived. Measured case exposed four report
observations, carried/missing exposed zero as existing report policy requires.
The new collector parser produced each input. No personal config/data or previous
outputs were loaded; no PDF or personal report was rendered/published. This is a
compatibility check, not a rerun of the entire analytics test suite.

Static grams evidence was recorded before implementing conversion: the Go Garmin
range model documents grams and an independent Python Garmin MCP range consumer
converts raw weight / 1000. Exact references/hashes and the distinction from the
unit-less live payload are in GARMIN_WEIGHT.md. There is no claim of a public
Garmin service schema guarantee. Pi staging must validate this new source separately.

Historical files and paused finalization state are untouched. No June 6 repair is
needed merely because optional weight was null. Obsolete production Renpho config
keys and package removal remain a separately approved hardening task.
