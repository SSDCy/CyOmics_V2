"""Trusted worker source-export seam; no SQL, Owner input, HTTP, R, or statistics."""
from dataclasses import asdict,dataclass
import hashlib
import os
from pathlib import Path
from . import raw_qc_contract as c
from .import_report import SourceRef
from .protein_groups_parser import parse_protein_groups
from .r_raw_qc.raw_qc_runner import InputVersion
from .raw_qc_artifacts import open_root
from .raw_qc_checkpoint import JournalFailure,write_all

fsync=os.fsync
MAX_BYTES=26214400


class InputIntegrityFailure(RuntimeError):
    code='input_integrity_failed'


@dataclass(frozen=True,slots=True)
class BoundRaw:
    job_id:str
    attempt_id:str
    source:dict
    execution:dict
    content:bytes


@dataclass(frozen=True,slots=True)
class ExportedInput:
    server_key:str
    version:InputVersion
    selected_columns_sha256:str


class InputExporter:
    def __init__(self,root=None):self.root=root

    def prepare(self,job,attempt,bound,registration):
        if self.root is None:raise InputIntegrityFailure('input_export_unconfigured')
        try:
            if type(bound) is not BoundRaw or bound.job_id!=job['job_id'] or bound.attempt_id!=attempt:
                raise ValueError()
            # A frozen loader DTO is neither authorization nor an immutable dict.
            # Freeze ordinary dictionaries again at this synchronous seam before
            # parser callbacks or filesystem work; strict schema excludes proxies.
            c.exact(job,c.PUBLIC_KEYS);c.source_value(bound.source);c.execution_value(bound.execution)
            local_job=c.strict_document(c.canonical(job).encode('utf-8'),c.MAX_RESPONSE)
            s=local_job['source'];data=bound.content
            source_snapshot=c.strict_document(c.canonical(bound.source).encode('utf-8'),4096)
            execution_snapshot=c.strict_document(c.canonical(bound.execution).encode('utf-8'),4096)
            registered=c.strict_document(c.canonical(registration.snapshot()).encode('utf-8'),4096)
            if type(data) is not bytes or not 1<=len(data)<=MAX_BYTES or len(data)!=s['source_bytes'] or hashlib.sha256(data).hexdigest()!=s['source_sha256']:
                raise ValueError()
            if source_snapshot!=s or execution_snapshot!=local_job['execution'] or execution_snapshot!=registered:
                raise ValueError()
            source=SourceRef(s['project_id'],s['file_id'],'raw_bytea_v1:'+s['file_id']+':'+s['source_sha256'],s['source_bytes'],s['source_sha256'])
            # config/report hashes are immutable upstream provenance supplied by
            # the reviewed bound-source RPC. Do not reconstruct an import row or
            # relabel a current parse as an historical parser-release report.
            outcome=parse_protein_groups(source,(data[i:i+65536] for i in range(0,len(data),65536)))
            if not outcome.ok:raise ValueError()
            parsed=outcome.parsed
            selected=[asdict(column) for column in parsed.columns if column.quant_family==local_job['quant_family']]
            selected_sha=c.digest(c.canonical(selected))
            if (not selected or selected_sha!=s['selected_columns_sha256']
                or len(parsed.rows)!=s['row_count'] or len(parsed.columns)!=s['column_count']):raise ValueError()
            version=InputVersion(s['dataset_version_id'],s['file_id'],len(data),s['source_sha256'],len(parsed.rows),tuple(x.name_raw for x in parsed.columns))
        except Exception:raise InputIntegrityFailure('input_integrity_failed') from None
        # The exclusive artifact survives any partial write/fsync failure for QA.
        c.identifier(attempt);key=attempt+'.tsv';root=open_root(self.root)
        try:
            fd=os.open(key,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=root)
            try:write_all(fd,data);os.fchmod(fd,0o400);fsync(fd)
            finally:os.close(fd)
            fsync(root)
            read=os.open(key,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=root)
            try:
                info=os.fstat(read)
                if info.st_size!=len(data) or info.st_nlink!=1 or info.st_mode&0o777!=0o400 or os.pread(read,len(data)+1,0)!=data:
                    raise JournalFailure('input_snapshot_unconfirmed')
            finally:os.close(read)
        except Exception:raise JournalFailure('input_snapshot_unconfirmed') from None
        finally:os.close(root)
        return ExportedInput(key,version,selected_sha)
