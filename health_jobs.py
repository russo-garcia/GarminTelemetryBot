"""Bounded health workflows. No credentials, network or state I/O on import."""
from datetime import datetime, timedelta, timezone
import json
import math
import time
from garmin_auth import GarminBusy
from health_state import HealthStore, HealthStateError, HealthJobBusy, parse_date


def berlin_today(now=None):
    now = datetime.now(timezone.utc) if now is None else now
    if now.tzinfo is None or now.utcoffset() is None:
        raise HealthStateError('An aware clock is required')
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        zone = ZoneInfo('Europe/Berlin')
    except ZoneInfoNotFoundError:
        raise HealthStateError('Europe/Berlin timezone data is unavailable') from None
    return now.astimezone(zone).date()


def default_store():
    return HealthStore()


def _sync(day, sync, weight_context=None):
    if sync is None:
        from health_sync import sync_health_data
        sync = sync_health_data if weight_context is None else (
            lambda date_str: sync_health_data(date_str, weight_context=weight_context))
    try:
        return 'OK' if sync(day.isoformat()) is True else 'SYNC_FAILED'
    except GarminBusy:
        return 'GARMIN_BUSY'
    except Exception:
        return 'SYNC_FAILED'  # Never print raw SDK/Drive exception bodies.


def _result(status, count, state, end, reason=None, failed_date=None):
    return {'status': status, 'completed_dates': count,
            'finalized_through': state.finalized_through.isoformat() if state.finalized_through else None,
            'target_end': end.isoformat(), 'reason': reason,
            'failed_date': failed_date.isoformat() if failed_date else None}


def run_range(*, start=None, end=None, repair=False, daily=False, store=None,
              now=None, sync=None, pace_seconds=3.0, max_days=366,
              max_seconds=3600.0, sleep=time.sleep, monotonic=time.monotonic):
    """One job lease covers state -> sync/upload -> state commit -> pacing.

    No retries within an invocation. False/busy stops at that date. Runtime bound
    is checked between operations; it cannot interrupt an in-flight HTTP call.
    """
    if (type(max_days) is not int or not 1 <= max_days <= 366
            or not math.isfinite(pace_seconds) or pace_seconds < 3
            or not math.isfinite(max_seconds) or max_seconds <= 0):
        raise HealthStateError('Invalid pacing or job bounds')
    if daily and (start is not None or end is not None or repair):
        raise HealthStateError('Daily finalization does not accept a repair range')
    first = None if daily else parse_date(start)
    last = None if daily else parse_date(end)
    if not daily and first > last:
        raise HealthStateError('Start must not follow end')
    store = default_store() if store is None else store
    with store.lease() as lease:
        today = berlin_today(now)  # Freeze the calendar cutoff after obtaining ownership.
        last = today - timedelta(days=1) if daily else last
        if last >= today:
            raise HealthStateError('Today and future dates cannot be finalized or repaired')
        state = lease.load(today)
        if state is None:
            if daily or repair:
                raise HealthStateError('Missing state: explicit bootstrap is required')
            state = lease.initialize(first, today)
        if repair:
            if first < state.history_start or state.finalized_through is None or last > state.finalized_through:
                raise HealthStateError('Repair is restricted to already-finalized dates')
            current = first
        else:
            if not daily and (first < state.history_start or first > state.next_date):
                raise HealthStateError('Requested range would skip history or change history_start')
            current = state.next_date
        weight_context = None
        if sync is None and current <= last:
            from garmin_weight import WeightContext
            window_end = current + timedelta(days=min(max_days - 1, (last - current).days))
            weight_context = WeightContext(window_end.isoformat())
        count = 0
        deadline = monotonic() + max_seconds
        while current <= last:
            if count >= max_days or monotonic() >= deadline:
                return _result('INTERRUPTED', count, state, last, 'JOB_LIMIT')
            outcome = _sync(current, sync, weight_context)
            if outcome != 'OK':
                return _result('INTERRUPTED', count, state, last, outcome, current)
            if not repair:
                # If publication/durability fails, abort. Do not undo a successful
                # replace; rerun must read disk and may safely repeat an upload.
                state = lease.advance(current, today)
            count += 1
            current += timedelta(days=1)
            if current <= last and count < max_days:
                remaining = deadline - monotonic()
                if remaining < pace_seconds:
                    return _result('INTERRUPTED', count, state, last, 'JOB_LIMIT')
                sleep(pace_seconds)  # Garmin lease was released by the one-date primitive.
        return _result('COMPLETE', count, state, last)


def refresh_today(*, store=None, now=None, sync=None):
    """Provisional only; serialize health uploads but NEVER load or write state."""
    store = default_store() if store is None else store
    with store.lease():
        today = berlin_today(now)
        return today.isoformat(), _sync(today, sync) == 'OK'


def cli_result(call):
    try:
        result = call()
    except HealthJobBusy:
        print(json.dumps({'status': 'BUSY', 'reason': 'HEALTH_JOB_ACTIVE'}))
        return 75
    except (HealthStateError, OSError, ValueError):
        print(json.dumps({'status': 'FAILED', 'reason': 'STATE_OR_CONFIGURATION_ERROR',
                          'action': 'Inspect state before retry; never reset it automatically'}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result['status'] == 'COMPLETE' else 75


def add_bounds(parser):
    parser.add_argument('--pace-seconds', type=float, default=3.0)
    parser.add_argument('--max-days', type=int, default=366)
    parser.add_argument('--max-seconds', type=float, default=3600.0)
