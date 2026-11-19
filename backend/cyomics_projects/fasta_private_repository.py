"""Internal immutable Owner FASTA persistence. No HTTP/authentication or activation.

Actor must come from the existing verified authority. Uses UploadRepository's
actual transaction/GUC/cleanup boundary; never treats a DTO as authorization.
Source-backed index stores all original bytes plus the full candidate proof.
"""
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
from uuid import UUID, uuid4
from . import fasta_intake_contract as model
from .fasta_source_repository import _decode
from .settings import Fault
from .upload_repository import UploadRepository

SOURCE_COLUMNS = 's.source_id,s.project_id,s.request_id,s.filename,s.format,s.bytes,s.sha256,s.status,s.created_at,s.content'
READ = ('SELECT '+SOURCE_COLUMNS+',i.candidate_json,i.created_at '
        'FROM cyomics_app.fasta_sources s LEFT JOIN cyomics_app.fasta_index_versions i '
        'ON i.source_id=s.source_id AND i.project_id=s.project_id '
        'WHERE s.project_id=%s AND s.request_id=%s LIMIT 2')
READ_SOURCE = READ.replace('s.request_id=%s','s.source_id=%s')
LIST = ('SELECT '+SOURCE_COLUMNS.rsplit(',s.content',1)[0]+',i.candidate_json,i.created_at '
        'FROM cyomics_app.fasta_sources s LEFT JOIN cyomics_app.fasta_index_versions i '
        'ON i.source_id=s.source_id AND i.project_id=s.project_id '
        'WHERE s.project_id=%s ORDER BY s.created_at DESC,s.source_id DESC LIMIT 5')



def _uuid(value):
    if type(value) is not str:
        raise Fault(400, 'invalid_fasta_request')
    try:
        valid = str(UUID(value)) == value
    except ValueError:
        valid = False
    if not valid:
        raise Fault(400, 'invalid_fasta_request')
    return value


def _stamp(value):
    return value.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


@dataclass(frozen=True, slots=True, repr=False)
class SavedFasta:
    source: model.StoredSource
    candidate: model.IndexCandidate
    index_created_at: str

    def dto(self):
        source = {('createdAt' if k == 'created_at' else k):getattr(self.source,k)
                  for k in model.StoredSource.__dataclass_fields__ if k != 'content'}
        return {'source':model.validate_source_dto(source),
                'index':model.index_dto(self.candidate,self.index_created_at)}


class FastaPrivateRepository(UploadRepository):
    """Default inert with no pool. No role/password/verified-bool shortcuts.

    Trusted caller supplies a fresh upload-compatible Budget (including create
    outcome), verified actor and approved fixed configuration per operation.
    Parsing is synchronous and cooperatively bounded by the existing model;
    caller must account for that CPU work in its request execution policy.
    """
    def __init__(self, pool=None):
        super().__init__(pool)

    def _check(self, actor, project, request, budget, config):
        if self.pool is None:
            raise Fault(503, 'fasta_intake_unavailable')
        _uuid(actor); _uuid(project); _uuid(request)
        model.validate_config(config)
        budget.check()

    def _control(self, budget):
        return model.ParseControl(checkpoint=budget.check)

    async def _existing(self, q, project, request, budget, *, lookup='request_id', parsed=None):
        if lookup not in ('request_id','source_id'):raise Fault(400,'invalid_fasta_request')
        rows = await q(READ if lookup=='request_id' else READ_SOURCE, (UUID(project),UUID(request)), many=True)
        if not rows:
            return None
        if len(rows) != 1 or len(rows[0]) != 12:
            raise Fault(503, 'fasta_index_persisted_invalid')
        row = rows[0]
        source = _decode(row[:10], project, str(row[0]), budget.check)
        if (source.request_id if lookup=='request_id' else source.source_id) != request or type(row[10]) is not str or len(row[10].encode('utf8')) > 16384:
            raise Fault(503, 'fasta_index_persisted_invalid')
        candidate = model.candidate_from_json(row[10].encode('utf8'))
        if type(row[11]) is not datetime or row[11].tzinfo is None or row[11].utcoffset() is None:
            raise Fault(503, 'fasta_index_persisted_invalid')
        if parsed is None:
            try:
                model.rebuild_and_verify(source,candidate,expected_project_id=project,
                    expected_source_id=source.source_id,expected_index_version_id=candidate.index_version_id,
                    control=self._control(budget))
            except model.IntakeError as error:
                if error.code=='fasta_intake_timeout':
                    raise Fault(504,'fasta_intake_timeout') from None
                raise
        else:
            # Only save's same-request fully parsed source/candidate reaches here.
            # Full fingerprint is independent of source UUID; compare every field
            # after rebinding only IDs, plus exact source bytes, never a verified bool.
            fresh_source,fresh_candidate=parsed
            if (type(fresh_source) is not model.StoredSource or type(fresh_candidate) is not model.IndexCandidate
                    or source.project_id!=fresh_source.project_id or source.filename!=fresh_source.filename
                    or source.content!=fresh_source.content or source.bytes!=fresh_source.bytes or source.sha256!=fresh_source.sha256):
                raise Fault(409,'fasta_request_conflict')
            rebound=replace(fresh_candidate,source_id=source.source_id,
                index_version_id=model.version_key(project,source.source_id,source.sha256,fresh_candidate.config_json))
            model.decode_candidate(model.candidate_snapshot(rebound))
            if model.candidate_snapshot(rebound)!=model.candidate_snapshot(candidate):
                raise Fault(503,'fasta_index_persisted_invalid')
            budget.check()
        result = SavedFasta(source,candidate,_stamp(row[11]))
        result.dto()
        budget.check()
        return result

    async def read(self, actor, project_id, request_id, budget, *, config_json=None):
        self._check(actor,project_id,request_id,budget,config_json)
        async def operation(q):
            await self.project(q,actor,project_id)
            result = await self._existing(q,project_id,request_id,budget)
            if result is None:
                raise Fault(404, 'fasta_source_not_found')
            return result
        return await self.transaction(actor,budget,operation)

    async def save(self, actor, project_id, request_id, filename, raw, budget, *, config_json=None):
        self._check(actor,project_id,request_id,budget,config_json)
        # Owner check precedes byte processing/full parsing. No foreign data returned.
        async def preflight(q):
            await self.project(q,actor,project_id)
            return await self._existing(q,project_id,request_id,budget)
        existing = await self.transaction(actor,budget,preflight)
        if type(raw) is not bytes or not 1 <= len(raw) <= model.MAX_SOURCE:
            raise Fault(413 if type(raw) is bytes and len(raw)>model.MAX_SOURCE else 400,'invalid_fasta_request')
        if type(filename) is not str or not filename.lower().endswith(('.fa','.fasta','.faa')):
            raise Fault(400,'invalid_fasta_request')
        sha = hashlib.sha256(raw).hexdigest()
        now = datetime.now(timezone.utc)
        source_dto = dict(source_id=str(uuid4()),project_id=project_id,request_id=request_id,
            filename=filename,format='protein_fasta',bytes=len(raw),sha256=sha,status='stored',createdAt=_stamp(now))
        source = model.source_from_json(_json(source_dto).encode('ascii'),raw)

        def retry(value):
            if (value.source.filename != filename or value.source.sha256 != sha
                    or value.source.bytes != len(raw) or value.source.content != raw):
                raise Fault(409,'fasta_request_conflict')
            return 200,value

        if existing is not None:
            return retry(existing)
        candidate = model.build_candidate(source,expected_project_id=project_id,
            expected_source_id=source.source_id,control=self._control(budget),config_json=config_json)
        payload = _json(model.candidate_snapshot(candidate))
        async def operation(q):
            await self.project(q,actor,project_id)
            lock = int.from_bytes(hashlib.sha256(('cyomics:private-fasta:v1:'+actor).encode('ascii')).digest()[:8],'big',signed=True)
            if not (await q('SELECT pg_catalog.pg_try_advisory_xact_lock(%s)',(lock,)))[0]:
                raise Fault(429,'fasta_intake_busy')
            prior = await self._existing(q,project_id,request_id,budget,parsed=(source,candidate))
            if prior is not None:
                return retry(prior)
            counts=await q('SELECT count(*), COALESCE(sum(s.bytes),0), count(*) FILTER (WHERE s.project_id=%s), COALESCE(sum(s.bytes) FILTER (WHERE s.project_id=%s),0) FROM cyomics_app.fasta_sources s JOIN cyomics_app.projects p ON p.project_id=s.project_id WHERE p.owner_id=%s',
                (UUID(project_id),UUID(project_id),UUID(actor)))
            if type(counts) not in (tuple,list) or len(counts)!=4 or any(type(v) is not int or v<0 for v in counts):
                raise Fault(503,'fasta_index_persisted_invalid')
            if counts[0]>=8 or counts[1]+len(raw)>209715200 or counts[2]>=4 or counts[3]+len(raw)>104857600:
                raise Fault(413,'fasta_quota_exceeded')
            budget.check()
            await q('INSERT INTO cyomics_app.fasta_sources (source_id,project_id,request_id,filename,format,bytes,sha256,status,created_at,content) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING source_id',
                (UUID(source.source_id),UUID(project_id),UUID(request_id),filename,'protein_fasta',len(raw),sha,'stored',now,raw))
            await q('INSERT INTO cyomics_app.fasta_index_versions (index_version_id,project_id,source_id,source_bytes,source_sha256,config_sha256,index_manifest_sha256,candidate_json,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING index_version_id',
                (UUID(candidate.index_version_id),UUID(project_id),UUID(source.source_id),len(raw),sha,candidate.config_sha256,candidate.index_manifest_sha256,payload,now))
            budget.check()
            return 201,SavedFasta(source,candidate,_stamp(now))
        return await self.transaction(actor,budget,operation,writing=True)

    async def read_source(self, actor, project_id, source_id, budget, *, config_json=None):
        self._check(actor,project_id,source_id,budget,config_json)
        async def operation(q):
            await self.project(q,actor,project_id)
            result=await self._existing(q,project_id,source_id,budget,lookup='source_id')
            if result is None:raise Fault(404,'fasta_source_not_found')
            return result
        return await self.transaction(actor,budget,operation)

    async def list(self, actor, project_id, budget, *, config_json=None):
        if self.pool is None:raise Fault(503,'fasta_intake_unavailable')
        _uuid(actor);_uuid(project_id);model.validate_config(config_json);budget.check()
        async def operation(q):
            await self.project(q,actor,project_id)
            rows=await q(LIST,(UUID(project_id),),many=True)
            if len(rows)>4:raise Fault(503,'fasta_index_persisted_invalid')
            items=[]
            for row in rows:
                budget.check()
                if len(row)!=11 or any(type(v) is not UUID for v in row[:3]) or str(row[1])!=project_id:
                    raise Fault(503,'fasta_index_persisted_invalid')
                if any(type(row[i]) is not datetime or row[i].tzinfo is None or row[i].utcoffset() is None for i in (8,10)):
                    raise Fault(503,'fasta_index_persisted_invalid')
                source=model.validate_source_dto(dict(zip(('source_id','project_id','request_id','filename','format','bytes','sha256','status','createdAt'),
                    (str(row[0]),str(row[1]),str(row[2]),*row[3:8],_stamp(row[8])))))
                if type(row[9]) is not str or len(row[9].encode('utf8'))>16384:raise Fault(503,'fasta_index_persisted_invalid')
                candidate=model.candidate_from_json(row[9].encode('utf8'))
                if (candidate.project_id,candidate.source_id,candidate.source_bytes,candidate.source_sha256)!=(project_id,source['source_id'],source['bytes'],source['sha256']):
                    raise Fault(503,'fasta_index_persisted_invalid')
                # Metadata-only list, not a fake StoredSource or full-index verifier.
                items.append({'source':source,'index':model.index_dto(candidate,_stamp(row[10]))})
            budget.check();return {'items':items,'nextCursor':None}
        return await self.transaction(actor,budget,operation)
