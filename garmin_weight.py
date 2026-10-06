"""Optional Garmin recorded weight. Pure projection; no SDK/config I/O on import.

Unit/selection evidence and limitations: docs/GARMIN_WEIGHT.md.
Only projected kg/date survive parsing; no raw response or sample ID is cached.
"""
from datetime import date, timedelta
import math
import re

CONTRACT = 'garmin-weight-range/1.0.0'
WINDOW_DAYS = 45
MIN_KG, MAX_KG = 10.0, 500.0


class InvalidWeight(ValueError):
    def __init__(self, outcome='INVALID_RESPONSE'):
        self.outcome = outcome
        super().__init__(outcome)


def calendar_day(value):
    if type(value) is not str or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise InvalidWeight()
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise InvalidWeight() from None


def _units(obj):
    for key in ('unit', 'units', 'unitKey', 'weightUnit', 'weightUnitKey', 'massUnit'):
        if key in obj and obj[key] not in ('g', 'gram', 'grams', 'G', 'GRAM'):
            raise InvalidWeight()


def _empty_previous(obj):
    """Recognize only explicit null-filled known record fields, never partial data."""
    known = {'calendarDate', 'weight', 'sourceType', 'samplePk', 'date', 'timestampGMT',
             'bmi', 'bodyFat', 'bodyWater', 'boneMass', 'muscleMass', 'visceralFat',
             'metabolicAge', 'physiqueRating', 'unit', 'units', 'unitKey',
             'weightUnit', 'weightUnitKey', 'massUnit'}
    return (type(obj) is dict and {'calendarDate', 'weight'} <= obj.keys()
            and obj.keys() <= known and all(value is None for value in obj.values()))


def _measurement(obj):
    if type(obj) is not dict:
        raise InvalidWeight()
    _units(obj)
    day = calendar_day(obj.get('calendarDate'))
    grams = obj.get('weight')
    if type(grams) not in (int, float):
        raise InvalidWeight()
    try:
        kg = grams / 1000
        if not math.isfinite(kg) or not MIN_KG <= kg <= MAX_KG:
            raise InvalidWeight()
    except (OverflowError, TypeError):
        raise InvalidWeight() from None
    return day, kg


def _selected(summary, day):
    _units(summary)
    count = summary.get('numOfWeightEntries')
    rows = summary.get('allWeightMetrics')
    latest = summary.get('latestWeight')
    if (type(count) is not int or count < 1 or type(rows) is not list
            or len(rows) != count or not all(type(r) is dict for r in rows)):
        raise InvalidWeight()
    observation = _measurement(latest)
    if observation[0] != day:
        raise InvalidWeight('AMBIGUOUS')
    if 'samplePk' in latest and latest['samplePk'] is not None:
        identity = latest['samplePk']
        if type(identity) is not int or identity <= 0:
            raise InvalidWeight()
        matches = [r for r in rows if type(r.get('samplePk')) is int and r['samplePk'] == identity]
        if len(matches) != 1 or _measurement(matches[0]) != observation:
            raise InvalidWeight('AMBIGUOUS')
    return observation


def parse_window(raw, start, end):
    """Map dates to (projected measurement or None, fixed outcome).

    Errors with an identifiable date form a carry barrier. Unplaceable errors
    invalidate the window, since their temporal effect cannot be established.
    """
    if (type(raw) is not dict or not {'dailyWeightSummaries', 'previousDateWeight',
            'nextDateWeight', 'totalAverage'} <= raw.keys()
            or type(raw['dailyWeightSummaries']) is not list):
        raise InvalidWeight()
    _units(raw)
    current, unavailable = None, 'NO_OBSERVATION'
    if raw['previousDateWeight'] is not None and not _empty_previous(raw['previousDateWeight']):
        try:
            current = _measurement(raw['previousDateWeight'])
            if current[0] >= start:
                raise InvalidWeight()
        except InvalidWeight:
            current, unavailable = None, 'INVALID_RESPONSE'
    events = {}
    for summary in raw['dailyWeightSummaries']:
        if type(summary) is not dict:
            raise InvalidWeight()
        day = calendar_day(summary.get('summaryDate'))
        if not start <= day <= end:
            raise InvalidWeight()
        if day in events:
            events[day] = (None, 'AMBIGUOUS')
            continue
        try:
            events[day] = (_selected(summary, day), None)
        except InvalidWeight as exc:
            events[day] = (None, exc.outcome)
    result = {}
    day = start
    while day <= end:
        if day in events:
            current, unavailable = events[day]
        if current is None:
            result[day] = (None, unavailable)
        else:
            measured, kg = current
            carried = measured < day
            result[day] = ({'weight': kg, 'bmi': None, 'is_carried_forward': carried,
                            'last_measured_date': measured.isoformat()},
                           'CARRIED' if carried else 'MEASURED')
        if day == end:
            break
        day += timedelta(days=1)
    return result


def metadata(outcome, start, end):
    return {'contract': CONTRACT, 'source': 'garmin_connect', 'outcome': outcome,
            'range_start': start.isoformat(), 'range_end': end.isoformat()}


class WeightContext:
    """One invocation's bounded lazy cache; caller supplies an owned facade.

    Never retains a facade/client. Fetch only after core stats/sleep succeeded.
    """
    def __init__(self, end):
        self.end = calendar_day(end)
        self._start = self._end = None
        self._results = {}

    def for_day(self, client, date_str):
        day = calendar_day(date_str)
        if day > self.end:
            return None, metadata('INVALID_RESPONSE', day, day)
        if self._start is None or not self._start <= day <= self._end:
            self._start = day
            self._end = day + timedelta(days=min(WINDOW_DAYS - 1, (self.end - day).days))
            try:
                raw = client.get_weigh_ins(self._start.isoformat(), self._end.isoformat())
            except Exception:
                self._results = {}
                self._failure = 'FETCH_FAILED'
            else:
                try:
                    self._results = parse_window(raw, self._start, self._end)
                    self._failure = None
                except Exception:
                    self._results = {}
                    self._failure = 'INVALID_RESPONSE'
        weight, outcome = self._results.get(day, (None, self._failure))
        return None if weight is None else dict(weight), metadata(outcome, self._start, self._end)
