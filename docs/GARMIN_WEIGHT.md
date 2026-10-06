# Garmin-only optional recorded weight

Contract `garmin-weight-range/1.0.0`, first implemented 2026-10-06.
This is a local development patch, not a production deployment record.

## Unit evidence established before conversion implementation

The operator-run sanitized 0.3.16 range/day probes established field names,
containers, numeric weight, ISO calendarDate, sourceType MANUAL and daily
latestWeight/allWeightMetrics nesting. The live response contains **no explicit
weight unit**. Its numeric magnitude is not unit evidence.

Two independently implemented public clients corroborate the read-side grams
contract (not two forks counted as independent evidence):

1. [Go Garmin v0.1.0, weight_svc.go](https://github.com/jylitalo/go-garmin/blob/v0.1.0/weight_svc.go)
   (Go implementation, inherited from harrybrwn/go-garmin): `WeighIn.Weight` is
   explicitly documented in grams; `WeightService.Range` reads the same
   `/weight-service/weight/range/{start}/{end}` endpoint into WeightRange, with
   DailyWeightSummaries, PreviousDateWeight and NextDateWeight. Source SHA-256:
   `dd3a7aa8471d0155a5e9e3b7464ae2015e076ec96a9d3fae7302231da590cf43`.
2. [Taxuspt Garmin MCP weight_management.py](https://github.com/Taxuspt/garmin_mcp/blob/cfc5d799ab0f165e837f1188a1d093c65838aaf7/src/garmin_mcp/weight_management.py)
   (Python consumer, independent of that Go model): `get_weigh_ins` traverses
   dailyWeightSummaries/allWeightMetrics, names raw weight `weight_grams` and
   computes weight_kg by dividing by 1000. Its daily consumer does the same.
   Source SHA-256:
   `684f1f612e9704e1fe9435cadedfd469006f0eef04ea794cce07b22f4b5e69e8`.

These are public implementation corroboration, not a Garmin official service
specification. The versioned adapter uses kg = grams / 1000 without rounding.
A 10–500 kg inclusive adult-collector corruption guard rejects implausible output;
it never selects units from magnitude. Null/bool/string/nonfinite weights are
invalid. Recognized explicit unit fields, if introduced, must agree with grams.
An unchanged-looking undocumented server unit change cannot be perfectly detected;
this assumption remains part of the versioned contract.

## Selection and storage

The envelope requires dailyWeightSummaries (list), previousDateWeight (record or
null), nextDateWeight and totalAverage. Next/average/composition values are never
used. Added unrelated fields are ignored; missing/type-changed required structure
fails soft. Each daily summary requires ISO summaryDate, positive integer
numOfWeightEntries, object latestWeight and list allWeightMetrics of matching size.
Selected calendarDate must equal summaryDate. Duplicate summaries are ambiguous.
If selected samplePk is present, matching evidence must occur exactly once in
allWeightMetrics and agree in date/weight. Identifiers are transient cross-checks
only; they are not stored. Timestamp order is never inferred from date or
 timestampGMT. Garmin's latestWeight is authoritative within an internally
consistent summary. PreviousDateWeight must precede the requested range.

A no-previous-observation sentinel is recognized only in previousDateWeight:
JSON null, or a plain object explicitly containing calendarDate and weight, with
**every supplied value null** and no keys outside this known record-field set:
calendarDate, weight, sourceType, samplePk, date, timestampGMT, bmi, bodyFat,
bodyWater, boneMass, muscleMass, visceralFat, metabolicAge, physiqueRating, unit,
units, unitKey, weightUnit, weightUnitKey, massUnit. The minimal two-key null object
and a full known-field null record therefore yield NO_OBSERVATION. Missing required
keys, empty objects, unknown keys (even null), non-null metadata, partially populated
or malformed measurement fields remain INVALID_RESPONSE. This is not applied to
latestWeight: an allegedly selected daily measurement must still be valid. A later
valid in-range observation establishes MEASURED and subsequent CARRIED normally.

A bad selected day clears carry-forward until the next valid observation. A bad
carry-in similarly leaves earlier days unavailable until valid in-range evidence.
Malformed/unplaceable summaries or out-of-range dates invalidate the whole window.
No averaging, interpolation, nextDateWeight look-ahead, or Renpho fallback.

Only the projected kg/date survive the parser. Output remains:
`weight_metrics = {weight, bmi: null, is_carried_forward, last_measured_date}`,
or null. A versioned weight_acquisition sibling identifies Garmin Connect, fixed
outcome, and query bounds. Outcomes: MEASURED, CARRIED, NO_OBSERVATION,
FETCH_FAILED, INVALID_RESPONSE, AMBIGUOUS. No source IDs, raw responses, timestamps,
provider BMI or body-composition values are retained. The user records trusted
scale readings manually in Garmin Connect; other Garmin recorded sources are not
silently rejected by sourceType. BMI is a future downstream derived metric, not
acquired here.

## Acquisition/finalization

A job-local WeightContext lazily makes one range request per at-most-45-calendar-
day window under the **existing** Garmin lease, after core health/sleep succeeds.
previousDateWeight supplies carry-in without a lookback request. Max-days and
range end bound the last window. A longer job fetches its next window only when
needed. Errors are cached for the window rather than retried per date. Standalone
and provisional refreshes use one-day windows. No daily weigh-in API is exposed
by the production facade. No persistent weight cache, new credential store or
new client/authentication path is added.

Lock order remains health job -> Garmin. Garmin ownership is released before
Drive/state commit/pacing. Missing/bad/ambiguous weight or a weight API failure
never blocks an otherwise successful health/sleep+Drive day. Core Garmin busy,
health/sleep failure and Drive failure retain their previous stop/no-advance
semantics. Garmin null/empty health responses remain accepted. Optional failures
are reported by fixed status only; no provider exception text or payload logs.

## Retirement and history

health_sync no longer imports/calls Renpho. renpho_sync is a fail-closed retired
entry with no SDK/config/network access; renpho_backfill remains only its existing
explicit bounded whole-health alias, now Garmin-only. Installed Renpho dependencies
and obsolete config keys remain dormant until separately approved hardening.
Do not delete credentials or uninstall dependencies with this source patch.

Historical April–June files retain their original provenance and contents.
Watermark 2026-06-29 is not reset/reseeded; June 6 needs no repair just for null
optional weight. No analytics code, historical BMI, forecast or report is rewritten.
Current analytics accepts measured/carried/missing weight with null BMI. Its
measured-weight charts intentionally omit carried rows; do not mark a carried
observation as newly measured to display it.

Next steps after source review: independent credential-free Pi staging, source
freeze, separately approved source deployment, then separately approved bootstrap
resume. No timer activation, live call, bootstrap or repair is implied here.
