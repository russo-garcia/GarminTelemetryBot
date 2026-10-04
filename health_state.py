"""Private, contiguous health state and a separate stable job lease (POSIX)."""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, timedelta
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
from typing import Optional

SCHEMA_VERSION = 1
DEFAULT_HISTORY_START = '2026-04-01'
DEFAULT_DIRECTORY = Path(__file__).resolve().parent / '.health-runtime'


class HealthStateError(RuntimeError):
    """Invalid state or unsafe filesystem; never fall back to an empty state."""


class HealthJobBusy(TimeoutError):
    pass


def parse_date(value):
    if type(value) is not str or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise HealthStateError('Expected an ISO calendar date')
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HealthStateError('Invalid calendar date') from None


@dataclass(frozen=True)
class FinalizationState:
    history_start: date
    finalized_through: Optional[date] = None

    @property
    def next_date(self):
        return self.history_start if self.finalized_through is None else self.finalized_through + timedelta(days=1)

    def as_dict(self):
        return {'schema_version': SCHEMA_VERSION,
                'history_start': self.history_start.isoformat(),
                'finalized_through': self.finalized_through.isoformat() if self.finalized_through else None}


def _validate(state, today):
    if type(today) is not date or type(state.history_start) is not date or state.history_start >= today:
        raise HealthStateError('History must start before today; check the clock')
    end = state.finalized_through
    if end is not None and (type(end) is not date or end < state.history_start or end >= today):
        raise HealthStateError('Invalid or future finalization watermark')
    return state


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise HealthStateError('Duplicate state key')
        result[key] = value
    return result


def decode_state(raw, today):
    try:
        obj = json.loads(raw, object_pairs_hook=_unique_object)
        if (type(obj) is not dict or set(obj) != {'schema_version', 'history_start', 'finalized_through'}
                or type(obj['schema_version']) is not int or obj['schema_version'] != SCHEMA_VERSION):
            raise HealthStateError('Unsupported health state schema')
        return _validate(FinalizationState(parse_date(obj['history_start']),
                         None if obj['finalized_through'] is None else parse_date(obj['finalized_through'])), today)
    except (ValueError, TypeError, UnicodeError):
        raise HealthStateError('Invalid health state; manual investigation required') from None


_registry_guard = threading.Lock()
_mutexes = {}
_fds = set()


def _child_after_fork():
    global _registry_guard, _mutexes, _fds
    for fd in _fds:
        os.close(fd)  # Never LOCK_UN an inherited open-file description.
    _registry_guard = threading.Lock()
    _mutexes = {}
    _fds = set()


os.register_at_fork(before=lambda: _registry_guard.acquire(),
                    after_in_parent=lambda: _registry_guard.release(),
                    after_in_child=_child_after_fork)


def _private_file(info):
    return (stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1)


def _sync_directory(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class HealthStore:
    def __init__(self, directory=None):
        directory = DEFAULT_DIRECTORY if directory is None else Path(directory)
        if not directory.is_absolute():
            raise HealthStateError('Health runtime directory must be absolute')
        self.directory = directory.parent.resolve() / directory.name
        self.path = self.directory / 'health_state.json'
        self.lock_path = self.directory / 'health_job.lock'

    def _directory(self):
        try:
            self.directory.mkdir(mode=0o700)
        except FileExistsError:
            pass
        else:
            _sync_directory(self.directory.parent)
        info = self.directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise HealthStateError('Health runtime directory must be owner-only and not a symlink')

    @contextmanager
    def lease(self):
        """Nonblocking: health job -> Garmin auth; never acquire in reverse order."""
        self._directory()
        with _registry_guard:
            mutex = _mutexes.setdefault(str(self.lock_path), threading.Lock())
        if not mutex.acquire(blocking=False):
            raise HealthJobBusy('Another health job is active')
        fd = None
        acquired = False
        lease = None
        pid = os.getpid()
        try:
            with _registry_guard:
                fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
                _fds.add(fd)
            info = os.fstat(fd)
            if not _private_file(info):
                raise HealthStateError('Health job lock is unsafe')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise HealthJobBusy('Another health job is active') from None
            acquired = True
            lease = _Lease(self, info)
            lease._check()
            yield lease
        finally:
            if lease is not None:
                lease.active = False
            if os.getpid() == pid:
                try:
                    if fd is not None:
                        try:
                            if acquired:
                                fcntl.flock(fd, fcntl.LOCK_UN)
                        finally:
                            with _registry_guard:
                                _fds.discard(fd)
                            os.close(fd)
                finally:
                    mutex.release()


class _Lease:
    def __init__(self, store, lock_info):
        self.store = store
        self.identity = (lock_info.st_dev, lock_info.st_ino)
        self.pid, self.thread = os.getpid(), threading.get_ident()
        self.active = True

    def _check(self):
        if not self.active or self.pid != os.getpid() or self.thread != threading.get_ident():
            raise HealthStateError('Health lease is expired or inherited')
        info = self.store.lock_path.lstat()
        if not _private_file(info) or (info.st_dev, info.st_ino) != self.identity:
            raise HealthStateError('Stable health lock changed')

    def load(self, today):
        self._check()
        try:
            fd = os.open(self.store.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return None
        try:
            if not _private_file(os.fstat(fd)):
                raise HealthStateError('Health state file is unsafe')
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                raw = stream.read(4097)
            if len(raw) > 4096:
                raise HealthStateError('Health state file is oversized')
            return decode_state(raw, today)
        finally:
            os.close(fd)

    def initialize(self, history_start, today):
        if self.load(today) is not None:
            raise HealthStateError('Health state already exists')
        state = _validate(FinalizationState(history_start), today)
        self._publish(state)
        return state

    def advance(self, day, today):
        state = self.load(today)
        if state is None or type(day) is not date or day != state.next_date or day >= today:
            raise HealthStateError('Watermark cannot skip a date or finalize today')
        result = _validate(FinalizationState(state.history_start, day), today)
        self._publish(result)
        return result

    def _publish(self, state):
        self._check()
        # load() already rejected corrupt/unsafe current state under this lease.
        fd, temp = tempfile.mkstemp(prefix='.health_state.', suffix='.tmp', dir=self.store.directory)
        try:
            with os.fdopen(fd, 'wb') as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write((json.dumps(state.as_dict(), sort_keys=True) + '\n').encode())
                stream.flush()
                os.fsync(stream.fileno())
            self._check()
            os.replace(temp, self.store.path)
            _sync_directory(self.store.directory)
        finally:
            try:
                os.unlink(temp)
            except FileNotFoundError:
                pass
