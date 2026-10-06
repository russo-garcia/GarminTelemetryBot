import json
import multiprocessing as mp
import os
from pathlib import Path
import signal
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from garmin_auth import GarminCoordinator, GarminBusy, GarminUnavailable, LeaseExpired

class FakeGarmin:
    ActivityDownloadFormat = SimpleNamespace(ORIGINAL='original')
    def __init__(self): self.revision=None
    def login(self, path): self.revision=json.loads(Path(path).read_text())['revision']
    def get_activities(self, start, count):return [{'revision':self.revision,'start':start,'count':count}]
    def download_activity(self, activity_id, *, dl_fmt):
        assert dl_fmt=='original'
        return b'fictional-fit'
    def get_stats(self, date):return {'date':date,'steps':0}
    def get_sleep_data(self, date):return {'sleep':None}
    def get_weigh_ins(self, start, end):return {'dates':[start,end]}


def hold_process(store,lock,connection,delay):
    with GarminCoordinator(store,lock).session(FakeGarmin) as client:
        connection.send(('held',os.getpid(),client.get_stats('fictional')))
        time.sleep(delay)
    connection.send(('released',os.getpid()))
    connection.close()


def contend_process(store,lock,connection):
    try:
        with GarminCoordinator(store,lock,timeout=.1).session(FakeGarmin):connection.send('entered')
    except GarminBusy:connection.send('busy')
    connection.close()

class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve()
        self.store=self.root/'synthetic-session.json';self.store.write_text('{"revision":1}')
        self.lock=self.root/'owner.lock';self.lock.touch(mode=0o600);self.coordinator=GarminCoordinator(self.store,self.lock,timeout=.15)
    def tearDown(self):self.temp.cleanup()
    def test_single_thread_and_detached_output(self):
        with self.coordinator.session(FakeGarmin) as c:
            self.assertEqual(c.get_stats('date'),{'date':'date','steps':0})
            self.assertEqual(c.download_activity('1'),b'fictional-fit')
            self.assertFalse(hasattr(c,'login'));self.assertFalse(hasattr(c,'client'))
        self.assertTrue(self.lock.exists())
    def test_weight_facade_read_only_expiry_and_detachment(self):
        raw={'dates':['a','b']}
        with self.coordinator.session(FakeGarmin) as c:
            with patch.object(FakeGarmin,'get_weigh_ins',return_value=raw):
                result=c.get_weigh_ins('a','b');result['dates'].append('c')
                self.assertEqual(raw,{'dates':['a','b']})
            self.assertFalse(hasattr(c,'get_daily_weigh_ins'))
            self.assertFalse(hasattr(c,'add_weigh_in'))
            method=c.get_weigh_ins
        with self.assertRaises(LeaseExpired):method('a','b')
    def test_weight_request_failure_sanitized(self):
        with self.coordinator.session(FakeGarmin) as c:
            with patch.object(FakeGarmin,'get_weigh_ins',side_effect=RuntimeError('FAKE_SECRET')):
                with self.assertRaises(GarminUnavailable) as err:c.get_weigh_ins('a','b')
                self.assertNotIn('FAKE_SECRET',str(err.exception))
    def test_fresh_client_and_reload(self):
        created=[]
        def factory():
            obj=FakeGarmin();created.append(obj);return obj
        for revision in (1,2,3):
            self.store.write_text(json.dumps({'revision':revision}))
            with self.coordinator.session(factory) as c:self.assertEqual(c.get_activities(0,1)[0]['revision'],revision)
        self.assertEqual(len({id(c) for c in created}),3)
    def test_expired_proxy_and_bound_method(self):
        with self.coordinator.session(FakeGarmin) as c:method=c.get_stats
        for fn in (lambda:c.get_stats('x'),lambda:method('x')):
            with self.assertRaises(LeaseExpired):fn()
    def test_exception_release(self):
        with self.assertRaisesRegex(RuntimeError,'synthetic'):
            with self.coordinator.session(FakeGarmin):raise RuntimeError('synthetic')
        with self.coordinator.session(FakeGarmin) as c:self.assertEqual(c.get_sleep_data('x'),{'sleep':None})
    def test_constructor_failure_release_and_redaction(self):
        def factory():raise ValueError('FAKE_SECRET_MUST_NOT_APPEAR')
        with self.assertRaises(GarminUnavailable) as err:
            with self.coordinator.session(factory):pass
        self.assertNotIn('FAKE_SECRET',str(err.exception))
        with self.coordinator.session(FakeGarmin):pass
    def test_login_failure_release(self):
        class Broken(FakeGarmin):
            def login(self,path):raise RuntimeError('FAKE_SECRET')
        with self.assertRaises(GarminUnavailable):
            with self.coordinator.session(Broken):pass
        with self.coordinator.session(FakeGarmin):pass
    def test_request_failure_redacted(self):
        class Broken(FakeGarmin):
            def get_stats(self,date):raise ValueError('FAKE_SECRET')
        with self.coordinator.session(Broken) as c:
            with self.assertRaises(GarminUnavailable) as err:c.get_stats('x')
            self.assertNotIn('FAKE_SECRET',str(err.exception))
    def test_timeout_no_factory_or_fallback(self):
        factory_calls=[]
        with self.coordinator.session(FakeGarmin):
            start=time.monotonic()
            with self.assertRaises(GarminBusy):
                with self.coordinator.session(lambda:factory_calls.append(1)):pass
            self.assertLess(time.monotonic()-start,1)
        self.assertEqual(factory_calls,[])
    def test_different_instances_threads_serialize(self):
        barrier=threading.Barrier(4);events=[];errors=[]
        def worker():
            try:
                barrier.wait()
                with GarminCoordinator(self.store,self.lock,timeout=2).session(FakeGarmin):
                    events.append('enter');time.sleep(.02);events.append('exit')
            except Exception as e:errors.append(e)
        ts=[threading.Thread(target=worker) for _ in range(4)]
        for t in ts:t.start()
        for t in ts:t.join(3)
        self.assertEqual(errors,[]);self.assertEqual(events,['enter','exit']*4)
    def test_off_thread_client_rejected(self):
        errors=[]
        with self.coordinator.session(FakeGarmin) as c:
            def worker():
                try:c.get_stats('x')
                except LeaseExpired:errors.append('blocked')
            t=threading.Thread(target=worker);t.start();t.join()
        self.assertEqual(errors,['blocked'])
    def test_two_processes_serialize(self):
        ctx=mp.get_context('spawn');parent,child=ctx.Pipe()
        process=ctx.Process(target=hold_process,args=(str(self.store),str(self.lock),child,.35))
        process.start()
        try:
            self.assertTrue(parent.poll(5));self.assertEqual(parent.recv()[0],'held')
            with self.assertRaises(GarminBusy):
                with self.coordinator.session(FakeGarmin):pass
            self.assertTrue(parent.poll(5));self.assertEqual(parent.recv()[0],'released')
            with self.coordinator.session(FakeGarmin):pass
        finally:process.join(5);parent.close();child.close()
        self.assertEqual(process.exitcode,0)
    def test_process_competes_with_parent_thread_lease(self):
        ctx=mp.get_context('spawn');parent,child=ctx.Pipe()
        with self.coordinator.session(FakeGarmin):
            process=ctx.Process(target=contend_process,args=(str(self.store),str(self.lock),child));process.start()
            try:self.assertTrue(parent.poll(5));self.assertEqual(parent.recv(),'busy')
            finally:process.join(5)
        parent.close();child.close();self.assertEqual(process.exitcode,0)
    def test_kernel_release_after_sigkill(self):
        ctx=mp.get_context('spawn');parent,child=ctx.Pipe()
        process=ctx.Process(target=hold_process,args=(str(self.store),str(self.lock),child,60));process.start()
        try:
            self.assertTrue(parent.poll(5));self.assertEqual(parent.recv()[0],'held')
            inode=self.lock.stat().st_ino
            os.kill(process.pid,signal.SIGKILL);process.join(5)
            with self.coordinator.session(FakeGarmin):pass
            self.assertEqual(self.lock.stat().st_ino,inode)
        finally:
            if process.is_alive():process.kill();process.join()
            parent.close();child.close()
    def test_fork_does_not_unlock_parent_and_proxy_invalid(self):
        # Fork is tested only in this single-threaded test, not as an SDK workflow.
        r,w=os.pipe()
        with self.coordinator.session(FakeGarmin) as c:
            pid=os.fork()
            if pid==0:
                os.close(r)
                try:
                    try:c.get_stats('x');out=b'bad'
                    except LeaseExpired:out=b'expired'
                    try:
                        with self.coordinator.session(FakeGarmin):out+=b'-bad'
                    except GarminBusy:out+=b'-busy'
                    os.write(w,out)
                finally:os._exit(0)
            os.close(w)
            self.assertEqual(os.read(r,64),b'expired-busy');os.waitpid(pid,0);os.close(r)
        with self.coordinator.session(FakeGarmin):pass
    def test_stable_inode_with_atomic_token_replace(self):
        with self.coordinator.session(FakeGarmin):
            inode=self.lock.stat().st_ino
            tmp=self.root/'synthetic-next';tmp.write_text('{"revision":2}');os.replace(tmp,self.store)
        with self.coordinator.session(FakeGarmin) as c:
            self.assertEqual(c.get_activities(0,1)[0]['revision'],2)
            self.assertEqual(self.lock.stat().st_ino,inode)
    def test_reject_relative_paths_same_path_invalid_timeouts(self):
        for args in [('relative',self.lock),(self.store,'relative'),(self.store,self.store)]:
            with self.assertRaises(ValueError):GarminCoordinator(*args)
        for value in (-1,float('inf'),float('nan')):
            with self.assertRaises(ValueError):GarminCoordinator(self.store,self.lock,timeout=value)
    def test_symlink_store_and_lock_refused(self):
        link=self.root/'alias';link.symlink_to(self.store)
        with self.assertRaises(ValueError):
            with GarminCoordinator(link,self.lock).session(FakeGarmin):pass
        locklink=self.root/'lockalias';locklink.symlink_to(self.store)
        with self.assertRaises(OSError):
            with GarminCoordinator(self.store,locklink).session(FakeGarmin):pass
    def test_cwd_independence(self):
        before=os.getcwd()
        try:
            os.chdir('/')
            with self.coordinator.session(FakeGarmin) as c:self.assertEqual(c.get_activities(0,1)[0]['revision'],1)
        finally:os.chdir(before)
    def test_data_only_results(self):
        class Bad(FakeGarmin):
            def get_stats(self,date):return self
        with self.coordinator.session(Bad) as c:
            with self.assertRaises(GarminUnavailable):c.get_stats('x')
    def test_no_unlisted_operations(self):
        with self.coordinator.session(FakeGarmin) as c:
            with self.assertRaises(ValueError):c._call('login','forbidden')
    def test_reader_reload_and_expiry_protocol(self):
        loaded=[];refresh_attempts=[]
        class RefreshRequired(Exception):pass
        class GuardedReader:
            def get(self,path):raise RefreshRequired('REFRESH_REQUIRED')
        def guarded_loader(store):
            loaded.append(Path(store).read_text());return GuardedReader()
        for _ in range(2):
            with self.assertRaises(RefreshRequired):
                with self.coordinator.read_only(guarded_loader) as reader:reader.get('/read-only-test')
            # Handoff happens after release; collector can now acquire and refresh.
            with self.coordinator.session(FakeGarmin):refresh_attempts.append('owner-only')
        self.assertEqual(len(loaded),2);self.assertEqual(refresh_attempts,['owner-only']*2)
        with self.assertRaises(LeaseExpired):reader.get('/read-only-test')
    def test_reader_does_not_call_login_and_cannot_use_owner_methods(self):
        events=[]
        class Reader:
            def get(self,path):events.append('GET');return b'{}'
            def login(self,*args):raise AssertionError('must not login')
        before=self.store.read_bytes()
        with self.coordinator.read_only(lambda store:Reader()) as reader:
            self.assertEqual(reader.get('/test'),b'{}')
            with self.assertRaises(ValueError):reader.get_stats('x')
        self.assertEqual(self.store.read_bytes(),before);self.assertEqual(events,['GET'])
    def test_reader_loader_failure_releases(self):
        def loader(store):raise RuntimeError('REFRESH_REQUIRED')
        with self.assertRaises(RuntimeError):
            with self.coordinator.read_only(loader):pass
        with self.coordinator.session(FakeGarmin):pass

    def test_owner_creates_stable_lock_with_private_permissions(self):
        new=self.root/'new-owner.lock'
        with GarminCoordinator(self.store,new).session(FakeGarmin):pass
        self.assertEqual(new.stat().st_mode & 0o777,0o600)
    def test_reader_never_creates_lock_or_store(self):
        new=self.root/'unprovisioned.lock';calls=[]
        with self.assertRaises(FileNotFoundError):
            with GarminCoordinator(self.store,new).read_only(lambda store:calls.append(1)):pass
        self.assertEqual(calls,[]);self.assertFalse(new.exists())
    def test_reader_lock_open_is_read_only(self):
        real=os.open;flags=[]
        def tracked(path,value,*args,**kwargs):
            flags.append(value);return real(path,value,*args,**kwargs)
        with patch('garmin_auth.os.open',side_effect=tracked):
            with self.coordinator.read_only(lambda store:SimpleNamespace(get=lambda path:b'{}')):pass
        self.assertEqual(len(flags),1)
        self.assertFalse(flags[0] & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC))
    def test_hard_link_to_token_is_not_a_coordinator_lock(self):
        self.store.chmod(0o600)
        alias=self.root/'hardlink.lock';os.link(self.store,alias)
        with self.assertRaises(ValueError):
            with GarminCoordinator(self.store,alias).session(FakeGarmin):pass

if __name__=='__main__':unittest.main()
