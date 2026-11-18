"""Pure bounded preprocessing request contract. No IO, authorization or job run."""
from dataclasses import dataclass, field
import hashlib
import json
import re
from uuid import UUID

CONTRACT = 'preprocessing_log2_job_v0.1'
ENABLED = False
MAX_REQUEST_BYTES = 4096
FAMILIES = ('Intensity', 'LFQ intensity')
FLAG_KEYS = ('contaminant', 'reverse', 'site_only')
REQUEST_KEYS = frozenset(('request_id', 'quant_family', 'parameters'))
PARAMETER_KEYS = frozenset(('blank_policy', 'filter_flags', 'imputation', 'input_scale',
    'log2', 'normalization', 'output_scale', 'preset', 'pseudocount', 'seed', 'steps', 'zero_policy'))
STEPS = ('flags_filter', 'zero_and_blank_to_NA', 'log2_positive', 'normalization_none', 'imputation_none')
_UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z')
ERROR_CODES = frozenset(('invalid_preprocessing_request', 'preprocessing_request_too_large'))


class RequestError(ValueError):
    """Fixed safe code only; never incorporates an untrusted value or JSON error."""
    def __init__(self, code='invalid_preprocessing_request'):
        self.code = code if code in ERROR_CODES else 'invalid_preprocessing_request'
        super().__init__(self.code)


def _need(value):
    if not value:
        raise RequestError()


def _canonical_uuid(value):
    # Equivalent to validation.canonical_uuid for plain strings; no settings
    # import or persistence/configuration dependency is required for this slice.
    return type(value) is str and _UUID.fullmatch(value) is not None and str(UUID(value)) == value


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf8')


def _document(raw):
    _need(type(raw) is bytes and len(raw) > 0 and not raw.startswith(b'\xef\xbb\xbf'))
    if len(raw) > MAX_REQUEST_BYTES:
        raise RequestError('preprocessing_request_too_large')
    def pairs(items):
        result = {}
        for key, value in items:
            _need(key not in result)
            result[key] = value
        return result
    def reject(_value):
        raise RequestError()
    value = json.loads(raw.decode('utf8', errors='strict'), object_pairs_hook=pairs,
        parse_constant=reject)
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        _need(depth <= 8)
        if type(item) is str:
            item.encode('utf8', errors='strict')  # no escaped lone surrogates
        elif type(item) is dict:
            pending.extend((v, depth+1) for pair in item.items() for v in pair)
        elif type(item) is list:
            pending.extend((v, depth+1) for v in item)
        else:
            _need(type(item) in (bool, int, type(None)))  # no float/NaN/Infinity aliases
    return value


def _parameter_value(flags):
    _need(type(flags) is dict and set(flags) == set(FLAG_KEYS))
    _need(all(type(flags[key]) is bool for key in FLAG_KEYS))
    return {'blank_policy':'to_NA', 'filter_flags':{key:flags[key] for key in FLAG_KEYS},
        'imputation':'none', 'input_scale':'raw_nonnegative', 'log2':'positive_only',
        'normalization':'none', 'output_scale':'log2', 'preset':'positive_log2_zero_na_no_impute_v1',
        'pseudocount':0, 'seed':None, 'steps':list(STEPS), 'zero_policy':'to_NA'}


def canonical_parameters(filter_flags):
    """Reconstruct canonical bytes by named boolean flags; never mutate the caller."""
    return _canonical(_parameter_value(filter_flags))


def _parameters(value):
    _need(type(value) is dict and set(value) == PARAMETER_KEYS)
    expected = _parameter_value(value['filter_flags'])
    for key, wanted in expected.items():
        actual = value[key]
        if key == 'filter_flags':
            continue  # exact dict keys and strict bool types already checked
        _need(type(actual) is type(wanted) and actual == wanted)
        if key == 'steps':
            _need(all(type(item) is str for item in actual))
    return expected


@dataclass(frozen=True, slots=True)
class PreprocessingRequest:
    """Immutable normalized request, not SourceRef, Owner grant or execution proof."""
    request_id: str
    quant_family: str
    filter_flags: tuple[tuple[str, bool], ...]
    parameters_bytes: bytes = field(repr=False)
    parameters_sha256: str
    canonical_request_bytes: bytes = field(repr=False)
    request_sha256: str

    def parameters_value(self):
        """Detached JSON value for a future adapter; cannot mutate this snapshot."""
        return json.loads(self.parameters_bytes)

    def request_value(self):
        return json.loads(self.canonical_request_bytes)


def request_input(raw):
    """Parse exactly the3-key body; reject every client-supplied server binding.

    A future authenticated handler must independently resolve the route and
    authoritative SourceRef. The returned request hash is intentionally unbound
    to Owner/project/source/execution and must never be used as authorization.
    """
    try:
        value = _document(raw)
        _need(type(value) is dict and set(value) == REQUEST_KEYS)
        _need(_canonical_uuid(value['request_id']))
        family = value['quant_family']
        _need(type(family) is str and family in FAMILIES)
        parameters = _parameters(value['parameters'])
        parameter_bytes = _canonical(parameters)
        # Build all accepted keys from validated scalars and server constants,
        # never serialize the caller's original mutable dictionary by reference.
        body = {'request_id':value['request_id'], 'quant_family':family, 'parameters':parameters}
        request_bytes = _canonical(body)
        return PreprocessingRequest(value['request_id'], family,
            tuple((key,parameters['filter_flags'][key]) for key in FLAG_KEYS),
            parameter_bytes, hashlib.sha256(parameter_bytes).hexdigest(),
            request_bytes, hashlib.sha256(request_bytes).hexdigest())
    except RequestError:
        raise
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
        raise RequestError() from None
