"""Raw-only v2 contract. No file I/O, authentication shortcut or text decoding of content."""
import base64
import hashlib
import re
from datetime import timezone
from .settings import Fault
from .validation import canonical_uuid

MAX_BYTES = 26214400
UUID = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
PREFIX = rb'/api/v1/projects/(' + UUID.encode() + rb')/'
PATTERNS = [
    ('save', 'POST', PREFIX + rb'raw-uploads'),
    ('receipt', 'GET', PREFIX + rb'raw-uploads/(' + UUID.encode() + rb')'),
    ('list', 'GET', PREFIX + rb'files'),
    ('content', 'GET', PREFIX + rb'files/(' + UUID.encode() + rb')/content'),
]

def classify(scope):
    """Only exact method, canonical raw path and absent query select a profile."""
    raw = scope.get('raw_path')
    if not isinstance(raw, bytes) or scope.get('query_string', b''):
        return None
    for kind, method, pattern in PATTERNS:
        match = re.fullmatch(pattern, raw)
        if match and scope.get('method') == method and scope.get('path') == raw.decode('ascii'):
            return {'kind': kind, 'project_id': match[1].decode(),
                    'id': match[2].decode() if match.lastindex == 2 else None,
                    'long': kind in ('save', 'content')}
    return None

def owns(scope):
    # Malformed/encoded aliases remain API failures, never static Worker routes.
    path = scope.get('path', '')
    return isinstance(path, str) and path.startswith('/api/v1/projects/') and any(
        part in ('raw-uploads', 'files') for part in path.split('/'))

def reject_route(scope):
    if scope.get('query_string', b''):
        raise Fault(400, 'invalid_upload_query')
    raw = scope.get('raw_path', b'')
    if isinstance(raw, bytes) and any(re.fullmatch(p, raw) for _, _, p in PATTERNS):
        raise Fault(405, 'method_not_allowed')
    raise Fault(404, 'file_not_found')

def single(request, name, required=False):
    values = request.headers.getlist(name)
    if len(values) > 1 or (required and len(values) != 1):
        raise Fault(400, 'invalid_upload')
    return values[0] if values else None

def valid_filename(value):
    return (isinstance(value, str) and 1 <= len(value.encode('utf8')) <= 255
            and value.lower().endswith('.txt') and not any(
                ord(c) < 32 or 127 <= ord(c) <= 159 or c in '/\\' or 0xD800 <= ord(c) <= 0xDFFF for c in value))

def input_headers(request):
    if single(request, 'content-type', True) != 'application/octet-stream':
        raise Fault(415, 'unsupported_media_type')
    if single(request, 'content-encoding') not in (None, 'identity'):
        raise Fault(415, 'unsupported_media_type')
    request_id = single(request, 'idempotency-key', True)
    name = single(request, 'x-cyomics-filename', True)
    size = single(request, 'x-cyomics-file-bytes', True)
    digest = single(request, 'x-cyomics-file-sha256', True)
    try:
        if not canonical_uuid(request_id) or re.fullmatch(r'[A-Za-z0-9_-]+', name) is None or len(name) > 340:
            raise ValueError()
        raw = base64.b64decode(name + '=' * (-len(name) % 4), altchars=b'-_', validate=True)
        if base64.urlsafe_b64encode(raw).decode().rstrip('=') != name:
            raise ValueError()
        filename = raw.decode('utf8', errors='strict')
        if not valid_filename(filename) or single(request, 'x-cyomics-file-format', True) != 'proteinGroups_txt':
            raise ValueError()
        if re.fullmatch(r'[1-9][0-9]{0,7}', size) is None or not 1 <= int(size) <= MAX_BYTES:
            raise Fault(413, 'upload_too_large')
        if re.fullmatch(r'[0-9a-f]{64}', digest) is None:
            raise ValueError()
        length = single(request, 'content-length')
        if length is not None and (not length.isascii() or not length.isdecimal() or int(length) != int(size)):
            raise ValueError()
        if length is not None and single(request, 'transfer-encoding') is not None:
            raise ValueError()
        return {'request_id': request_id, 'filename': filename, 'format': 'proteinGroups_txt', 'bytes': int(size), 'sha256': digest}
    except Fault:
        raise
    except (ValueError, TypeError, UnicodeError):
        raise Fault(400, 'invalid_upload') from None

def check_content(raw, value):
    if len(raw) != value['bytes']:
        raise Fault(400, 'invalid_upload')
    if hashlib.sha256(raw).hexdigest() != value['sha256']:
        raise Fault(400, 'upload_checksum_mismatch')

COLUMNS = 'file_id, project_id, request_id, filename, format, bytes, sha256, status, created_at'

def public_file(row):
    result = dict(zip(('file_id','project_id','request_id','filename','format','bytes','sha256','status','createdAt'), row))
    for name in ('file_id','project_id','request_id'):
        result[name] = str(result[name])
        if not canonical_uuid(result[name]):
            raise ValueError('Invalid persisted file')
    result['createdAt'] = result['createdAt'].astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
    if not valid_filename(result['filename']) or result['format'] != 'proteinGroups_txt' or result['status'] != 'stored' or type(result['bytes']) is not int or not 1 <= result['bytes'] <= MAX_BYTES or re.fullmatch('[0-9a-f]{64}', result['sha256']) is None:
        raise ValueError('Invalid persisted file')
    return result
