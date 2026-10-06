"""Real health/job/facade path with synthetic stores and fake providers only."""
import builtins
from contextlib import contextmanager, redirect_stdout
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import health_jobs as jobs
import health_sync
import health_daily
import health_backfill
import renpho_backfill
import renpho_sync
import GarminTelemetry as bot
from health_state import HealthStore, HealthJobBusy
from garmin_auth import GarminCoordinator, GarminBusy
from test_garmin_weight import response, summary, measurement

NOW=datetime(2026,6,1,12,tzinfo=timezone.utc)
SECRET='SYNTHETIC_PRIVATE_PROVIDER_EXCEPTION'


class OptionalHealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve()
        self.store=HealthStore(self.root/'health');token=self.root/'fake.json';token.write_text('{}')
        self.coord=GarminCoordinator(token,self.root/'garmin.lock',timeout=0)
        self.raw=response();self.weight_error=None;self.fail_date=None;self.fail_sleep=False;self.drive_error=False
        self.core_calls=[];self.weight_calls=[];self.uploads=[];self.active=False;self.states_at_upload=[]
        outer=self
        class Client:
            def login(self,path):pass
            def get_stats(self,day):
                outer.assertTrue(outer.active);outer.core_calls.append(day)
                if outer.fail_date==day:raise RuntimeError(SECRET)
                return None  # Legitimate empty historical Garmin payload.
            def get_sleep_data(self,day):
                outer.assertTrue(outer.active)
                if outer.fail_sleep:raise RuntimeError(SECRET)
                return {}
            def get_weigh_ins(self,start,end):
                outer.assertTrue(outer.active);outer.weight_calls.append((start,end))
                if outer.weight_error:raise outer.weight_error
                return outer.raw
        @contextmanager
        def init():
            with self.coord.session(Client) as c:
                self.active=True
                try:yield c
                finally:self.active=False
        def folder(*_):self.assertFalse(self.active);return 'synthetic-folder'
        def upload(path,*_):
            self.assertFalse(self.active)
            if self.drive_error:raise RuntimeError(SECRET)
            self.states_at_upload.append(json.loads(self.store.path.read_bytes())['finalized_through'] if self.store.path.exists() else None)
            self.uploads.append(json.loads(Path(path).read_text()))
        self.output=io.StringIO();self.quiet=redirect_stdout(self.output);self.quiet.__enter__()
        self.patches=[patch.object(health_sync,'init_garmin',init),patch.object(health_sync,'get_drive_folder_id',return_value='fake'),
                      patch.object(health_sync,'get_or_create_drive_folder',side_effect=folder),patch.object(health_sync,'upload_to_drive',side_effect=upload),
                      patch.object(jobs,'default_store',return_value=self.store),
                      patch.object(renpho_sync,'get_renpho_metrics',side_effect=AssertionError('RETIRED_CALLED'))]
        for p in self.patches:p.start()
    def tearDown(self):
        self.quiet.__exit__(None,None,None)
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()
    def run_job(self,**kw):
        args=dict(start='2026-04-01',end='2026-04-03',store=self.store,now=NOW,sleep=self.pace)
        args.update(kw);return jobs.run_range(**args)
    def pace(self,_):
        self.assertFalse(self.active)
        with self.coord.session(lambda:SimpleNamespace(login=lambda _:None)):pass
        with self.assertRaises(HealthJobBusy):
            with HealthStore(self.store.directory).lease():pass
    def state_identity(self):return self.store.path.read_bytes(),self.store.path.stat().st_ino
    def test_measurement_upload_finalization_and_lock_order(self):
        self.raw=response([summary()]);r=self.run_job()
        self.assertEqual(r['finalized_through'],'2026-04-03');self.assertEqual(self.states_at_upload,[None,'2026-04-01','2026-04-02'])
        self.assertEqual([x['weight_acquisition']['outcome'] for x in self.uploads],['MEASURED','CARRIED','CARRIED'])
        self.assertEqual(self.weight_calls,[('2026-04-01','2026-04-03')])
    def test_empty_weight_never_blocks_empty_core_payload(self):
        r=self.run_job();self.assertEqual(r['status'],'COMPLETE')
        for x in self.uploads:
            self.assertIsNone(x['weight_metrics']);self.assertIsNone(x['daily_stats']);self.assertEqual(x['sleep_data'],{})
    def test_previous_weight_carry_only(self):
        self.raw=response(previous=measurement('2026-03-01'));self.assertEqual(self.run_job()['status'],'COMPLETE')
        self.assertTrue(all(x['weight_metrics']['last_measured_date']=='2026-03-01' for x in self.uploads))
    def test_optional_api_error_uploads_finalizes_no_raw_leak(self):
        self.weight_error=RuntimeError(SECRET);r=self.run_job();self.assertEqual(r['status'],'COMPLETE')
        self.assertEqual(len(self.weight_calls),1)
        self.assertTrue(all(x['weight_acquisition']['outcome']=='FETCH_FAILED' for x in self.uploads))
        self.assertNotIn(SECRET,self.output.getvalue()+repr(self.uploads)+repr(r))
    def test_malformed_schema_uploads_finalizes(self):
        self.raw={'unrecognized':SECRET};self.assertEqual(self.run_job()['status'],'COMPLETE')
        self.assertTrue(all(x['weight_acquisition']['outcome']=='INVALID_RESPONSE' for x in self.uploads));self.assertNotIn(SECRET,repr(self.uploads))
    def test_ambiguous_day_uploads_null_finalizes(self):
        s=summary();s['latestWeight']['calendarDate']='2026-04-02';self.raw=response([s])
        self.assertEqual(self.run_job()['status'],'COMPLETE')
        self.assertTrue(all(x['weight_metrics'] is None for x in self.uploads));self.assertEqual(self.uploads[0]['weight_acquisition']['outcome'],'AMBIGUOUS')
    def test_core_failure_stops_before_next_day(self):
        self.fail_date='2026-04-02';r=self.run_job()
        self.assertEqual(r['failed_date'],'2026-04-02');self.assertEqual(r['finalized_through'],'2026-04-01')
        self.assertEqual(self.core_calls,['2026-04-01','2026-04-02']);self.assertEqual(len(self.uploads),1)
        self.assertNotIn(SECRET,self.output.getvalue())
    def test_sleep_failure_no_weight_or_upload_or_advance(self):
        self.fail_sleep=True;r=self.run_job();self.assertIsNone(r['finalized_through']);self.assertEqual(self.weight_calls,[]);self.assertEqual(self.uploads,[])
    def test_busy_coordinator_no_weight_or_advance(self):
        with patch.object(health_sync,'init_garmin',side_effect=GarminBusy('busy')):r=self.run_job()
        self.assertIsNone(r['finalized_through']);self.assertEqual(self.uploads,[]);self.assertEqual(self.weight_calls,[])
    def test_drive_failure_stops_no_advance(self):
        self.drive_error=True;r=self.run_job();self.assertIsNone(r['finalized_through']);self.assertEqual(self.core_calls,['2026-04-01'])
        self.assertNotIn(SECRET,self.output.getvalue())
    def test_repair_success_keeps_state_bytes_inode(self):
        self.run_job();before=self.state_identity();self.weight_calls.clear()
        r=self.run_job(start='2026-04-02',end='2026-04-02',repair=True)
        self.assertEqual(r['status'],'COMPLETE');self.assertEqual(before,self.state_identity());self.assertEqual(self.weight_calls,[('2026-04-02','2026-04-02')])
    def test_repair_optional_failure_is_success_keeps_state(self):
        self.run_job();before=self.state_identity();self.weight_error=RuntimeError(SECRET)
        self.assertEqual(self.run_job(repair=True)['status'],'COMPLETE');self.assertEqual(before,self.state_identity())
    def test_repair_core_failure_keeps_state(self):
        self.run_job();before=self.state_identity();self.fail_date='2026-04-01'
        self.assertEqual(self.run_job(repair=True)['status'],'INTERRUPTED');self.assertEqual(before,self.state_identity())
    def test_provisional_optional_failure_keeps_watermark(self):
        self.run_job();before=self.state_identity();self.weight_error=RuntimeError(SECRET)
        self.assertEqual(jobs.refresh_today(store=self.store,now=NOW),('2026-06-01',True));self.assertEqual(before,self.state_identity())
        self.assertEqual(self.weight_calls[-1],('2026-06-01','2026-06-01'))
    def test_provisional_core_failure_keeps_watermark(self):
        self.run_job();before=self.state_identity();self.fail_date='2026-06-01'
        self.assertEqual(jobs.refresh_today(store=self.store,now=NOW),('2026-06-01',False));self.assertEqual(before,self.state_identity())
    def test_45_date_tranche_fetch_bound_and_resume(self):
        r=self.run_job(end='2026-05-16',max_days=45);self.assertEqual(r['reason'],'JOB_LIMIT');self.assertEqual(r['completed_dates'],45)
        self.assertEqual(self.weight_calls,[('2026-04-01','2026-05-15')])
        r=self.run_job(end='2026-05-16',max_days=45);self.assertEqual(r['status'],'COMPLETE');self.assertEqual(self.weight_calls[-1],('2026-05-16','2026-05-16'))
    def test_46_dates_two_bounded_queries_no_daily_fanout(self):
        self.assertEqual(self.run_job(end='2026-05-16')['completed_dates'],46)
        self.assertEqual(self.weight_calls,[('2026-04-01','2026-05-15'),('2026-05-16','2026-05-16')])
    def test_caught_up_no_query(self):
        self.run_job();self.weight_calls.clear();self.assertEqual(self.run_job()['completed_dates'],0);self.assertEqual(self.weight_calls,[])
    def test_daily_backfill_legacy_alias_and_telegram_no_renpho_access(self):
        real_import=builtins.__import__
        def guarded(name,*a,**kw):
            if name.split('.')[0] in ('renpho','renpho_sync','renpho_api'):raise AssertionError('Renpho import prohibited')
            return real_import(name,*a,**kw)
        with patch.object(builtins,'__import__',side_effect=guarded):
            self.assertEqual(health_backfill.run_health_backfill('2026-04-01','2026-04-01',store=self.store,now=NOW)['status'],'COMPLETE')
            self.assertEqual(renpho_backfill.backfill_history('2026-04-01','2026-04-02',store=self.store,now=NOW,sleep=self.pace)['status'],'COMPLETE')
            self.assertEqual(health_daily.run_daily(store=self.store,now=datetime(2026,4,4,tzinfo=timezone.utc),sleep=self.pace)['status'],'COMPLETE')
            before=self.state_identity();messages=[]
            with patch.object(bot,'bot',SimpleNamespace(send_message=lambda _,text:messages.append(text))),patch.object(jobs,'berlin_today',return_value=NOW.date()):
                bot.trigger_health_sync_bot(SimpleNamespace(chat=SimpleNamespace(id=1)))
                self.assertEqual(health_sync.main(),0)
            self.assertEqual(before,self.state_identity());self.assertTrue(any('provisional' in m.lower() for m in messages))


class RetirementTests(unittest.TestCase):
    def test_retired_entrypoint_no_io_or_provider_import(self):
        with patch('builtins.open',side_effect=AssertionError('config access')),patch('builtins.__import__',side_effect=AssertionError('SDK import')):
            with self.assertRaises(renpho_sync.RenphoRetiredError):renpho_sync.get_renpho_metrics('2026-04-01')
            with redirect_stdout(io.StringIO()) as out:self.assertEqual(renpho_sync.main(),2)
        self.assertIn('retired',out.getvalue().lower())
    def test_fresh_process_imports_without_credentials_sdk_network(self):
        code='''import builtins, socket, sys
from unittest.mock import patch

def blocked(*a, **k):raise AssertionError("IO forbidden")
with patch.object(builtins,"open",side_effect=blocked),patch.object(socket,"socket",side_effect=blocked):
 import garmin_weight, health_sync, health_jobs, health_daily, health_backfill, renpho_backfill, renpho_sync
 assert not any(n in sys.modules for n in ("renpho","renpho_api","garminconnect","googleapiclient","telebot"))
'''
        r=subprocess.run([sys.executable,'-B','-c',code],capture_output=True,text=True,timeout=10)
        self.assertEqual(r.returncode,0,r.stderr)

if __name__=='__main__':unittest.main()
