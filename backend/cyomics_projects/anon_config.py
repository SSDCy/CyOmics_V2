"""Fixed, lazy ANON source. Payload checks reject mismatches, not verify signatures.

The private helpers accept synthetic paths/bytes for offline tests. Application
code uses only settings.load_approved_anon_key(), behind its two closed gates.
No credential cache, environment lookup, shell evaluation or logging exists.
"""
import io
import math
import os
from pathlib import Path
import re
import stat

from .settings import unavailable
from .validation import decode_url64, strict_json

_CONFIG = Path('/home/user01/Cy_Omics_v2.0/auth-dev/.env.keys')
_LIMIT = 16 * 1024
_STABLE = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns',
           'st_uid', 'st_gid', 'st_mode', 'st_nlink')
_TARGET_SPACE = r'[\t\n\v\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]'
# Broad JS whitespace identifies target attempts so rejected parseEnv lookalikes
# cannot be skipped in favour of a later key. It does not define acceptance.
_CANDIDATE = re.compile(rf'^{_TARGET_SPACE}*(?:export{_TARGET_SPACE}+)?ANON_KEY(?=[^A-Za-z0-9_]|$)')
# Conservative native-provider-probed subset: ASCII spaces outside the value.
# The complete provider includes parseEnv after its broader JS regex; that
# combination rejects the probed TAB/NBSP/interior-BOM variants on A Node20.
_ASSIGNMENT = re.compile(
    r'''^ *(?:export )?ANON_KEY *= *(?:"(?P<double>[^"\r\n]*)"|'(?P<single>[^'\r\n]*)'|(?P<bare>[^\s#'"\r\n\ufeff]+)) *(?:#[^\r\n\u2028\u2029]*)?$''')


def _require(value):
    if not value:
        raise ValueError()


def _finite_json(value):
    pending = [value]
    while pending:
        current = pending.pop()
        if type(current) is float:
            _require(math.isfinite(current))
        elif isinstance(current, dict):
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)


def _anon_value(value):
    _require(isinstance(value, str) and re.fullmatch(r'[\x21-\x7e]{1,4096}', value))
    parts = value.split('.')
    _require(len(parts) == 3)
    # Reuse the reviewed canonical base64url and strict JSON implementations.
    header = strict_json(decode_url64(parts[0], 2048))
    payload = strict_json(decode_url64(parts[1], 4096))
    decode_url64(parts[2], 4096)
    _finite_json(header)
    _finite_json(payload)
    _require(type(header) is dict and type(payload) is dict)
    algorithm = header.get('alg')
    _require(isinstance(algorithm, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', algorithm))
    _require(algorithm.lower() != 'none' and payload.get('role') == 'anon')
    return value


def _parse_unchecked(raw):
    _require(isinstance(raw, (bytes, bytearray)) and 0 < len(raw) <= _LIMIT)
    selected = None
    # Other assignments are never parsed into objects or kept in a cache. A
    # bounded transient input is unavoidable; no secure erasure of strings is claimed.
    with io.StringIO(raw.decode('utf-8-sig', errors='strict')) as lines:
        for line in lines:
            line = line.removesuffix('\n').removesuffix('\r')
            if not _CANDIDATE.match(line):
                continue
            _require(selected is None)
            match = _ASSIGNMENT.fullmatch(line)
            _require(match is not None)
            selected = next(value for value in match.groups() if value is not None)
    _require(selected is not None)
    return _anon_value(selected)


def _parse_anon_config(raw):
    try:
        return _parse_unchecked(raw)
    except Exception:
        pass
    # Raise outside the handler: no underlying exception or source line value
    # is retained as an exception context or exposed to the API/logging boundary.
    raise unavailable()


def _read_unchecked(path):
    _require(os.name == 'posix' and isinstance(path, Path) and path.is_absolute())
    _require(1 < len(path.parts) <= 128)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    uid, gid = os.getuid(), os.getgid()
    owned = []
    raw = bytearray()
    try:
        parent = os.open('/', directory_flags)
        owned.append(parent)
        anchor = os.fstat(parent)
        # The actual root descriptor is the trust anchor, including its mapped
        # root owner in a user namespace. No arbitrary third owner is accepted.
        owners, groups = {anchor.st_uid, uid}, {anchor.st_gid, gid}
        for component in (None, *path.parent.parts[1:]):
            if component is not None:
                parent = os.open(component, directory_flags, dir_fd=parent)
                owned.append(parent)
            info = os.fstat(parent)
            _require(stat.S_ISDIR(info.st_mode) and not info.st_mode & 0o022)
            _require(info.st_uid in owners and info.st_gid in groups)
        _require(info.st_uid == uid and info.st_gid == gid and stat.S_IMODE(info.st_mode) == 0o700)
        fd = os.open(path.name, file_flags, dir_fd=parent)
        owned.append(fd)
        before = os.fstat(fd)
        _require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1)
        _require(before.st_uid == uid and before.st_gid == gid and stat.S_IMODE(before.st_mode) == 0o600)
        _require(0 < before.st_size <= _LIMIT)
        while len(raw) <= _LIMIT:
            chunk = os.read(fd, min(1024, _LIMIT + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        _require(len(raw) <= _LIMIT and len(raw) == before.st_size)
        after = os.fstat(fd)
        _require(all(getattr(before, key) == getattr(after, key) for key in _STABLE))
        return _parse_unchecked(raw)
    finally:
        try:
            raw[:] = b'\0' * len(raw)
        finally:
            close_failed = False
            for fd in reversed(owned):
                try:
                    os.close(fd)
                except OSError:
                    close_failed = True
            if close_failed:
                raise ValueError()


def _read_anon_key(path):
    try:
        return _read_unchecked(path)
    except Exception:
        pass
    raise unavailable()


def _load_fixed_anon_key():
    return _read_anon_key(_CONFIG)
