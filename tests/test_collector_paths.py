from contextlib import contextmanager, redirect_stdout
from datetime import datetime as RealDatetime
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import GarminTelemetry as botmod
import collector_runtime as runtime
import health_sync
import backfill
import health_backfill
import renpho_backfill
from garmin_auth import GarminCoordinator, GarminBusy

ACTIVITIES=[
    {'activityId':101,'activityType':{'typeKey':'running'},'startTimeLocal':'2026-09-28 08:00:00','distance':5000.0,'averageHR':140},
    {'activityId':102,'activityType':{'typeKey':'cycling'},'startTimeLocal':'2026-09-29 09:00:00'},
]

class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve()
        self.store=self.root/'synthetic-session.json';self.store.write_text('{}')
        self.coord=GarminCoordinator(self.store,self.root/'owner.lock',timeout=2)
        self.local=threading.local();self.events=[];self.uploads=[];self.marked=[];self.messages=[]
        self.activities=ACTIVITIES;self.login_count=0
        outer=self
        class Client:
            ActivityDownloadFormat=SimpleNamespace(ORIGINAL='original')
            def login(self,path):
                self.assert_owned_path=path
                outer.login_count+=1
                # The authentication boundary was acquired before SDK login.
                with outer.assertRaises(GarminBusy):
                    with GarminCoordinator(outer.store,outer.coord.lock_path,timeout=0).session(lambda:None):pass
            def get_activities(self,start,count):
                outer.check_auth();outer.events.append(('list',start,count))
                return outer.activities if start==0 else []
            def download_activity(self,activity_id,*,dl_fmt):
                outer.check_auth();outer.assertEqual(dl_fmt,'original');outer.events.append(('download',activity_id))
                return ('synthetic-fit-'+activity_id).encode()
            def get_stats(self,date):outer.check_auth();outer.events.append(('stats',date));return {'steps':0,'date':date,'restingHeartRate':None}
            def get_sleep_data(self,date):outer.check_auth();outer.events.append(('sleep',date));return {'date':date,'sleepTimeSeconds':1234}
        self.client_class=Client
        def folder(name,parent):self.check_outside();self.events.append(('folder',name,parent));return parent+'/'+name
        def upload(path,name,mime,parent):
            self.check_outside();data=Path(path).read_bytes();self.uploads.append((name,mime,parent,data));return 'fictional-upload-id'
        def renpho(date):self.check_outside();self.events.append(('renpho',date));return {'weight':70,'is_carried_forward':True,'last_measured_date':'2026-09-20'}
        def mark(activity_id):self.check_outside();self.marked.append(activity_id)
        fakebot=SimpleNamespace(send_message=lambda chat,text,**kw:self.messages.append(text))
        self.patches=[patch.object(botmod,'bot',fakebot)]
        for module in (botmod,backfill,health_sync):
            self.patches += [patch.object(module,'init_garmin',self.init),patch.object(module,'get_drive_folder_id',return_value='root'),patch.object(module,'get_or_create_drive_folder',side_effect=folder),patch.object(module,'upload_to_drive',side_effect=upload)]
        for module in (botmod,backfill):
            self.patches += [patch.object(module,'get_synced_ids',return_value=['102']),patch.object(module,'mark_as_synced',side_effect=mark)]
        self.patches += [patch.object(health_sync,'get_renpho_metrics',side_effect=renpho)]
        for p in self.patches:p.start()
        self.output=io.StringIO();self.quiet=redirect_stdout(self.output);self.quiet.__enter__()
    def tearDown(self):
        self.quiet.__exit__(None,None,None)
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()
    @contextmanager
    def init(self):
        with self.coord.session(self.client_class) as session:
            self.local.owned=True;self.events.append(('enter',))
            try:yield session
            finally:self.events.append(('exit',));self.local.owned=False
    def check_auth(self):self.assertTrue(getattr(self.local,'owned',False))
    def check_outside(self):self.assertFalse(getattr(self.local,'owned',False))
    def message(self):return SimpleNamespace(chat=SimpleNamespace(id=42))
    def test_activity_selection_payload_and_paths_unchanged(self):
        botmod.trigger_sync(self.message())
        self.assertIn(('list',0,5),self.events);self.assertEqual(self.marked,['101'])
        self.assertEqual([u[:3] for u in self.uploads],[('running_101.fit','application/octet-stream','root/Running/2026/09'),('running_101.json','application/json','root/Running/2026/09')])
        self.assertEqual(self.uploads[0][3],b'synthetic-fit-101')
        self.assertEqual(self.uploads[1][3],json.dumps(ACTIVITIES[0],indent=4).encode())
        self.assertEqual(self.login_count,2)
    def test_activity_no_new_behavior(self):
        with patch.object(botmod,'get_synced_ids',return_value=['101','102']):botmod.trigger_sync(self.message())
        self.assertEqual(self.uploads,[]);self.assertIn('No new activities',self.messages[-1])
    def test_activity_missing_date_folders(self):
        self.activities=[{'activityId':101,'activityType':{'typeKey':'running'}}]
        botmod.trigger_sync(self.message())
        self.assertEqual(self.uploads[0][2],'root/Running/Unknown_Year/Unknown_Month')
    def test_health_payload_fields_and_non_garmin_release(self):
        self.assertTrue(health_sync.sync_health_data('2026-09-28'))
        self.assertEqual(self.login_count,1)
        name,mime,parent,data=self.uploads[0]
        self.assertEqual((name,mime,parent),('health_2026-09-28.json','application/json','root/Daily_Health/2026/09'))
        self.assertEqual(json.loads(data),{'date':'2026-09-28','daily_stats':{'steps':0,'date':'2026-09-28','restingHeartRate':None},'sleep_data':{'date':'2026-09-28','sleepTimeSeconds':1234},'weight_metrics':{'weight':70,'is_carried_forward':True,'last_measured_date':'2026-09-20'}})
        self.assertLess(self.events.index(('exit',)),self.events.index(('renpho','2026-09-28')))
    def test_health_garmin_failure_prevents_partial_upload(self):
        def broken(date):self.check_auth();raise RuntimeError('FAKE_SECRET')
        with patch.object(self.client_class,'get_sleep_data',side_effect=broken):self.assertFalse(health_sync.sync_health_data('2026-09-28'))
        self.assertEqual(self.uploads,[]);self.assertNotIn('FAKE_SECRET',self.output.getvalue());self.check_outside()
    def test_activity_timeout_controlled_no_fallback(self):
        with patch.object(botmod,'init_garmin',side_effect=GarminBusy('busy')):botmod.trigger_sync(self.message())
        self.assertIn('busy',self.messages[-1]);self.assertEqual(self.login_count,0);self.assertEqual(self.uploads,[])
    def test_health_timeout_controlled(self):
        with patch.object(health_sync,'init_garmin',side_effect=GarminBusy('busy')):self.assertFalse(health_sync.sync_health_data('2026-09-28'))
        self.assertEqual(self.login_count,0);self.assertIn('busy',self.output.getvalue())
    def test_standalone_health_entry(self):
        class Clock(RealDatetime):
            @classmethod
            def now(cls):return cls(2026,9,28)
        with patch.object(health_sync,'datetime',Clock):health_sync.main()
        self.assertIn(('stats','2026-09-28'),self.events)
    def test_telegram_health_entry(self):
        botmod.trigger_health_sync_bot(self.message())
        self.assertEqual(self.login_count,1);self.assertIn('Successfully synced',self.messages[-1])
    def test_activity_backfill_bounded_and_outputs(self):
        with patch.object(backfill.time,'sleep',side_effect=lambda seconds:self.check_outside()):backfill.run_full_backfill()
        self.assertIn(('list',0,50),self.events);self.assertIn(('list',50,50),self.events)
        self.assertEqual(self.login_count,3) # first page, download, empty next page
        self.assertEqual(self.marked,['101']);self.assertEqual(json.loads(self.uploads[1][3]),ACTIVITIES[0])
        self.assertEqual(sum(e==('enter',) for e in self.events),sum(e==('exit',) for e in self.events))
    def test_backfill_date_boundary_preserved(self):
        self.activities=[{'activityId':1,'activityType':{'typeKey':'running'},'startTimeLocal':'2026-06-25 23:59:59'}]
        backfill.run_full_backfill();self.assertEqual(self.login_count,1);self.assertEqual(self.uploads,[])
    def test_backfill_page_timeout_stops(self):
        with patch.object(backfill,'init_garmin',side_effect=GarminBusy('busy')):backfill.run_full_backfill()
        self.assertEqual(self.login_count,0);self.assertEqual(self.uploads,[])
    def test_health_backfill_indirect_and_per_date(self):
        class Clock(RealDatetime):
            @classmethod
            def now(cls):return cls(2026,9,24)
        with patch.object(health_backfill,'datetime',Clock),patch.object(health_backfill.time,'sleep',side_effect=lambda seconds:self.check_outside()):health_backfill.run_health_backfill()
        self.assertEqual([e for e in self.events if e[0]=='stats'],[('stats','2026-09-23'),('stats','2026-09-24')]);self.assertEqual(self.login_count,2)
    def test_renpho_backfill_indirect_and_per_date(self):
        class Clock(RealDatetime):
            @classmethod
            def now(cls):return cls(2026,4,24)
        with patch.object(renpho_backfill,'datetime',Clock),patch.object(renpho_backfill.time,'sleep',side_effect=lambda seconds:self.check_outside()):renpho_backfill.backfill_history()
        self.assertEqual([e for e in self.events if e[0]=='stats'],[('stats','2026-04-23'),('stats','2026-04-24')]);self.assertEqual(self.login_count,2)
    def test_threaded_telegram_callers(self):
        errors=[]
        def run():
            try:botmod.trigger_sync(self.message())
            except Exception as e:errors.append(e)
        ts=[threading.Thread(target=run) for _ in range(3)]
        for t in ts:t.start()
        for t in ts:t.join(5)
        self.assertFalse(any(t.is_alive() for t in ts));self.assertEqual(errors,[])
        self.assertEqual(self.login_count,6)
        # Each unchanged ledger snapshot can still select the same activity:
        # auth coordination deliberately does NOT promise upload/id deduplication.
        self.assertEqual(self.marked,['101']*3)
    def test_runtime_factory_created_after_lease_with_absolute_store(self):
        fake_config={'GARMIN_EMAIL':'fictional@example.invalid','GARMIN_PASSWORD':'synthetic-password'}
        created=[]
        outer=self
        class Client(self.client_class):
            def __init__(self,email,password):
                created.append((email,password))
                with outer.assertRaises(GarminBusy):
                    with GarminCoordinator(outer.store,outer.coord.lock_path,timeout=0).session(lambda:None):pass
        with patch.dict(sys.modules,{'garminconnect':SimpleNamespace(Garmin=Client)}),patch.object(runtime,'get_config',return_value=fake_config),patch.dict(os.environ,{'GARMIN_TOKEN_STORE':str(self.store),'GARMIN_COORDINATOR_LOCK':str(self.coord.lock_path)}):
            with runtime.init_garmin():pass
            with runtime.init_garmin():pass
        self.assertEqual(len(created),2);self.assertEqual(self.login_count,2)
    def test_bot_registration_explicit(self):
        calls=[]
        fake=SimpleNamespace(register_message_handler=lambda f,**kw:calls.append((f,kw)),infinity_polling=lambda:calls.append(('poll',{})))
        with patch.dict(sys.modules,{'telebot':SimpleNamespace(TeleBot=lambda token:fake)}),patch.object(botmod,'get_config',return_value={'TELEGRAM_TOKEN':'synthetic-token'}):botmod.run_bot()
        self.assertEqual(len(calls),6);self.assertEqual(calls[-1][0],'poll')
        for f,kwargs in calls[1:5]:
            text={'trigger_sync':'🏃 Get Latest Activities','sync_status':'📊 Sync Status','trigger_health_sync_bot':'❤️ Get Health Data','placeholder_handler':'✨ New Button'}[f.__name__]
            self.assertTrue(kwargs['func'](SimpleNamespace(text=text)))

class RenphoPreservationTests(unittest.TestCase):
    def test_existing_carry_forward_payload(self):
        import renpho_sync
        records=[{'timeStamp':RealDatetime(2026,9,20,12).timestamp(),'weight':70,'bmi':22}, {'timeStamp':RealDatetime(2026,9,22,12).timestamp(),'weight':71,'bmi':23}]
        class Client:
            def __init__(self,*args):pass
            def login(self):pass
            def get_all_measurements(self):return records
        with patch.dict(sys.modules,{'renpho':SimpleNamespace(RenphoClient=Client)}),patch('builtins.open',return_value=io.StringIO('{"GARMIN_EMAIL":"synthetic","RENPHO_PASSWORD":"synthetic"}')):
            result=renpho_sync.get_renpho_metrics('2026-09-21')
        self.assertEqual(result,{'weight':70,'bmi':22,'is_carried_forward':True,'last_measured_date':'2026-09-20'})

class ImportTests(unittest.TestCase):
    def test_imports_have_no_configuration_or_service_side_effects(self):
        code='''import builtins, sys
from unittest.mock import patch

def refuse(*a, **kw): raise AssertionError("unexpected configuration/service construction")
with patch.object(builtins, "open", side_effect=refuse):
 import garmin_auth, collector_runtime, GarminTelemetry, health_sync, backfill, health_backfill, renpho_backfill, renpho_sync
 assert GarminTelemetry.bot is None
 assert collector_runtime._config is None
 assert collector_runtime._drive_service is None
 assert not any(x in sys.modules for x in ("garminconnect", "telebot", "googleapiclient", "renpho"))
'''
        result=subprocess.run([sys.executable,'-B','-c',code],text=True,capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)

if __name__=='__main__':unittest.main()
