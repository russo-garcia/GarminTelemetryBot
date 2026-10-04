from contextlib import redirect_stdout
from datetime import date, datetime, timezone
import io
import json
import multiprocessing
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import health_state as hs
import health_jobs as jobs
import health_daily
import health_backfill
import health_sync
import GarminTelemetry as bot
from garmin_auth import GarminCoordinator, GarminBusy
from types import SimpleNamespace

NOW=datetime(2026,4,5,10,tzinfo=timezone.utc)
TODAY=date(2026,4,5)

def holding_job(directory, ready, release):
    with hs.HealthStore(directory).lease():
        ready.set();release.wait(10)

def killed_after_upload(directory, uploaded):
    store=hs.HealthStore(directory)
    def sync(day):
        uploaded.set()
        os.kill(os.getpid(),signal.SIGKILL)
    jobs.run_range(start='2026-04-01',end='2026-04-02',store=store,now=NOW,sync=sync)


def probe_job(directory, result):
    try:
        with hs.HealthStore(directory).lease():result.put('opened')
    except hs.HealthJobBusy:result.put('busy')


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve()
        self.store=hs.HealthStore(self.root/'runtime');self.calls=[]
    def tearDown(self):self.tmp.cleanup()
    def sync(self,day):self.calls.append(day);return True
    def run_job(self,**kw):
        options=dict(start='2026-04-01',end='2026-04-04',store=self.store,now=NOW,sync=self.sync,sleep=lambda _:None)
        options.update(kw);return jobs.run_range(**options)
    def state(self):
        with self.store.lease() as lease:return lease.load(TODAY)
    def test_first_bootstrap_and_owner_only_state(self):
        r=self.run_job();self.assertEqual(r['status'],'COMPLETE');self.assertEqual(r['completed_dates'],4)
        self.assertEqual(self.calls,['2026-04-01','2026-04-02','2026-04-03','2026-04-04'])
        self.assertEqual(self.state().as_dict(),{'schema_version':1,'history_start':'2026-04-01','finalized_through':'2026-04-04'})
        self.assertEqual(stat.S_IMODE(self.store.path.stat().st_mode),0o600)
        self.assertEqual(stat.S_IMODE(self.store.directory.stat().st_mode),0o700)
    def test_failure_stops_then_resume_contiguously(self):
        def fail(day):self.calls.append(day);return day!='2026-04-02'
        r=self.run_job(sync=fail);self.assertEqual(r['status'],'INTERRUPTED')
        self.assertEqual(self.calls,['2026-04-01','2026-04-02']);self.assertEqual(r['finalized_through'],'2026-04-01')
        self.calls.clear();self.assertEqual(self.run_job()['status'],'COMPLETE');self.assertEqual(self.calls,['2026-04-02','2026-04-03','2026-04-04'])
    def test_first_date_failure_leaves_null(self):
        r=self.run_job(sync=lambda _:False);self.assertEqual(r['finalized_through'],None);self.assertEqual(r['completed_dates'],0)
        self.assertIsNone(self.state().finalized_through)
    def test_missing_gap_rejected_before_sync(self):
        self.run_job(end='2026-04-01');self.calls.clear()
        with self.assertRaises(hs.HealthStateError):self.run_job(start='2026-04-03')
        self.assertEqual(self.calls,[])
    def test_watermark_does_not_advance_inside_sync(self):
        seen=[]
        def sync(day):
            seen.append(json.loads(self.store.path.read_bytes())['finalized_through']);return True
        self.run_job(sync=sync)
        self.assertEqual(seen,[None,'2026-04-01','2026-04-02','2026-04-03'])
    def test_advance_rejects_skipping_and_today(self):
        with self.store.lease() as lease:
            lease.initialize(date(2026,4,1),TODAY)
            for day in (date(2026,4,2),TODAY):
                with self.assertRaises(hs.HealthStateError):lease.advance(day,TODAY)
    def test_drive_failure_from_real_primitive_never_finalizes(self):
        from contextlib import contextmanager
        @contextmanager
        def session():yield SimpleNamespace(get_stats=lambda _: {},get_sleep_data=lambda _: {})
        with patch.object(health_sync,'init_garmin',session),patch.object(health_sync,'get_renpho_metrics',return_value=None),patch.object(health_sync,'get_drive_folder_id',return_value='fake'),patch.object(health_sync,'get_or_create_drive_folder',return_value='fake'),patch.object(health_sync,'upload_to_drive',side_effect=RuntimeError('SYNTHETIC_SECRET')),redirect_stdout(io.StringIO()) as output:
            r=self.run_job(sync=health_sync.sync_health_data)
        self.assertEqual(r['status'],'INTERRUPTED');self.assertIsNone(self.state().finalized_through);self.assertNotIn('SYNTHETIC_SECRET',output.getvalue())
    def test_real_primitive_garmin_busy(self):
        with patch.object(health_sync,'init_garmin',side_effect=GarminBusy('synthetic')),redirect_stdout(io.StringIO()):r=self.run_job(sync=health_sync.sync_health_data)
        self.assertEqual(r['status'],'INTERRUPTED');self.assertIsNone(self.state().finalized_through)
    def test_unwrapped_busy_stops(self):
        def busy(_):raise GarminBusy('not printed')
        r=self.run_job(sync=busy);self.assertEqual(r['reason'],'GARMIN_BUSY');self.assertIsNone(self.state().finalized_through)
    def test_truthy_nonboolean_is_not_success(self):
        r=self.run_job(sync=lambda _: 'true');self.assertEqual(r['status'],'INTERRUPTED');self.assertIsNone(self.state().finalized_through)
    def test_unknown_exception_stops_no_later_date(self):
        def bad(day):self.calls.append(day);raise RuntimeError('PRIVATE')
        r=self.run_job(sync=bad);self.assertEqual(r['reason'],'SYNC_FAILED');self.assertEqual(self.calls,['2026-04-01'])
    def test_daily_requires_explicit_bootstrap(self):
        with self.assertRaises(hs.HealthStateError):health_daily.run_daily(store=self.store,now=NOW,sync=self.sync)
        self.assertEqual(self.calls,[]);self.assertFalse(self.store.path.exists())
    def test_already_caught_up_noop(self):
        self.run_job();self.calls.clear();before=self.store.path.read_bytes()
        r=health_daily.run_daily(store=self.store,now=NOW,sync=self.sync)
        self.assertEqual(r['status'],'COMPLETE');self.assertEqual(r['completed_dates'],0);self.assertEqual(self.calls,[]);self.assertEqual(self.store.path.read_bytes(),before)
    def test_multiday_downtime(self):
        self.run_job(end='2026-04-01');self.calls.clear()
        r=health_daily.run_daily(store=self.store,now=NOW,sync=self.sync,sleep=lambda _:None)
        self.assertEqual(r['completed_dates'],3);self.assertEqual(self.calls,['2026-04-02','2026-04-03','2026-04-04'])
    def test_today_future_rejected_without_state_initialization(self):
        for end in ('2026-04-05','2026-04-06'):
            with self.assertRaises(hs.HealthStateError):self.run_job(end=end)
        self.assertEqual(self.calls,[]);self.assertFalse(self.store.path.exists())
    def test_repair_keeps_watermark_bytes_and_inode(self):
        self.run_job();before=self.store.path.read_bytes();inode=self.store.path.stat().st_ino;self.calls.clear()
        r=self.run_job(start='2026-04-02',end='2026-04-03',repair=True)
        self.assertEqual(r['status'],'COMPLETE');self.assertEqual(self.calls,['2026-04-02','2026-04-03'])
        self.assertEqual(self.store.path.read_bytes(),before);self.assertEqual(self.store.path.stat().st_ino,inode)
    def test_repair_failure_keeps_existing_prefix(self):
        self.run_job();before=self.store.path.read_bytes()
        r=self.run_job(repair=True,sync=lambda _:False)
        self.assertEqual(r['status'],'INTERRUPTED');self.assertEqual(self.store.path.read_bytes(),before)
    def test_repair_cannot_extend_or_initialize(self):
        with self.assertRaises(hs.HealthStateError):self.run_job(repair=True)
        self.run_job(end='2026-04-01')
        with self.assertRaises(hs.HealthStateError):self.run_job(repair=True)
    def test_range_history_start_cannot_move(self):
        self.run_job(end='2026-04-01')
        with self.assertRaises(hs.HealthStateError):self.run_job(start='2026-03-31')
    def test_day_limit_resumable(self):
        r=self.run_job(max_days=2);self.assertEqual(r['reason'],'JOB_LIMIT');self.assertEqual(r['finalized_through'],'2026-04-02')
        self.calls.clear();self.run_job();self.assertEqual(self.calls,['2026-04-03','2026-04-04'])
    def test_time_limit_and_pacing(self):
        ticks=iter([0,0,2]);sleeps=[]
        r=self.run_job(max_seconds=4,monotonic=lambda:next(ticks),sleep=sleeps.append)
        self.assertEqual(r['reason'],'JOB_LIMIT');self.assertEqual(r['completed_dates'],1);self.assertEqual(sleeps,[])
        self.calls.clear();self.run_job(sleep=sleeps.append);self.assertEqual(sleeps,[3.0,3.0])
    def test_invalid_bounds_before_acquisition(self):
        for kw in ({'pace_seconds':0},{'max_days':0},{'max_days':True},{'max_seconds':float('inf')}):
            with self.assertRaises(hs.HealthStateError):self.run_job(**kw)
        self.assertFalse(self.store.path.exists());self.assertEqual(self.calls,[])
    def test_corrupt_schema_date_duplicate_and_future_fail_closed(self):
        self.run_job();valid=self.store.path.read_bytes()
        cases=[b'broken',b'{}',valid.replace(b'"schema_version": 1',b'"schema_version": true'),valid.replace(b'2026-04-04',b'2026-04-06'),valid.replace(b'2026-04-01',b'2026-02-30'),valid.replace(b'{',b'{"schema_version": 1,',1),valid.replace(b'}',b',"extra":0}')]
        for raw in cases:
            with self.subTest(raw=raw):
                self.store.path.write_bytes(raw);self.calls.clear()
                with self.assertRaises(hs.HealthStateError):self.run_job()
                self.assertEqual(self.calls,[]);self.assertEqual(self.store.path.read_bytes(),raw)
    def test_unsafe_state_permissions_and_symlink_refused(self):
        self.run_job();self.store.path.chmod(0o644)
        with self.assertRaises(hs.HealthStateError):self.state()
        self.store.path.unlink();private=self.root/'fictional.json';private.write_text('DO_NOT_OPEN');self.store.path.symlink_to(private)
        with self.assertRaises(OSError):self.state()
    def test_atomic_replace_failure_retains_old_state(self):
        self.run_job(end='2026-04-01');before=self.store.path.read_bytes()
        with patch.object(hs.os,'replace',side_effect=OSError('simulated')):
            with self.assertRaises(OSError):self.run_job()
        self.assertEqual(self.store.path.read_bytes(),before);self.assertEqual(list(self.store.directory.glob('*.tmp')),[])
    def test_file_fsync_failure_does_not_publish(self):
        self.run_job(end='2026-04-01');before=self.store.path.read_bytes()
        with patch.object(hs.os,'fsync',side_effect=OSError('simulated')):
            with self.assertRaises(OSError):self.run_job()
        self.assertEqual(self.store.path.read_bytes(),before)
    def test_directory_fsync_failure_is_uncertain_but_never_skips(self):
        self.run_job(end='2026-04-01');self.calls.clear()
        with patch.object(hs,'_sync_directory',side_effect=OSError('simulated')):
            with self.assertRaises(OSError):self.run_job()
        self.assertEqual(self.calls,['2026-04-02']);self.assertEqual(self.state().finalized_through,date(2026,4,2))
        self.calls.clear();self.run_job();self.assertEqual(self.calls,['2026-04-03','2026-04-04'])
    def test_fsync_replace_directory_order(self):
        self.run_job(end='2026-04-01');events=[];fs=hs.os.fsync;rp=hs.os.replace
        def fsync(fd):events.append('directory_fsync' if stat.S_ISDIR(os.fstat(fd).st_mode) else 'file_fsync');return fs(fd)
        def replace(a,b):events.append('replace');return rp(a,b)
        with patch.object(hs.os,'fsync',side_effect=fsync),patch.object(hs.os,'replace',side_effect=replace):self.run_job(end='2026-04-02')
        self.assertEqual(events,['file_fsync','replace','directory_fsync'])
    def test_kill_after_remote_success_before_commit_repeats_date(self):
        ctx=multiprocessing.get_context('spawn');event=ctx.Event();process=ctx.Process(target=killed_after_upload,args=(self.store.directory,event))
        process.start();self.assertTrue(event.wait(10));process.join(10);self.assertEqual(process.exitcode,-signal.SIGKILL)
        self.assertIsNone(self.state().finalized_through);self.run_job(end='2026-04-01');self.assertEqual(self.calls,['2026-04-01'])
    def test_cross_process_daily_and_backfill_exclusion(self):
        ctx=multiprocessing.get_context('spawn');ready=ctx.Event();release=ctx.Event();process=ctx.Process(target=holding_job,args=(self.store.directory,ready,release))
        process.start()
        try:
            self.assertTrue(ready.wait(10))
            with self.assertRaises(hs.HealthJobBusy):self.run_job()
            with self.assertRaises(hs.HealthJobBusy):health_daily.run_daily(store=self.store,now=NOW,sync=self.sync)
            self.assertEqual(self.calls,[])
        finally:release.set();process.join(10)
        self.assertEqual(process.exitcode,0)
    def test_same_process_threads_exclude(self):
        seen=[]
        def contender():
            try:self.run_job()
            except hs.HealthJobBusy:seen.append('busy')
        with self.store.lease():
            thread=threading.Thread(target=contender);thread.start();thread.join(5)
        self.assertFalse(thread.is_alive());self.assertEqual(seen,['busy']);self.assertEqual(self.calls,[])
    def test_expired_and_inherited_lease_rejected(self):
        with self.store.lease() as lease:
            pid=os.fork()
            if pid==0:
                try:lease.load(TODAY)
                except hs.HealthStateError:os._exit(0)
                os._exit(1)
            _,status=os.waitpid(pid,0);self.assertEqual(os.waitstatus_to_exitcode(status),0)
            with self.assertRaises(hs.HealthJobBusy):
                with hs.HealthStore(self.store.directory).lease():pass
        with self.assertRaises(hs.HealthStateError):lease.load(TODAY)
    def test_fork_child_does_not_unlock_parent_for_other_process(self):
        ctx=multiprocessing.get_context('spawn');result=ctx.Queue()
        with self.store.lease():
            pid=os.fork()
            if pid==0:os._exit(0)
            os.waitpid(pid,0)
            probe=ctx.Process(target=probe_job,args=(self.store.directory,result));probe.start();probe.join(10)
            self.assertEqual(probe.exitcode,0);self.assertEqual(result.get(timeout=2),'busy')
        result.close();result.join_thread()
    def test_unsafe_job_lock_and_runtime_directory_refused(self):
        self.store._directory();self.store.lock_path.write_text('');self.store.lock_path.chmod(0o644)
        with self.assertRaises(hs.HealthStateError):self.run_job()
        self.store.lock_path.chmod(0o600);self.store.directory.chmod(0o755)
        with self.assertRaises(hs.HealthStateError):self.run_job()
        self.assertEqual(self.calls,[])
    def test_clock_rollback_rejects_future_watermark(self):
        self.run_job();self.calls.clear()
        with self.assertRaises(hs.HealthStateError):self.run_job(now=datetime(2026,4,4,tzinfo=timezone.utc))
        self.assertEqual(self.calls,[])
    def test_standalone_today_no_state_and_busy_exit(self):
        with patch.object(jobs,'default_store',return_value=self.store),patch.object(jobs,'berlin_today',return_value=TODAY),patch.object(health_sync,'sync_health_data',return_value=True),redirect_stdout(io.StringIO()):
            self.assertEqual(health_sync.main(),0)
            with self.store.lease():self.assertEqual(health_sync.main(),75)
        self.assertFalse(self.store.path.exists())
    def test_stable_job_lock_independent_of_state_replace(self):
        self.run_job(end='2026-04-01');old=self.store.lock_path.stat().st_ino
        self.run_job(end='2026-04-02');self.assertEqual(self.store.lock_path.stat().st_ino,old)
        self.assertNotEqual(self.store.path.stat().st_ino,old)
    def test_job_lock_then_garmin_and_release_before_upload(self):
        token=self.root/'synthetic.json';token.write_text('{}');coord=GarminCoordinator(token,self.root/'garmin.lock',timeout=0)
        class Client:
            def login(self,path):pass
            def get_stats(inner,day):
                with self.assertRaises(hs.HealthJobBusy):
                    with self.store.lease():pass
                return {}
        def sync(day):
            with coord.session(Client) as client:client.get_stats(day)
            # Independent activity caller can own Garmin while the health job
            # completes its upload/state step; no reversed acquisition occurs.
            with coord.session(Client):pass
            return True
        self.run_job(sync=sync,end='2026-04-01')
    def test_today_refresh_has_no_state_access(self):
        with patch.object(hs._Lease,'load',side_effect=AssertionError('NO_STATE_READ')),patch.object(hs._Lease,'_publish',side_effect=AssertionError('NO_STATE_WRITE')):
            day,ok=jobs.refresh_today(store=self.store,now=NOW,sync=self.sync)
        self.assertEqual(day,'2026-04-05');self.assertTrue(ok);self.assertFalse(self.store.path.exists())
    def test_telegram_provisional_berlin_no_watermark_change(self):
        self.run_job();before=self.store.path.read_bytes();messages=[]
        with patch.object(jobs,'default_store',return_value=self.store),patch.object(jobs,'berlin_today',return_value=TODAY),patch.object(health_sync,'sync_health_data',return_value=True) as sync,patch.object(bot,'bot',SimpleNamespace(send_message=lambda _,text:messages.append(text))):
            bot.trigger_health_sync_bot(SimpleNamespace(chat=SimpleNamespace(id=1)))
        sync.assert_called_once_with('2026-04-05');self.assertIn('provisional/open',messages[-1]);self.assertEqual(self.store.path.read_bytes(),before)
    def test_today_waits_for_job_without_watermark_change(self):
        with self.store.lease():
            with self.assertRaises(hs.HealthJobBusy):jobs.refresh_today(store=self.store,now=NOW,sync=self.sync)
        self.assertEqual(self.calls,[])
    def test_cli_exits(self):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(jobs.cli_result(lambda:{'status':'COMPLETE'}),0)
            self.assertEqual(jobs.cli_result(lambda:{'status':'INTERRUPTED'}),75)
            def invalid():raise hs.HealthStateError('not printed')
            self.assertEqual(jobs.cli_result(invalid),2)
            def busy():raise hs.HealthJobBusy('not printed')
            self.assertEqual(jobs.cli_result(busy),75)
    def test_paths_independent_of_cwd_and_relative_refused(self):
        self.assertTrue(hs.DEFAULT_DIRECTORY.is_absolute())
        with self.assertRaises(hs.HealthStateError):hs.HealthStore('relative')
    def test_dst_and_utc_calendar_boundaries(self):
        cases=[('2026-03-28T23:30:00+00:00','2026-03-29'),('2026-03-29T22:30:00+00:00','2026-03-30'),('2026-10-24T22:30:00+00:00','2026-10-25'),('2026-10-25T01:30:00+00:00','2026-10-25'),('2026-10-25T23:30:00+00:00','2026-10-26')]
        for instant,expected in cases:self.assertEqual(jobs.berlin_today(datetime.fromisoformat(instant)).isoformat(),expected)
        with self.assertRaises(hs.HealthStateError):jobs.berlin_today(datetime(2026,4,1))
    def test_strict_date_format(self):
        for value in ('2026-4-01','2026-04-01T00:00:00',True,'2026-02-29'):
            with self.assertRaises(hs.HealthStateError):hs.parse_date(value)
    def test_new_module_import_safety(self):
        code='''import builtins,os,socket,sys
from unittest.mock import patch

def fail(*a,**kw): raise AssertionError('SIDE_EFFECT')
with patch.object(builtins,'open',side_effect=fail),patch.object(os,'open',side_effect=fail),patch.object(socket,'socket',side_effect=fail):
 import health_state,health_jobs,health_daily,health_backfill,renpho_backfill
 assert not any(x in sys.modules for x in ('garminconnect','garth','telebot','googleapiclient','renpho'))
'''
        r=subprocess.run([sys.executable,'-B','-c',code],capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stderr)

if __name__=='__main__':unittest.main()
