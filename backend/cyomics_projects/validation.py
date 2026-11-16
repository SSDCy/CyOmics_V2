import base64
from datetime import datetime, timezone
import json
import re
import unicodedata
import urllib.parse
from uuid import UUID
from .settings import Fault, unavailable

UUID_PATTERN = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z')
TIME_PATTERN = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z\Z')


def canonical_uuid(value):
    return isinstance(value, str) and UUID_PATTERN.fullmatch(value) is not None and str(UUID(value)) == value


def _strict_json(raw):
    def pairs(items):
        obj = {}
        for key, value in items:
            if key in obj:
                raise ValueError()
            obj[key] = value
        return obj
    def reject_constant(value):
        raise ValueError()
    value = json.loads(raw.decode('utf-8', errors='strict'), object_pairs_hook=pairs, parse_constant=reject_constant)
    def scalars(item):
        if isinstance(item, str) and any(0xD800 <= ord(c) <= 0xDFFF for c in item):
            raise ValueError()
        if isinstance(item, dict):
            for key, child in item.items():
                scalars(key); scalars(child)
        elif isinstance(item, list):
            for child in item:
                scalars(child)
    scalars(value)
    return value


def strict_json(raw):
    try:
        return _strict_json(raw)
    except RecursionError:
        raise ValueError('JSON nesting rejected') from None


def decode_url64(value, max_bytes):
    if not isinstance(value, str) or not value or re.fullmatch(r'[A-Za-z0-9_-]+', value) is None:
        raise ValueError()
    raw = base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)
    if len(raw) > max_bytes or base64.urlsafe_b64encode(raw).decode().rstrip('=') != value:
        raise ValueError()
    return raw


def utc_time(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise unavailable()
    return value.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def encode_cursor(timestamp, project_id):
    obj = {'v': 1, 'createdAt': utc_time(timestamp), 'project_id': str(project_id)}
    if not canonical_uuid(obj['project_id']):
        raise unavailable()
    return base64.urlsafe_b64encode(json.dumps(obj, separators=(',', ':')).encode()).decode().rstrip('=')


def decode_cursor(value):
    try:
        if len(value) > 512:
            raise ValueError()
        obj = strict_json(decode_url64(value, 384))
        if type(obj) is not dict or set(obj) != {'v','createdAt','project_id'} or type(obj['v']) is not int or obj['v'] != 1:
            raise ValueError()
        if not canonical_uuid(obj['project_id']) or not isinstance(obj['createdAt'], str) or TIME_PATTERN.fullmatch(obj['createdAt']) is None:
            raise ValueError()
        timestamp = datetime.fromisoformat(obj['createdAt'].replace('Z','+00:00'))
        if utc_time(timestamp) != obj['createdAt']:
            raise ValueError()
        return timestamp, UUID(obj['project_id'])
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise Fault(400, 'invalid_project_query') from None


def query_pairs(raw):
    try:
        if len(raw) > 4096 or re.search(rb'%(?![0-9a-fA-F]{2})', raw):
            raise ValueError()
        pairs = urllib.parse.parse_qsl(raw.decode('ascii'), keep_blank_values=True, strict_parsing=True, encoding='utf-8', errors='strict')
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError()
            result[key] = value
        return result
    except (ValueError, UnicodeError):
        raise Fault(400, 'invalid_project_query') from None


def list_query(raw):
    obj = query_pairs(raw)
    if set(obj) - {'limit','cursor'}:
        raise Fault(400, 'invalid_project_query')
    value = obj.get('limit', '20')
    if re.fullmatch(r'[0-9]+', value) is None or not 1 <= int(value) <= 50:
        raise Fault(400, 'invalid_project_query')
    return int(value), decode_cursor(obj['cursor']) if 'cursor' in obj else None


def project_input(raw):
    try:
        obj = strict_json(raw)
        if type(obj) is not dict or set(obj) - {'name','description','species'} or 'name' not in obj:
            raise ValueError()
        values = {}
        for key, maximum in (('name',80),('description',1000),('species',200)):
            value = obj.get(key, '')
            if not isinstance(value, str):
                raise ValueError()
            value = value.strip()
            if len(value) > maximum or (key == 'name' and not value) or '\0' in value:
                raise ValueError()
            if key != 'description' and any(unicodedata.category(c) == 'Cc' for c in value):
                raise ValueError()
            values[key] = value
        return values
    except (ValueError, TypeError, UnicodeError):
        raise Fault(400, 'invalid_project') from None


PUBLIC_COLUMNS = 'project_id, name, description, species, visibility, created_at, updated_at'


def public_project(row):
    if row is None or len(row) != 7:
        raise unavailable()
    project_id, name, description, species, visibility, created, updated = row
    if not canonical_uuid(str(project_id)) or visibility != 'private' or not all(isinstance(v,str) for v in (name,description,species)):
        raise unavailable()
    return {'project_id':str(project_id),'name':name,'description':description,'species':species,'visibility':'private','createdAt':utc_time(created),'updatedAt':utc_time(updated)}
