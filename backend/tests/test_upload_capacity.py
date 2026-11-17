"""Synthetic capacity/profile regression; never real DB, data or browser proof."""
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch
from uuid import UUID,uuid4

from cyomics_projects import import_persistence as d
from cyomics_projects import import_http
from cyomics_projects.import_report import SourceRef
from cyomics_projects.protein_groups_parser import ParserLimits,parse_protein_groups
from cyomics_projects.import_repository import ImportRepository
from cyomics_projects.import_service import ImportService
from cyomics_projects.upload_repository import UploadRepository
from cyomics_projects.upload_validation import MAX_BYTES
from cyomics_projects.settings import Budget,Fault
import test_import_persistence as persistence_tests
import test_import_http as http_tests
import test_raw_uploads as upload_tests
from import_fakes import A,B,PROJECT,SECOND,FILE,OTHER_FILE,RAW,ImportPool,InlineTestRunner
from upload_fakes import FilePool
from fakes import NOW

FIXTURE=json.loads((Path(__file__).parent/'fixtures/legacy_import_v1.json').read_text())

def seed_legacy(pool):
    row=list(FIXTURE['row'])
    for i in (0,1,2):row[i]=UUID(row[i])
    row[12]=datetime.fromisoformat(row[12]);row=tuple(row)
    pool.versions[(row[2],row[3],row[4])]=row
    return row

def ref(raw,file_id=str(FILE),project_id=str(PROJECT)):
    sha=hashlib.sha256(raw).hexdigest()
    return SourceRef(project_id,file_id,'raw_bytea_v1:'+file_id+':'+sha,len(raw),sha)

def stored_candidate(candidate):
    s=candidate.source
    return (UUID(candidate.version_id),UUID(s.project_id),UUID(s.raw_file_id),'protein_groups_txt_v1',candidate.config_sha256,candidate.config_json,s.expected_bytes,s.expected_raw_sha256,'raw_backed',False,candidate.report_json,candidate.report_sha256,NOW)

class ParserCapacityTests(unittest.TestCase):
    def test_exact_25mib_parses_and_plus_one_rejects_before_producer(self):
        self.assertEqual(MAX_BYTES,26214400);self.assertEqual(ParserLimits().max_bytes,MAX_BYTES)
        chunks=[b'Protein IDs\tmeta\n'];remaining=MAX_BYTES-len(chunks[0])
        while remaining:
            size=min(1000003,remaining)
            if 0<remaining-size<3:size-=3
            self.assertGreaterEqual(size,3)
            chunks.append(b'P\t'+b'a'*(size-3)+b'\n');remaining-=size
        raw=b''.join(chunks);self.assertEqual(len(raw),MAX_BYTES)
        started=time.monotonic();out=parse_protein_groups(ref(raw),(raw[i:i+65536] for i in range(0,len(raw),65536)))
        self.assertTrue(out.ok,out.report.issues);self.assertEqual(out.report.observed_bytes,MAX_BYTES)
        print(json.dumps({'synthetic_parser_bytes':MAX_BYTES,'seconds':round(time.monotonic()-started,4),'real_input':False}))
        calls=[]
        def producer():calls.append(True);yield b'not read'
        too_large=SourceRef(str(PROJECT),str(FILE),'synthetic',MAX_BYTES+1,'0'*64)
        out=parse_protein_groups(too_large,producer());self.assertFalse(out.ok);self.assertEqual(calls,[])
        self.assertEqual((out.report.issues[0].code,out.report.issues[0].limit_key),('RESOURCE_LIMIT','max_bytes'))

    def test_two_million_cells_validated_by_new_profile_old_profile_stays_strict(self):
        limits=ParserLimits();self.assertEqual(limits.max_cells,2000000)
        self.assertEqual((limits.max_records,limits.max_columns,limits.max_field_units,limits.max_member_occurrences,limits.max_id_units),(10000,512,1000000,100000,2000000))
        names=['Protein IDs']+['Intensity '+str(i) for i in range(45)]+['LFQ intensity '+str(i) for i in range(45)]+['meta '+str(i) for i in range(409)]
        row=['P']+['0']*90+['']*409
        raw=('\t'.join(names)+'\n'+('\t'.join(row)+'\n')*4000).encode();source=ref(raw)
        cfg=d.canonical_json(d.configuration(),4096);key=d.version_key(str(FILE),cfg)
        candidate=d.build_candidate(d.StoredRaw(str(FILE),str(PROJECT),'proteinGroups_txt',len(raw),source.expected_raw_sha256,'stored',raw),source,cfg,key,lambda:None,lambda:False)
        value=d.decode_version(stored_candidate(candidate),source,cfg,key)
        report=value['importReport']['report'];self.assertEqual(report['row_count']*report['column_count'],2000000)
        self.assertEqual((report['intensity_sample_count'],report['lfq_sample_count']),(45,45))
        self.assertLessEqual(len(candidate.report_json.encode()),d.REPORT_MAX_BYTES)
        oldcfg=d.canonical_json(d.configuration(d.LEGACY_PROFILE),4096);oldkey=d.version_key(str(FILE),oldcfg)
        olddoc=json.loads(candidate.report_json);olddoc['report']['dataset_version_id']=oldkey;text=d.canonical_json(olddoc,d.REPORT_MAX_BYTES)
        oldrow=list(stored_candidate(candidate));oldrow[0]=UUID(oldkey);oldrow[4]=d.digest(oldcfg);oldrow[5]=oldcfg;oldrow[10]=text;oldrow[11]=d.digest(text)
        with self.assertRaises(d.ImportFault):d.decode_version(oldrow,source,oldcfg,oldkey)
        plus=raw+b'P\n';out=parse_protein_groups(ref(plus),(plus[i:i+65536] for i in range(0,len(plus),65536)))
        self.assertFalse(out.ok);issue=out.report.issues[0];self.assertEqual((issue.code,issue.limit_key,issue.record_number,issue.column_ordinal),('RESOURCE_LIMIT','max_cells',4001,0))

    def test_legacy_configuration_and_known_saved_version_id_exactly_preserved(self):
        legacy=d.configuration(d.LEGACY_PROFILE);self.assertEqual(legacy,FIXTURE['configuration'])
        oldcfg=d.canonical_json(legacy,4096);self.assertEqual(d.digest(oldcfg),'8f2d0412a6784c17ebcb0673b2c5c70dd76178bb361b7dc700ebaf110f733ad0')
        self.assertEqual(d.version_key('61be4269-067e-4fc0-be98-c53d3fb76a21',oldcfg),'28bc9473-173f-5c48-bc61-7edc7c40f584')
        for name,pin in d.CORE_PINS.items():
            raw=Path(d.__file__).with_name(name).read_bytes();self.assertEqual((len(raw),hashlib.sha256(raw).hexdigest()),(pin['bytes'],pin['sha256']))
        self.assertEqual(d.configuration()['parser_release']['source_commit'],'7834bef44bc05f1634cdd351283dc8e851107df6')

class ProfileReadTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=persistence_tests.ImportTests.asyncSetUp
    asyncTearDown=persistence_tests.ImportTests.asyncTearDown
    complete=persistence_tests.ImportTests.complete

    async def test_default_and_explicit_old_read_fresh_service_no_reparse_or_insert(self):
        oldrow=seed_legacy(self.pool);oldkey=str(oldrow[0]);before=list(self.pool.versions.items())
        with patch.object(d,'parse_protein_groups',side_effect=AssertionError('GET must not parse')):
            for service in (self.service,ImportService(self.repo,InlineTestRunner())):
                for version in (None,oldkey):
                    got=await service.read(A,str(PROJECT),str(FILE),Budget(30),version)
                    self.assertEqual(got,FIXTURE['response'])
        self.assertEqual(list(self.pool.versions.items()),before)
        self.assertFalse(any(sql.startswith('INSERT') for sql,_ in self.pool.calls))

    async def test_new_publication_coexists_and_default_prefers_current_explicit_old_stays(self):
        oldrow=seed_legacy(self.pool);new=await self.complete();newkey=new['datasetVersion']['dataset_version_id'];oldkey=str(oldrow[0])
        self.assertNotEqual(newkey,oldkey);self.assertEqual(len(self.pool.versions),2)
        self.assertEqual(await self.service.read(A,str(PROJECT),str(FILE),Budget(30)),new)
        self.assertEqual(await self.service.read(A,str(PROJECT),str(FILE),Budget(30),oldkey),FIXTURE['response'])
        self.assertEqual(await self.service.read(A,str(PROJECT),str(FILE),Budget(30),newkey),new)
        self.assertIn(oldrow,self.pool.versions.values())

    async def test_current_corruption_does_not_fall_back_and_unknown_config_is_rejected(self):
        seed_legacy(self.pool);new=await self.complete();key=next(k for k in self.pool.versions if k[2]==new['datasetVersion']['config_sha256'])
        row=list(self.pool.versions[key]);row[10]='{}';row[11]=d.digest('{}');self.pool.versions[key]=tuple(row)
        with self.assertRaises(d.ImportFault) as caught:await self.service.read(A,str(PROJECT),str(FILE),Budget(30))
        self.assertEqual(caught.exception.code,'import_persisted_report_invalid')
        self.assertEqual(await self.service.read(A,str(PROJECT),str(FILE),Budget(30),FIXTURE['response']['datasetVersion']['dataset_version_id']),FIXTURE['response'])
        bad=d.configuration();bad['limits']['max_cells']+=1;cfg=d.canonical_json(bad,4096)
        with self.assertRaises(d.ImportFault):d.configuration_limits(cfg)
        with self.assertRaises(d.ImportFault) as caught:await self.service.read(A,str(PROJECT),str(FILE),Budget(30),d.version_key(str(FILE),cfg))
        self.assertEqual(caught.exception.code,'dataset_version_not_found')

    async def test_legacy_read_wrong_owner_wrong_project_and_unknown_id_authorize_first(self):
        seed_legacy(self.pool)
        for subject,pid,fid,key,code in [(B,PROJECT,FILE,str(FIXTURE['row'][0]),'project_not_found'),(A,PROJECT,OTHER_FILE,None,'file_not_found'),(B,PROJECT,FILE,str(uuid4()),'project_not_found')]:
            with self.assertRaises(d.ImportFault) as caught:await self.service.read(subject,str(pid),str(fid),Budget(30),key)
            self.assertEqual((caught.exception.status,caught.exception.code),(404,code))
        self.assertEqual(self.runner.calls,0)

    async def test_repository_read_cap_25mib_plus_one_and_owner_check_preserved(self):
        raw=b'x'*MAX_BYTES;pool=ImportPool(raw);repo=ImportRepository(pool)
        value=await repo.load_raw(A,str(PROJECT),str(FILE),Budget(30));self.assertEqual(len(value.content),MAX_BYTES)
        pool.set_raw(FILE,PROJECT,raw+b'x')
        with self.assertRaises(d.ImportFault) as caught:await repo.load_raw(A,str(PROJECT),str(FILE),Budget(30))
        self.assertEqual(caught.exception.code,'import_source_integrity_failed')
        with self.assertRaises(d.ImportFault) as caught:await repo.load_raw(B,str(PROJECT),str(FILE),Budget(30))
        self.assertEqual(caught.exception.code,'project_not_found')

class ProfileHttpTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=http_tests.ImportHttpTests.asyncSetUp
    asyncTearDown=http_tests.ImportHttpTests.asyncTearDown
    request=http_tests.ImportHttpTests.request
    async def test_default_explicit_old_http_and_unauthenticated_with_strict_DTO(self):
        seed_legacy(self.pool);path=http_tests.PATH+'?project_id='+str(PROJECT)
        for suffix in ('','&version_id='+FIXTURE['row'][0]):
            response=await self.request('GET',path+suffix);self.assertEqual(response.status_code,200,response.text);self.assertEqual(response.json(),FIXTURE['response'])
        response=await self.request('GET',path,subject=None);self.assertEqual(response.status_code,401)
        with self.assertRaises(d.ImportFault):import_http.success_response(FIXTURE['response'],{'project_id':str(PROJECT),'file_id':str(FILE),'version_id':None,'method':'POST'},Budget(30))

class QuotaTests(unittest.IsolatedAsyncioTestCase):
    def seed(self,pool,items):
        pool.files=[(uuid4(),pid,uuid4(),'synthetic.txt','proteinGroups_txt',size,'0'*64,'stored',NOW,b'') for pid,size in items]
    async def save(self,pool,pid,raw=b'x'):
        value={'request_id':str(uuid4()),'filename':'synthetic.txt','format':'proteinGroups_txt','bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
        return await UploadRepository(pool).save(A,str(pid),value,raw,upload_tests.upload_budget())
    async def test_project_32mib_and_owner_64mib_exact_plus_one_unchanged(self):
        pool=FilePool();self.seed(pool,[(PROJECT,16*1048576),(PROJECT,16*1048576-1)])
        self.assertEqual((await self.save(pool,PROJECT))[0],201)
        with self.assertRaises(Fault) as caught:await self.save(pool,PROJECT)
        self.assertEqual(caught.exception.code,'upload_quota_exceeded');pool.close()
        pool=FilePool();other=UUID('88888888-8888-4888-8888-888888888888');target=UUID('99999999-9999-4999-8999-999999999999');pool.projects[other]=pool.projects[target]=A
        self.seed(pool,[(PROJECT,16*1048576)]*2+[(other,16*1048576),(other,16*1048576-1)])
        self.assertEqual((await self.save(pool,target))[0],201)
        with self.assertRaises(Fault) as caught:await self.save(pool,target)
        self.assertEqual(caught.exception.code,'upload_quota_exceeded');pool.close()
    async def test_original_and_new_target_sizes_fit_same_project_without_global_quota_change(self):
        self.assertLess(6186652+18988424,33554432)
        pool=FilePool();self.seed(pool,[(PROJECT,6186652)])
        self.assertEqual((await self.save(pool,PROJECT,b'x'*18988424))[0],201);pool.close()

class MigrationSourceTests(unittest.TestCase):
    def test_005_only_two_named_check_changes_and_old_migrations_untouched(self):
        b=Path(__file__).parents[1];sql=(b/'migrations/005_upload_capacity_25mib.sql').read_text()
        statements='\n'.join(line for line in sql.splitlines() if not line.lstrip().startswith('--'))
        self.assertEqual(statements.count('ALTER TABLE '),2)
        self.assertNotRegex(statements,r'\b(?:INSERT\s+INTO|UPDATE\s+cyomics|DELETE\s+FROM|CREATE\s+(?:TABLE|ROLE)|GRANT\s|REVOKE\s|DISABLE\s+ROW|NO\s+FORCE)')
        for text in ('raw_uploads_bytes_check','dataset_versions_source_bytes_check','10485760','26214400','remaining_after IS DISTINCT FROM remaining_before','policies_after IS DISTINCT FROM policies_before','tables_after IS DISTINCT FROM tables_before'):
            self.assertIn(text,sql)
        baseline=json.loads((b.parents[1]/'baseline.json').read_text())['backend_files']
        for name in ('003_private_raw_uploads.sql','004_raw_backed_dataset_imports.sql'):
            raw=(b/'migrations'/name).read_bytes();self.assertEqual(hashlib.sha256(raw).hexdigest(),baseline['backend/migrations/'+name]['sha256'])
