"""Synthetic versioned range fixtures; no provider or personal data."""
from copy import deepcopy
from datetime import date, timedelta
from types import SimpleNamespace
import unittest
from garmin_weight import WeightContext, CONTRACT


def measurement(day='2026-04-01', grams=70000, identity=101):
    return {'calendarDate':day,'weight':grams,'samplePk':identity,'sourceType':'MANUAL',
            'date':999,'timestampGMT':1,'bmi':23,'bodyFat':20,'bodyWater':50,
            'boneMass':3,'muscleMass':40,'visceralFat':1,'metabolicAge':40,'physiqueRating':5}


def summary(day='2026-04-01', grams=70000):
    m=measurement(day,grams)
    return {'summaryDate':day,'numOfWeightEntries':1,'latestWeight':m,'allWeightMetrics':[deepcopy(m)]}


def response(rows=None, previous=None):
    return {'dailyWeightSummaries':[] if rows is None else rows,'previousDateWeight':previous,
            'nextDateWeight':None,'totalAverage':{}}


class WeightTests(unittest.TestCase):
    def project(self, raw, day='2026-04-01', end=None):
        return WeightContext(end or day).for_day(SimpleNamespace(get_weigh_ins=lambda *_:raw),day)
    def test_exact_shape_and_grams_conversion(self):
        w,m=self.project(response([summary(grams=72345)]))
        self.assertEqual(w,{'weight':72.345,'bmi':None,'last_measured_date':'2026-04-01','is_carried_forward':False})
        self.assertEqual(m,{'source':'garmin_connect','contract':CONTRACT,'outcome':'MEASURED','range_start':'2026-04-01','range_end':'2026-04-01'})
    def test_prior_carry_in_keeps_date(self):
        w,m=self.project(response(previous=measurement('2026-03-10')))
        self.assertEqual(w['last_measured_date'],'2026-03-10');self.assertTrue(w['is_carried_forward']);self.assertEqual(m['outcome'],'CARRIED')
    def test_same_day_supersedes_prior(self):
        w,m=self.project(response([summary(grams=71000)],measurement('2026-03-10')))
        self.assertEqual(w['weight'],71);self.assertEqual(m['outcome'],'MEASURED')
    def test_carry_across_days_one_query(self):
        calls=[];raw=response([summary()])
        c=SimpleNamespace(get_weigh_ins=lambda *a:calls.append(a) or raw);ctx=WeightContext('2026-04-03')
        for i in range(1,4):
            w,m=ctx.for_day(c,f'2026-04-0{i}');self.assertEqual(w['last_measured_date'],'2026-04-01')
            self.assertEqual(m['outcome'],'MEASURED' if i==1 else 'CARRIED')
        self.assertEqual(calls,[('2026-04-01','2026-04-03')])
    def test_multiple_measurements_uses_latest_not_timestamp_order(self):
        s=summary();older=measurement(grams=69000,identity=102);older['timestampGMT']=999999
        s['allWeightMetrics']=[older,deepcopy(s['latestWeight'])];s['numOfWeightEntries']=2
        self.assertEqual(self.project(response([s]))[0]['weight'],70)
    def test_unordered_days_are_calendar_ordered(self):
        ctx=WeightContext('2026-04-03');c=SimpleNamespace(get_weigh_ins=lambda *_:response([summary('2026-04-03',72000),summary()]))
        self.assertEqual(ctx.for_day(c,'2026-04-01')[0]['weight'],70)
        self.assertEqual(ctx.for_day(c,'2026-04-02')[0]['weight'],70)
        self.assertEqual(ctx.for_day(c,'2026-04-03')[0]['weight'],72)
    def test_date_disagreement_ambiguous(self):
        s=summary();s['latestWeight']['calendarDate']='2026-04-02'
        self.assertEqual(self.project(response([s]))[1]['outcome'],'AMBIGUOUS')
    def test_identity_disagreement_ambiguous(self):
        s=summary();s['latestWeight']['samplePk']=999
        self.assertEqual(self.project(response([s]))[1]['outcome'],'AMBIGUOUS')
    def test_duplicate_identity_ambiguous(self):
        s=summary();s['allWeightMetrics']*=2;s['numOfWeightEntries']=2
        self.assertEqual(self.project(response([s]))[1]['outcome'],'AMBIGUOUS')
    def test_identifier_absence_supported_not_stored(self):
        s=summary();del s['latestWeight']['samplePk'];del s['allWeightMetrics'][0]['samplePk']
        self.assertEqual(self.project(response([s]))[1]['outcome'],'MEASURED')
    def test_duplicate_summaries_ambiguous(self):
        self.assertEqual(self.project(response([summary(),summary()]))[1]['outcome'],'AMBIGUOUS')
    def test_invalid_day_blocks_stale_carry_until_new_valid_day(self):
        s=summary('2026-04-02');s['latestWeight']['weight']=None
        c=SimpleNamespace(get_weigh_ins=lambda *_:response([summary(),s,summary('2026-04-04')]))
        ctx=WeightContext('2026-04-04')
        self.assertIsNotNone(ctx.for_day(c,'2026-04-01')[0])
        for d in ('2026-04-02','2026-04-03'):self.assertIsNone(ctx.for_day(c,d)[0])
        self.assertEqual(ctx.for_day(c,'2026-04-04')[1]['outcome'],'MEASURED')
    def test_nonfinite_and_invalid_mass_fail_soft(self):
        for value in (True,False,None,'70000',float('nan'),float('inf'),-1,0,70,10000000,10**1000):
            with self.subTest(type=type(value).__name__):
                w,m=self.project(response([summary(grams=value)]));self.assertIsNone(w);self.assertEqual(m['outcome'],'INVALID_RESPONSE')
    def test_mass_guard_boundaries(self):
        for grams in (10000,500000):self.assertIsNotNone(self.project(response([summary(grams=grams)]))[0])
    def test_explicit_conflicting_unit_invalid(self):
        for level in ('range','summary','record'):
            raw=response([summary()]);target={'range':raw,'summary':raw['dailyWeightSummaries'][0],'record':raw['dailyWeightSummaries'][0]['latestWeight']}[level]
            target['weightUnit']='kg';self.assertIsNone(self.project(raw)[0])
    def test_malformed_range_fail_soft(self):
        for raw in (None,[],{},response(rows={}),{'dailyWeightSummaries':[]},response(rows=[None]),response(rows=[{'summaryDate':'bad'}])):
            with self.subTest(rawtype=type(raw).__name__):self.assertEqual(self.project(raw)[1]['outcome'],'INVALID_RESPONSE')
    def test_malformed_summary_fields(self):
        for key,value in [('latestWeight',None),('numOfWeightEntries',True),('numOfWeightEntries',2),('allWeightMetrics',{}),('summaryDate','2026-02-30')]:
            raw=response([summary()]);raw['dailyWeightSummaries'][0][key]=value
            self.assertEqual(self.project(raw)[1]['outcome'],'INVALID_RESPONSE')
    def test_summary_outside_window_invalid(self):
        self.assertEqual(self.project(response([summary('2026-04-02')]))[1]['outcome'],'INVALID_RESPONSE')
    def test_empty_range_no_observation(self):
        self.assertEqual(self.project(response()),(None,{'source':'garmin_connect','contract':CONTRACT,'outcome':'NO_OBSERVATION','range_start':'2026-04-01','range_end':'2026-04-01'}))
    def test_null_previous_sentinel_no_observation(self):
        full={key:None for key in measurement()}
        for previous in (None,full,{'calendarDate':None,'weight':None}):
            with self.subTest(keys=None if previous is None else sorted(previous)):
                w,m=self.project(response(previous=previous))
                self.assertIsNone(w);self.assertEqual(m['outcome'],'NO_OBSERVATION')
    def test_partial_previous_sentinel_remains_invalid(self):
        empty={key:None for key in measurement()}
        for key,value in measurement().items():
            previous=dict(empty);previous[key]=value
            with self.subTest(key=key):
                w,m=self.project(response(previous=previous))
                self.assertIsNone(w);self.assertEqual(m['outcome'],'INVALID_RESPONSE')
        for value in ('',False,0,[],{},float('nan')):
            previous=dict(empty);previous['weight']=value
            self.assertEqual(self.project(response(previous=previous))[1]['outcome'],'INVALID_RESPONSE')
    def test_incomplete_or_unknown_previous_sentinel_invalid(self):
        for previous in ({},{'weight':None},{'calendarDate':None},[],
                         {'calendarDate':None,'weight':None,'unknownField':None},
                         {'calendarDate':'malformed','weight':None}):
            self.assertEqual(self.project(response(previous=previous))[1]['outcome'],'INVALID_RESPONSE')
    def test_null_previous_recovers_at_later_measurement(self):
        raw=response([summary('2026-04-02')],{key:None for key in measurement()})
        ctx=WeightContext('2026-04-03');client=SimpleNamespace(get_weigh_ins=lambda *_:raw)
        self.assertEqual(ctx.for_day(client,'2026-04-01')[1]['outcome'],'NO_OBSERVATION')
        w,m=ctx.for_day(client,'2026-04-02');self.assertEqual(m['outcome'],'MEASURED');self.assertFalse(w['is_carried_forward'])
        w,m=ctx.for_day(client,'2026-04-03');self.assertEqual(m['outcome'],'CARRIED');self.assertEqual(w['last_measured_date'],'2026-04-02')
    def test_null_sentinel_not_accepted_for_daily_selected_measurement(self):
        s=summary();s['latestWeight']={key:None for key in measurement()}
        self.assertEqual(self.project(response([s]))[1]['outcome'],'INVALID_RESPONSE')
    def test_future_previous_invalid(self):
        self.assertEqual(self.project(response(previous=measurement('2026-04-02')))[1]['outcome'],'INVALID_RESPONSE')
    def test_invalid_previous_can_recover_on_valid_day(self):
        self.assertEqual(self.project(response([summary()],previous=[]))[1]['outcome'],'MEASURED')
    def test_next_and_average_never_used(self):
        raw=response();raw['nextDateWeight']=measurement();raw['totalAverage']={'weight':70000}
        self.assertEqual(self.project(raw)[1]['outcome'],'NO_OBSERVATION')
    def test_only_projection_cached_composition_identifiers_discarded(self):
        raw=response([summary()]);ctx=WeightContext('2026-04-01');w,_=ctx.for_day(SimpleNamespace(get_weigh_ins=lambda *_:raw),'2026-04-01')
        cached=repr(vars(ctx))
        for key in ('samplePk','bodyFat','bodyWater','boneMass','muscleMass','visceralFat','metabolicAge','physiqueRating','sourceType','timestampGMT'):
            self.assertNotIn(key,cached);self.assertNotIn(key,w)
        self.assertIsNone(w['bmi']);w['weight']=1;self.assertEqual(ctx.for_day(None,'2026-04-01')[0]['weight'],70)
    def test_failure_is_cached_no_retry_storm(self):
        calls=[]
        def fetch(*a):calls.append(a);raise RuntimeError('PRIVATE_PROVIDER_BODY')
        ctx=WeightContext('2026-04-03');c=SimpleNamespace(get_weigh_ins=fetch)
        for d in ('2026-04-01','2026-04-02','2026-04-03'):
            w,m=ctx.for_day(c,d);self.assertIsNone(w);self.assertEqual(m['outcome'],'FETCH_FAILED');self.assertNotIn('PRIVATE',repr(m))
        self.assertEqual(len(calls),1)
    def test_windows_at_45_day_boundary(self):
        calls=[];c=SimpleNamespace(get_weigh_ins=lambda *a:calls.append(a) or response());ctx=WeightContext('2026-05-16')
        for offset in range(46):ctx.for_day(c,(date(2026,4,1)+timedelta(days=offset)).isoformat())
        self.assertEqual(calls,[('2026-04-01','2026-05-15'),('2026-05-16','2026-05-16')])

if __name__=='__main__':unittest.main()
