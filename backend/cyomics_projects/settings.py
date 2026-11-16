"""Fixed public policy and bounded operations. Production activation is pending."""
import asyncio
from dataclasses import dataclass
import json
import inspect
import os
from pathlib import Path
import re
import stat
import time

AUTH_USER_URL = 'http://127.0.0.1:57421/auth/v1/user'
EXPECTED_ISSUER = 'http://127.0.0.1:57421/auth/v1'
EXPECTED_AUDIENCE = 'authenticated'
MAX_TOKEN_LIFETIME = 3600
DB_HOST = '172.19.0.2'
DB_PORT = 5432
DB_NAME = 'cyomicsv2_business_dev'
DB_USER = 'cyomicsv2_app_runtime'
RUNTIME_FILE = Path('/home/user01/Cy_Omics_v2.0/releases/backend-review-20261116-v1-projects-owner/runtime/business-runtime.json')
# Source gates, never HTTP/environment switches. Templates are not live policy.
AUTH_POLICY_VERIFIED = False
RUNTIME_START_APPROVED = False


class RuntimeNotApproved(RuntimeError):
    pass


class Fault(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code


def unavailable():
    return Fault(503, 'projects_unavailable')


def production_gate():
    if not AUTH_POLICY_VERIFIED or not RUNTIME_START_APPROVED:
        raise RuntimeNotApproved('Runtime and actual Auth policy require separate approval')


@dataclass
class CreateOutcome:
    write_started: bool = False
    commit_started: bool = False
    committed: bool = False
    rollback_confirmed: bool = False

    def failure(self):
        if (self.commit_started or self.committed) and not self.rollback_confirmed:
            return Fault(504, 'project_create_result_unknown')
        return unavailable()


def consume_task(task):
    if not task.cancelled():
        task.exception()  # Consume without logging exception text or parameters.


class Budget:
    def __init__(self, seconds=9):
        self.deadline = time.monotonic() + seconds
        self.create = CreateOutcome()

    def remaining(self):
        return max(0.0, self.deadline - time.monotonic())

    def check(self):
        if not self.remaining():
            raise TimeoutError()

    async def run(self, operation, cap=None, abort=None, discard=None):
        # A lazy factory prevents expired budgets from scheduling new I/O.
        timeout = self.remaining()
        if cap is not None:
            timeout = min(timeout, cap)
        if not callable(operation):
            if inspect.iscoroutine(operation):
                operation.close()
            raise TypeError('Budget.run requires a lazy operation factory')
        if timeout <= 0:
            raise TimeoutError()
        async def invoke():
            self.check()  # also check after event-loop scheduling delay
            return await operation()
        task = asyncio.create_task(invoke())
        def abandon():
            if abort is not None:
                abort()
            if not task.done():
                task.cancel()
                task.add_done_callback(discard or consume_task)
            elif discard is not None:
                discard(task)
            else:
                consume_task(task)
        try:
            done, _ = await asyncio.wait({task}, timeout=timeout)
        except BaseException:
            abandon()
            raise
        if task not in done:
            abandon()
            raise TimeoutError()
        return task.result()


def load_approved_anon_key():
    production_gate()
    from .anon_config import _load_fixed_anon_key
    return _load_fixed_anon_key()


def load_runtime_password():
    """Lazy future loader, unreachable through the currently closed factory."""
    production_gate()
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    file_fd = None
    data = None
    obj = {}
    try:
        root_stat = os.fstat(fd)
        if not stat.S_ISDIR(root_stat.st_mode) or root_stat.st_mode & 0o022:
            raise unavailable()
        for component in RUNTIME_FILE.parent.parts[1:]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
            s = os.fstat(fd)
            if not stat.S_ISDIR(s.st_mode) or s.st_mode & 0o022:
                raise unavailable()
        directory = os.fstat(fd)
        if directory.st_uid != os.getuid() or stat.S_IMODE(directory.st_mode) != 0o700:
            raise unavailable()
        file_fd = os.open(RUNTIME_FILE.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        s = os.fstat(file_fd)
        if not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid() or s.st_nlink != 1 or stat.S_IMODE(s.st_mode) != 0o600:
            raise unavailable()
        data = os.read(file_fd, 4097)
        if len(data) > 4096:
            raise unavailable()
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise unavailable()
                result[key] = value
            return result
        obj = json.loads(data.decode('utf-8'), object_pairs_hook=pairs)
        if type(obj) is not dict or set(obj) != {'schemaVersion','purpose','password'}:
            raise unavailable()
        if type(obj['schemaVersion']) is not int or obj['schemaVersion'] != 1 or obj['purpose'] != 'cyomicsv2_business_runtime':
            raise unavailable()
        password = obj['password']
        if not isinstance(password, str) or re.fullmatch(r'[A-Za-z0-9_-]{64}', password) is None:
            raise unavailable()
        return password
    finally:
        data = None
        if isinstance(obj, dict):
            obj.clear()
        if file_fd is not None:
            os.close(file_fd)
        os.close(fd)
