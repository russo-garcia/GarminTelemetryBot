# Daily health finalization

## Current optional-weight migration (local review only)

Garmin Connect is the recorded-weight acquisition source. Trusted scale readings
are entered manually by the athlete. Weight is optional; BMI is not acquired.
Renpho is retired from all operational health paths. See
[GARMIN_WEIGHT.md](GARMIN_WEIGHT.md) for the versioned grams contract, range
carry-in, missing/failure semantics and synthetic validation.

The one-date primitive accepts an optional job-local weight context. Normal
bootstrap/daily/repair jobs share at-most-45-day windows, one range query per
window, using previousDateWeight and daily latestWeight. Provisional refresh uses
one day. Failed/missing/ambiguous weight never blocks core health/sleep plus Drive
success. Core Garmin/Drive failures still stop without advancing the watermark.
Weight metadata is additive; health completeness definitions remain unchanged.

No production state/history is rewritten by this patch. Existing Renpho-sourced
weights remain historical, not relabeled. Null optional weight alone requires no
historical repair. Source review, Pi staging and deployment are separate approvals.

## Historical initial development context

The following baseline/deployment statement records the original development
phase, not the current production status. Later staging evidence is recorded in
VALIDATION.md; it does not validate the new optional-weight patch on the Pi.

Baseline: `6d550ee7cefa89395fb1f519acbe5168767b2b6c`. Production remains on
`7adf3afb4af261d88b759c0d22497e7577c255e8`. No deployment, bootstrap, live acquisition,
unit installation/activation or push is part of this work.

## Meaning and state contract

Berlin today is provisional/open. Past dates are unfinalized until a successful
post-day call to the existing `health_sync.sync_health_data(YYYY-MM-DD)` returns
exactly `True` and progress is durably recorded. Finalization is an operational
skip policy, not filesystem immutability or proof Garmin has complete physiology.
The original finalization work did not change the JSON health payload or analytics'
completeness semantics. The current optional-weight patch adds weight_acquisition
metadata and preserves that same completeness policy.

Private state lives at module-directory `.health-runtime/health_state.json`:

```json
{"schema_version": 1, "history_start": "2026-04-01", "finalized_through": null}
```

The parent is mode 0700; state and stable `health_job.lock` are mode 0600, owned
by the executing collector account. Paths are absolute and independent of CWD.
No credentials appear in state. `.health-runtime/` is ignored in Git, including
crash remnants. Dedicated directory/filenames prevent reusing `garmin_auth.lock`.
There is no environment override allowing different automatic/manual state paths.
Programmatic store injection is for synthetic tests or explicitly reviewed use.

If `finalized_through=D`, all dates from `history_start` through D have completed
the acquisition/upload path in ascending order. Null means none have. Each state
advance must be exactly the next calendar date and strictly before the captured
Berlin today. No filename/Drive-presence inference seeds the watermark. Earlier
provisional exports do not count as post-day refreshes. Schema version, exact keys,
ISO dates, duplicate keys, chronology, clock-future values and filesystem safety
are checked strictly. Invalid/corrupt/unknown-version state aborts, never resets.

State publication writes an owner-only same-directory temporary file, flushes and
fsyncs the file, atomically replaces the state path, then fsyncs the directory.
Initial private-directory creation also fsyncs its parent. No chattr/read-only
permission is applied to health exports. POSIX local filesystem durability and a
trusted owner-only directory are required; flash/storage failure remains possible.

## Explicit bootstrap and resume

`health_backfill.py` now requires `--start` and `--end`. Suggested initial start is
2026-04-01. A missing state is initialized with that explicitly supplied start and
null watermark before any acquisition. The start establishes the history scope;
choosing a later start deliberately excludes earlier history and must be reviewed.
The end must be strictly before Berlin today. No implicit through-today backfill
remains. The following is documentation ONLY, not executed in this task:

```text
<collector-venv-python> -B health_backfill.py --start 2026-04-01 --end <Berlin-yesterday-YYYY-MM-DD>
```

On existing state, `--start` may be between history_start and the next unfinalized
date. An original bootstrap command can be rerun: already-finalized dates are
skipped. A start later than the next required date is rejected; changing the saved
history_start is rejected. Dates run chronologically. First False/exception/Busy
stops the invocation without finalizing that date or visiting later ones. A null
watermark after failure resumes at history_start. An end already finalized is a
successful no-op. State is written only after the one-date primitive's successful
upload return, not after the Garmin acquisition alone.

`renpho_backfill.py` was an indirect Garmin-health backfill despite its name. It is
now a compatibility entry point for exactly this explicit bounded tool; its old
hardcoded loop was removed. It does not gain a separate Renpho/Garmin auth path.
Old no-argument maintenance calls fail with CLI usage instead of running history.

Defaults: at least 3 seconds between successful days, at most 366 successful days
and a 3600-second monotonic budget checked between operations. `--max-days` accepts
1..366, `--max-seconds` a positive finite value, `--pace-seconds` at least 3. This
is a conservative pacing policy, not a documented Garmin quota guarantee. There
are no within-run retries or automatic transient-error loops. A failed range or
budget limit reports INTERRUPTED and exit 75; another approved invocation resumes.
COMPLETE (including no-op) exits 0. Busy job ownership exits 75. Invalid state or
filesystem/configuration errors exit 2. No failed/gapped range reports COMPLETE.

An upload and local state replacement cannot form one distributed transaction.
If the process dies after Drive upload but before state commit, that date is
repeated on resume (at-least-once delivery). Existing Drive lookup/update behavior
is preserved, not upgraded to exactly-once semantics. If directory fsync fails
after replace, the new watermark may be visible: the command fails immediately;
retry must reread state. It never moves progress backward or visits a later date
in the failed invocation. Orphan temporary files are ignored, never promoted as
state; an operator may inspect/remove only known crash remnants separately.

## Repair

`health_backfill.py --repair --start D1 --end D2` refreshes an explicit inclusive
range already inside `[history_start, finalized_through]`. Missing state, dates
before history_start, unfinalized dates and today/future are refused. Successful
repairs do not write state at all or change its inode. Failed repairs stop at the
first failure and retain the previously successful contiguous prefix. A repair
failure does not erase evidence of the original successful finalization.
There is no separate repair cursor: rerunning the same range repeats its successful
repair prefix; use the returned failed_date for an explicitly bounded continuation.

## Daily job and provisional requests

`health_daily.py` computes Berlin today from an aware clock and freezes yesterday
once after taking the health-job lease. It requires an existing valid state. This
prevents accidentally enabling a timer from silently triggering the first historical
bootstrap. An initialized null watermark can resume a partial bootstrap from its
recorded history_start. Multi-day downtime catches up sequentially through yesterday,
subject to the same bounds; ranges beyond a bound need subsequent invocations.
It never requests today's provisional data. Missing IANA timezone data fails closed.
A backward clock placing the saved watermark on/after today also fails closed.

Telegram `❤️ Get Health Data` and standalone `health_sync.py` refresh only Berlin
today, clearly described as provisional/open. They share the health-job lock so a
long-running provisional upload cannot overlap a later finalizer across midnight.
They NEVER read, initialize or update the finalization state. The one-date primitive
remains an internal low-level operation (now with optional weight context): arbitrary direct
callers can bypass the new health-job lease and must not be used as normal scheduled
or historical entry points. Metric formulas remain unchanged; see the additive
weight payload metadata documented above.

## Concurrency and lock order

A nonblocking process-local mutex plus POSIX flock owns the stable health-job inode
for the full job (load -> acquisition/upload -> commit -> pacing). Competing daily,
bootstrap, repair, legacy backfill or provisional callers return busy rather than
queue or duplicate the job. The systemd singleton adds another guard for timer
invocations; the file lock handles manual processes and threads too.

Order: **health-job lease -> existing Garmin authentication lease**. The Garmin
lease includes core health/sleep and optional Garmin weight. It is released before
Drive, state commit and pacing. No Renpho operation remains. Activity sync uses Garmin only and can interleave between health dates.
GarminBusy/False never advances progress. No second Garmin client/credential-store
mechanism is introduced. Never acquire a health-job lease while holding Garmin or
prescription state ownership. Future prescription ordering remains Garmin auth ->
prescription state; no health workflow acquires prescription state. All acquisition
waits are bounded/nonblocking; jobs have no reverse lock path.

Stable job locks are never unlinked/replaced as stale cleanup. Kernel ownership
releases on termination. Fork hooks close inherited descriptors without unlocking
the parent's open-file description; inherited/expired lease handles fail. A health
state replace does not change the separate job-lock inode. Coordination is advisory:
old copied scripts or direct primitive calls can bypass it. Migrate all entry points
and restart the collector under the separately approved rollout before activation.

## Scheduling definitions and version checks

Review files: `systemd/garmin-health-daily.service.in` and
`systemd/garmin-health-daily.timer`. Render only the non-secret collector user/group
and absolute project path. Use the existing venv and the collector's same absolute
Garmin token/lock settings. No credential values or deployment-specific private
configuration are committed. Paths with whitespace require explicit systemd
escaping during review; no automatic renderer/installer is supplied.

The timer specifies `OnCalendar=*-*-* 10:00:00 Europe/Berlin`, `AccuracySec=1min`
and `Persistent=true`. It follows Berlin DST independently of the machine's local
timezone: 10:00 Berlin is 09:00 UTC in winter and 08:00 UTC in summer. A missed
calendar activation triggers one catch-up service invocation; the watermark loop,
not systemd, enumerates missing days. An already-active oneshot is not started as
a second instance. RemainAfterExit is deliberately absent; Restart=no prevents
retry storms. A busy/failing invocation is eligible for a manual approved retry or
the next daily activation. Persistent does not automatically retry failed jobs.

These semantics were checked against the upstream **systemd v252** documentation:
- [systemd.time: calendar timezone syntax](https://raw.githubusercontent.com/systemd/systemd/v252/man/systemd.time.xml)
- [systemd.timer: active unit, accuracy and persistence](https://raw.githubusercontent.com/systemd/systemd/v252/man/systemd.timer.xml)

Conservative review target: systemd 252 or newer. This is a documentation baseline,
not a claim that these features were first introduced in 252. At initial development the Pi's installed
systemd version was unknown. Subsequent credential-free staging verified version
257 and the March/October DST sequences (VALIDATION.md). macOS has no systemd
calendar parser. The documented staging procedure checks `systemd-analyze --version`, `systemd-analyze calendar --iterations=3
'*-*-* 10:00:00 Europe/Berlin'`, and `systemd-analyze verify` on the rendered units.
Check March/October DST boundaries with explicit base times and confirm installed
Europe/Berlin tzdata and synchronized wall time. Do not infer clock readiness solely
from After=time-sync.target on a Pi without a working time-sync implementation.

The service is oneshot, UMask=0077, runs as the existing collector account and
uses no new auth mechanism. TimeoutStartSec=infinity avoids deliberately killing
an in-flight SDK/auth/Drive operation. The application's elapsed-time budget is NOT
an HTTP deadline. Existing SDK timeouts are unchanged; a stalled request can block
the health job longer, requiring operator intervention. No service/timer was installed,
started, enabled or otherwise applied by this development task.

## Migration/bootstrap review sequence — not executed

1. Review source/state/range policy and the proposed 2026-04-01 history start.
2. Credential-free Pi staging: full synthetic suite, source parity, systemd/calendar
   parser/version/DST checks, filesystem fsync/flock support and timezone data.
3. Obtain separate deployment approval; stop/drain old health entrants and migrate
   all changed source together. Preserve Garmin credentials and existing stable
   Garmin lock. Do not seed state from existing Drive files. Runtime directories
   are created privately at first supported invocation; do not create a fake D.
4. Obtain separate historical bootstrap approval for 2026-04-01 through the Berlin
   yesterday chosen at execution. Run explicit bounded ranges, inspect exit/result,
   resume from the persisted prefix if interrupted. No default current date in CLI
   examples is silently substituted by UTC or by this development session.
5. Only after bootstrap/catch-up is verified, separately approve rendering,
   installation and activation of the timer. A missed startup trigger may run
   immediately with Persistent=true; review before enabling.
6. Verify a provisional request leaves state unchanged and a post-day finalizer
   advances exactly one eligible date. Do not run live checks as part of this task.

Rollback/disable: first stop new scheduled/manual health jobs safely. Preserve
state and both stable locks; never restore credentials. Older health code ignores
the watermark/job lock, so keep its historical tools and any automatic finalizer
disabled if rolling source back. State is not an acquisition credential and must
not be manually advanced to make a rollout appear complete.

## Remaining limits

A successful primitive is the finalization criterion. Empty/late Garmin fields,
Renpho carry-forward or optional missing weight are not reinterpreted; payload
completeness and later-arriving data need explicit repair. Current Drive upload
lookup/update behavior can leave ambiguity after remote success/local failure.
There is no distributed lock across machines, no fairness guarantee, no per-day
repair journal and no verified live provider rate limit. Correct wall time, local
POSIX storage and a single supported shared runtime directory are required.
