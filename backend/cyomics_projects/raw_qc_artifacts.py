"""Default-off, POSIX-only durability check for a trusted worker's published job.

No authentication, scientific validation, worker, DB commit, or runtime registration.
All paths/config/registration are server-owned. Receipts are consistency evidence,
not proof of process execution. No file bytes, names, or permissions are changed.
"""
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import stat
import time
from . import raw_qc_contract as c

PER_FILE = 67108864
TOTAL = 268435456
WALL_SECONDS = 30


class ArtifactFailure(RuntimeError):
    def __init__(self, code='artifact_integrity_failed'):
        self.code = code
        super().__init__(code)


def require(condition, code='artifact_integrity_failed'):
    if not condition:
        raise ArtifactFailure(code)


def identity(s):
    return (s.st_dev, s.st_ino, s.st_mode, s.st_nlink, s.st_uid,
            s.st_gid, s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def directory(fd):
    s = os.fstat(fd)
    require(stat.S_ISDIR(s.st_mode) and s.st_uid == os.geteuid()
            and stat.S_IMODE(s.st_mode) in (0o700, 0o500))
    return identity(s)


def regular(fd):
    s = os.fstat(fd)
    require(stat.S_ISREG(s.st_mode) and s.st_nlink == 1
            and s.st_uid == os.geteuid()
            and stat.S_IMODE(s.st_mode) in (0o600, 0o400)
            and 1 <= s.st_size <= PER_FILE)
    return identity(s)


def open_root(path):
    # Walk every component through descriptors; never resolve/follow a symlink.
    require(isinstance(path, Path) and path.is_absolute()
            and '..' not in path.parts and str(path) == os.path.normpath(str(path)),
            'invalid_server_root')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open('/', flags)
    try:
        for part in path.parts[1:]:
            following = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = following
        directory(fd)
        return fd
    except BaseException:
        os.close(fd)
        raise


def checked_job(job, registration):
    c.exact(job, c.PUBLIC_KEYS)
    require(type(job['schemaVersion']) is int and job['schemaVersion'] == 1)
    for key in ('job_id', 'request_id'):
        c.identifier(job[key])
    c.source_value(job['source'])
    c.execution_value(job['execution'])
    require(type(registration) is c.ExecutionRegistration,
            'execution_registration_unconfigured')
    require(registration.snapshot() == job['execution'])
    source = job['source']
    require(job['quant_family'] in c.FAMILIES.values() and job['params'] == {})
    request = c.request_sha(source['project_id'], source['dataset_version_id'], job['quant_family'])
    require(job['request_sha256'] == request and job['binding_sha256'] ==
            c.binding_sha(request, source, job['execution'], job['quant_family']))
    require(job['status'] == 'running' and job['observation'] == 'confirmed'
            and job['error'] is None and job['result'] is None
            and job['finished_at'] is None)
    c.integer(job['revision'], 2)
    require(c.parse_stamp(job['started_at']) >= c.parse_stamp(job['created_at']))
    return c.strict_document(c.canonical(job).encode(), c.MAX_RESPONSE)


def checked_receipt(receipt, job):
    # The larger private runner receipt has process diagnostics. Never copy those
    # into public results; only these mandatory provenance fields are consumed.
    require(type(receipt) is dict)
    source, execution = job['source'], job['execution']
    for key, expected in {
        'job_id': job['job_id'], 'dataset_version_id': source['dataset_version_id'],
        'file_id': source['file_id'], 'input_bytes': source['source_bytes'],
        'input_sha256': source['source_sha256'], 'quant_family': job['quant_family'],
        'script_sha256': execution['script']['sha256'],
        'executable_sha256': execution['executable']['sha256'],
        'entry_kind': execution['entry_kind'], 'limits_profile': execution['limits_profile'],
        'limits_sha256': execution['limits_sha256'],
    }.items():
        require(key in receipt and type(receipt[key]) is type(expected)
                and receipt[key] == expected)
    pins = receipt['outputs']
    c.exact(pins, c.OUTPUTS)
    result = {}
    for name in sorted(c.OUTPUTS):
        pin = pins[name]
        c.exact(pin, ('bytes', 'sha256'))
        c.integer(pin['bytes'], 1, PER_FILE)
        c.hash_value(pin['sha256'])
        result[name] = {'bytes': int(pin['bytes']), 'sha256': pin['sha256']}
    require(sum(p['bytes'] for p in result.values()) <= TOTAL)
    return result


class RawQCArtifacts:
    def __init__(self, publish_root=None, registration=None, *, clock=None):
        self.root = publish_root
        self.registration = registration
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def build_result(self, job, receipt, *, check=lambda: None):
        """Return a result only after read/hash/fsync/recheck succeeds.

        Caller must independently authenticate/claim/revalidate the job and later
        commit its CAS transition. This function never touches DB or retries R.
        """
        require(self.root is not None and self.registration is not None,
                'artifacts_unconfigured')
        deadline = time.monotonic() + WALL_SECONDS

        def budget():
            check()
            require(time.monotonic() < deadline, 'artifact_budget_expired')

        try:
            budget()
            bound = checked_job(job, self.registration)
            pins = checked_receipt(receipt, bound)
            require(os.name == 'posix' and os.geteuid() > 0
                    and hasattr(os, 'O_NOFOLLOW') and hasattr(os, 'pread'),
                    'unprivileged_posix_required')
            with ExitStack() as stack:
                def own(fd):
                    stack.callback(os.close, fd)
                    return fd
                parent = own(open_root(self.root))
                parent_id = directory(parent)
                flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
                folder = own(os.open(bound['job_id'], flags, dir_fd=parent))
                folder_id = directory(folder)
                require(set(os.listdir(folder)) == c.OUTPUTS)
                opened = []
                total = 0
                for name in sorted(c.OUTPUTS):
                    budget()
                    fd = own(os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=folder))
                    before = regular(fd)
                    require(before[6] == pins[name]['bytes'])
                    h = hashlib.sha256()
                    count = 0
                    while True:
                        budget()
                        chunk = os.pread(fd, 65536, count)
                        if not chunk:
                            break
                        count += len(chunk)
                        require(count <= pins[name]['bytes'] and total + count <= TOTAL)
                        h.update(chunk)
                    require(count == pins[name]['bytes'] and h.hexdigest() == pins[name]['sha256'])
                    require(regular(fd) == before)
                    total += count
                    opened.append((name, fd, before))
                for _, fd, _ in opened:
                    budget()
                    os.fsync(fd)
                for fd in (folder, parent):
                    budget()
                    os.fsync(fd)
                for name, fd, before in opened:
                    budget()
                    require(regular(fd) == before and identity(os.stat(name, dir_fd=folder, follow_symlinks=False)) == before)
                require(directory(folder) == folder_id and directory(parent) == parent_id
                        and identity(os.stat(bound['job_id'], dir_fd=parent, follow_symlinks=False)) == folder_id
                        and set(os.listdir(folder)) == c.OUTPUTS)
                reopened = own(open_root(self.root))
                require(directory(reopened) == parent_id)
                budget()
                artifacts = [{'name': name, **pins[name], 'media_type':
                    'text/plain; charset=utf-8' if name == 'R-session-info.txt' else
                    'text/tab-separated-values; charset=utf-8'} for name in sorted(c.OUTPUTS)]
                result = {'schemaVersion': 1, 'job_id': bound['job_id'],
                    'binding_sha256': bound['binding_sha256'], 'execution': bound['execution'],
                    'artifacts': artifacts, 'manifest_sha256': c.digest(c.canonical(artifacts)),
                    'verified_at': c.stamp(self.clock())}
                c.result_value(result, bound)
                budget()
                return c.strict_document(c.canonical(result).encode(), c.MAX_RESPONSE)
        except ArtifactFailure:
            raise
        except Exception:
            raise ArtifactFailure() from None
