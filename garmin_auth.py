"""Side-effect-free POSIX Garmin ownership boundary (no login on import).

All participating writers/readers must use the same absolute paths. Kernel flock
is advisory: it cannot coordinate unmodified processes. Never unlink the lock.
"""
from contextlib import contextmanager
import fcntl
import math
import os
from pathlib import Path
import stat
import threading
import time


class GarminBusy(TimeoutError):
    """No ownership obtained. No client was constructed and no login attempted."""


class GarminUnavailable(RuntimeError):
    """Sanitized client construction/login/request failure."""


class LeaseExpired(RuntimeError):
    pass


_registry_guard = threading.Lock()
_thread_locks = {}
_active_fds = set()


def _after_fork():
    # The child must not prolong the parent's open-file-description lock lifetime.
    # close, NEVER LOCK_UN: unlocking an inherited description unlocks the parent.
    global _registry_guard, _thread_locks, _active_fds
    for fd in _active_fds:
        os.close(fd)
    _active_fds = set()
    _registry_guard = threading.Lock()
    _thread_locks = {}


os.register_at_fork(before=lambda: _registry_guard.acquire(),
                    after_in_parent=lambda: _registry_guard.release(),
                    after_in_child=_after_fork)


def _absolute(value):
    p = Path(value)
    if not p.is_absolute():
        raise ValueError('Coordinator paths must be absolute')
    # Canonicalize parent aliases, never follow the final token/lock symlink.
    return p.parent.resolve() / p.name


def _plain(value):
    """Detach results; never return SDK clients, callbacks, lazy iterators or handles."""
    if type(value) in (str, int, float, bool, bytes, type(None)):
        return value
    if type(value) is list:
        return [_plain(item) for item in value]
    if type(value) is tuple:
        return tuple(_plain(item) for item in value)
    if type(value) is dict and all(type(k) is str for k in value):
        return {k: _plain(v) for k, v in value.items()}
    raise GarminUnavailable('Garmin returned an unsupported non-data result')


class _Session:
    def __init__(self, client, *, reader=False):
        self.__client = client
        self.__reader = reader
        self.__pid = os.getpid()
        self.__thread = threading.get_ident()
        self.__active = True

    def _close(self):
        self.__active = False
        self.__client = None

    def _call(self, method, *args, **kwargs):
        if (not self.__active or self.__pid != os.getpid()
                or self.__thread != threading.get_ident()):
            raise LeaseExpired('Garmin session is outside its owning lifetime/thread')
        allowed = {'get'} if self.__reader else {'get_activities', 'download_activity', 'get_stats', 'get_sleep_data', 'get_weigh_ins'}
        if method not in allowed:
            raise ValueError('Operation is outside the acquisition interface')
        try:
            if method == 'download_activity':
                kwargs['dl_fmt'] = self.__client.ActivityDownloadFormat.ORIGINAL
            return _plain(getattr(self.__client, method)(*args, **kwargs))
        except GarminUnavailable:
            raise
        except Exception:
            # Reader failures (e.g. REFRESH_REQUIRED) are its guarded adapter's
            # already-sanitized control protocol. They must leave this context.
            if self.__reader:
                raise
            raise GarminUnavailable('Garmin acquisition failed') from None

    def get_activities(self, start, count):
        return self._call('get_activities', start, count)

    def download_activity(self, activity_id):
        return self._call('download_activity', activity_id)

    def get_stats(self, date):
        return self._call('get_stats', date)

    def get_sleep_data(self, date):
        return self._call('get_sleep_data', date)

    def get_weigh_ins(self, startdate, enddate):
        return self._call('get_weigh_ins', startdate, enddate)

    def get(self, path):
        return self._call('get', path)


class GarminCoordinator:
    def __init__(self, token_store, lock_path, *, timeout=5.0):
        self.token_store = _absolute(token_store)
        self.lock_path = _absolute(lock_path)
        if self.token_store == self.lock_path:
            raise ValueError('Token store and stable lock must be different files')
        self.timeout = float(timeout)
        if not math.isfinite(self.timeout) or self.timeout < 0:
            raise ValueError('Timeout must be finite and nonnegative')

    @contextmanager
    def _ownership(self, *, read_only_lock=False):
        deadline = time.monotonic() + self.timeout
        with _registry_guard:
            mutex = _thread_locks.setdefault(str(self.lock_path), threading.Lock())
        if not mutex.acquire(timeout=max(0, deadline - time.monotonic())):
            raise GarminBusy('Garmin is busy; retry after the current operation')
        fd = None
        acquired = False
        pid = os.getpid()
        try:
            # No auto-mkdir, no credential loading, no CWD-dependent fallback.
            with _registry_guard:
                flags = os.O_RDONLY if read_only_lock else os.O_RDWR | os.O_CREAT
                fd = os.open(self.lock_path, flags | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
                _active_fds.add(fd)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError('Coordinator lock must be an owner-only regular file')
            if self.token_store.is_symlink():
                raise ValueError('Garmin token store must not be a symlink')
            try:
                token_info = self.token_store.stat()
            except FileNotFoundError:
                token_info = None
            if token_info and not stat.S_ISREG(token_info.st_mode):
                raise ValueError('Garmin token-store path must name a regular file')
            if token_info and (token_info.st_dev, token_info.st_ino) == (info.st_dev, info.st_ino):
                raise ValueError('Lock inode must not be the token-store inode')
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except BlockingIOError:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise GarminBusy('Garmin is busy; retry after the current operation') from None
                    time.sleep(min(0.025, remaining))
            published = self.lock_path.lstat()
            if (published.st_dev, published.st_ino) != (info.st_dev, info.st_ino):
                raise ValueError('Stable coordinator lock was replaced; ownership refused')
            yield
        finally:
            if os.getpid() == pid:
                try:
                    if fd is not None:
                        try:
                            if acquired:
                                fcntl.flock(fd, fcntl.LOCK_UN)
                        finally:
                            with _registry_guard:
                                _active_fds.discard(fd)
                            os.close(fd)
                finally:
                    mutex.release()

    @contextmanager
    def session(self, client_factory):
        """Factory creates an unauthenticated NEW SDK client, only after ownership.

        Collector login restores/refreshes its current store within this lease.
        No raw SDK object or method is returned to callers.
        """
        with self._ownership():
            try:
                client = client_factory()
                client.login(str(self.token_store))
            except Exception:
                raise GarminUnavailable('Garmin session initialization failed') from None
            session = _Session(client)
            try:
                yield session
            finally:
                session._close()
                # No client cache or global SDK session survives to another lease.
                client = None

    @contextmanager
    def read_only(self, guarded_loader):
        """Integration seam for the EXISTING guarded Phase 2B.1 reader.

        guarded_loader(absolute_store) must load a fresh reader with login, refresh,
        token publication and non-whitelisted HTTP disabled BEFORE loading tokens.
        This coordinator supplies ownership, NOT the reader's auth/HTTP policy.
        Use the audited deployed_reader adapter, never Garmin.login as this loader.
        Catch REFRESH_REQUIRED only OUTSIDE this context; then notify the owner.
        Lock order: this auth lease, then the independent prescription-state lease.
        """
        # The owner/operator must provision the stable inode first. O_RDONLY
        # allows the audited reader's no-filesystem-writes guard to remain active.
        with self._ownership(read_only_lock=True):
            reader = guarded_loader(str(self.token_store))
            session = _Session(reader, reader=True)
            try:
                yield session
            finally:
                session._close()
                reader = None
