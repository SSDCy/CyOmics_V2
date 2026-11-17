"""Pure bounded FASTA identity index; no paths, storage, Owner or annotation API."""
from dataclasses import dataclass,fields
import hashlib
from types import MappingProxyType
from .sequence.vendor_domain_r1 import normalize_sequence,SEQUENCE_IDENTITY_CONTRACT
INDEX_CONTRACT='fasta_ascii_identity_index_v0.1'

@dataclass(frozen=True,slots=True)
class Limits:
    max_bytes:int=26214400
    max_records:int=50000
    max_id_length:int=256
    max_id_characters:int=2000000
    max_line_length:int=1000000
    max_sequence_length:int=1000000
    max_lines:int=500000
    max_chunks:int=500000

class FastaIndexError(ValueError):
    def __init__(self,code,line=0):
        self.code,self.line=code,line
        self.category='unsupported' if code in ('NON_ASCII_FASTA','UTF8_BOM','LONE_CR') else 'invalid_or_limit'
        super().__init__(f'{code} (line {line})')

@dataclass(frozen=True,slots=True,repr=False)
class Record:
    ordinal:int
    header_line:int
    header_raw:str
    id:str
    sequence_lines:tuple[str,...]
    sequence_raw:str
    raw_sequence_sha256:str
    canonical_sequence:str|None
    sequence_sha256:str|None
    normalization_status:str
    canonical_issue:str|None

@dataclass(frozen=True,slots=True,repr=False)
class FastaIndex:
    source_bytes:int
    source_sha256:str
    records:tuple[Record,...]
    id_ordinals:object
    blank_lines:int
    line_count:int
    sequence_identity_contract:str=SEQUENCE_IDENTITY_CONTRACT
    index_contract:str=INDEX_CONTRACT

    def records_for_id(self,identifier):
        if type(identifier) is not str:raise FastaIndexError('INVALID_ID_QUERY')
        return tuple(self.records[n-1] for n in self.id_ordinals.get(identifier,()))

def _reject(code,line=0):raise FastaIndexError(code,line)

def _limits(value):
    if type(value) is not Limits:_reject('INVALID_LIMITS')
    ceiling=Limits()
    for field in fields(value):
        n=getattr(value,field.name)
        if type(n) is not int or not 1<=n<=getattr(ceiling,field.name):_reject('INVALID_LIMITS')
    return value

def parse_fasta(chunks,limits=Limits()):
    """Only bytes or iterable-of-bytes; complete result after all checks.

    Vendor identity v0.1 is retained: join LF/CRLF sequence lines without trim,
    uppercase only valid ASCII residues incl BJOUXZ. Invalid/empty sequences
    remain indexed, with raw identity and no canonical identity. Duplicate IDs
    preserve every ordinal. UTF-8 is checked, then non-ASCII/BOM is refused.
    """
    limits=_limits(limits)
    if type(chunks) is bytes:chunks=(chunks,)
    elif isinstance(chunks,(str,bytearray,memoryview)):_reject('INVALID_INPUT')
    try:iterator=iter(chunks)
    except TypeError:_reject('INVALID_INPUT')
    data=bytearray();digest=hashlib.sha256();count=0
    for chunk in iterator:
        count+=1
        if count>limits.max_chunks:_reject('CHUNK_LIMIT')
        if type(chunk) is not bytes:_reject('INVALID_CHUNK')
        if len(chunk)>limits.max_bytes-len(data):_reject('BYTE_LIMIT')
        digest.update(chunk);data.extend(chunk)
    try:text=data.decode('utf-8','strict')
    except UnicodeError:_reject('INVALID_UTF8')
    if text.startswith('\ufeff'):_reject('UTF8_BOM')
    if not text.isascii():_reject('NON_ASCII_FASTA')
    line_count=text.count('\n')+(not text.endswith('\n'))
    if line_count>limits.max_lines:_reject('LINE_LIMIT')
    lines=text.split('\n')
    if lines[-1]=='':lines.pop()
    records=[];index={};header=None;header_line=0;identifier=None
    sequence=[];length=0;blank_lines=0;id_characters=0
    def finish():
        if header is None:return
        raw=''.join(sequence)
        try:canonical=normalize_sequence(raw)
        except ValueError:canonical=None
        ordinal=len(records)+1
        issue=None if canonical is not None else ('empty_sequence' if not raw else
            'sequence_whitespace' if any(c.isspace() for c in raw) else
            'stop_or_gap' if '*' in raw or '-' in raw else 'illegal_residue')
        records.append(Record(ordinal,header_line,header,identifier,tuple(sequence),raw,
            hashlib.sha256(raw.encode('ascii')).hexdigest(),canonical,
            hashlib.sha256(canonical.encode('ascii')).hexdigest() if canonical is not None else None,
            'valid_canonical_residues' if canonical is not None else 'invalid_no_canonical_sequence_identity',issue))
        index.setdefault(identifier,[]).append(ordinal)
    for number,wrapped in enumerate(lines,1):
        line=wrapped.removesuffix('\r')
        if '\r' in line or (number==len(lines) and not text.endswith('\n') and wrapped.endswith('\r')):_reject('LONE_CR',number)
        if len(line)>limits.max_line_length:_reject('LINE_LENGTH_LIMIT',number)
        if line=='':
            blank_lines+=1
            if header is not None:sequence.append(line)
            continue
        if line.startswith('>'):
            finish()
            if len(records)>=limits.max_records:_reject('RECORD_LIMIT',number)
            header=line[1:];tokens=header.split()
            if not tokens:_reject('EMPTY_ID',number)
            identifier=tokens[0]
            if len(identifier)>limits.max_id_length:_reject('ID_LENGTH_LIMIT',number)
            id_characters+=len(identifier)
            if id_characters>limits.max_id_characters:_reject('ID_CHARACTER_LIMIT',number)
            header_line=number;sequence=[];length=0
        else:
            if header is None:_reject('SEQUENCE_BEFORE_HEADER',number)
            length+=len(line)
            if length>limits.max_sequence_length:_reject('SEQUENCE_LENGTH_LIMIT',number)
            sequence.append(line)
    finish()
    if not records:_reject('NO_RECORDS')
    return FastaIndex(len(data),digest.hexdigest(),tuple(records),
        MappingProxyType({k:tuple(v) for k,v in index.items()}),blank_lines,len(lines))
