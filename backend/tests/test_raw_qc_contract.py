"""SOURCE-ONLY synthetic tests. Baseline imports are read-only; psycopg is a no-network stub."""
from datetime import datetime, timezone
from pathlib import Path
import copy
import hashlib
import json
import os
import sys
import types
import unittest
from enum import IntEnum
from uuid import UUID, uuid4

BASELINE = Path(os.environ.get('CYOMICS_RAW_QC_BASELINE', Path(__file__).resolve().parents[1])).resolve()
CANDIDATE = Path(__file__).resolve().parents[1]
class TS(IntEnum):
    IDLE=0; ACTIVE=1; INTRANS=2; INERROR=3; UNKNOWN=4
pg=types.ModuleType('psycopg'); pq=types.ModuleType('psycopg.pq'); pq.TransactionStatus=TS
class NeverConnect:
    @staticmethod
    async def connect(*args,**kwargs):
        raise AssertionError('NO NETWORK: synthetic source tests')
pg.AsyncConnection=NeverConnect; pg.pq=pq
sys.modules.update({'psycopg':pg,'psycopg.pq':pq})
package=types.ModuleType('cyomics_projects')
package.__path__=[str(CANDIDATE/'cyomics_projects'),str(BASELINE/'cyomics_projects')]
sys.modules['cyomics_projects']=package
from cyomics_projects.raw_qc_contract import *
from cyomics_projects.settings import Budget
from cyomics_projects.import_persistence import configuration, canonical_json, CONFIG_MAX_BYTES, build_candidate, StoredRaw, trusted_source, version_key
OWNER='11111111-1111-4111-8111-111111111111'
OTHER='22222222-2222-4222-8222-222222222222'
PROJECT='33333333-3333-4333-8333-333333333333'
FILE='44444444-4444-4444-8444-444444444444'
RAW=b'Protein IDs\tIntensity s\tLFQ intensity s\nP001\t0\t1\nP002\t\t2\n'
SHA=hashlib.sha256(RAW).hexdigest()
CONFIG=canonical_json(configuration(),CONFIG_MAX_BYTES)
DATASET=version_key(FILE,CONFIG)
STORED=StoredRaw(FILE,PROJECT,'proteinGroups_txt',len(RAW),SHA,'stored',RAW)
SOURCE=trusted_source(STORED,PROJECT,FILE,lambda:None)
C=build_candidate(STORED,SOURCE,CONFIG,DATASET,lambda:None,lambda:False)
STAMP=datetime(2026,11,18,tzinfo=timezone.utc)
DATASET_ROW=(UUID(DATASET),UUID(PROJECT),UUID(FILE),'protein_groups_txt_v1',
             digest(CONFIG),CONFIG,len(RAW),SHA,'raw_backed',False,C.report_json,C.report_sha256,STAMP)
RAW_META=(UUID(FILE),UUID(PROJECT),'proteinGroups_txt',len(RAW),SHA,'stored')
SYNTHETIC_EXECUTION={'script':{'bytes':4949,'sha256':SCRIPT_SHA},
 'runner':{'bytes':123,'sha256':'a'*64},'output_validator':{'bytes':456,'sha256':'b'*64},
 'executable':{'bytes':789,'sha256':'c'*64},'r_version':'R version 4.6.1','entry_kind':'native_R',
 'limits_profile':'raw_qc_limits_v3','limits_sha256':limits_sha256()}
REGISTRATION=ExecutionRegistration(canonical(SYNTHETIC_EXECUTION))  # deliberately synthetic, NOT an approved Native pin

def body(request=None,family='intensity'):
    return canonical({'schemaVersion':1,'request_id':request or str(uuid4()),'quant_family':family,'params':{}}).encode()

def record(request=None,family='Intensity'):
    source=bound_source(DATASET_ROW,RAW_META,PROJECT,DATASET,family)
    request_hash=request_sha(PROJECT,DATASET,family)
    return [UUID(str(uuid4())),UUID(PROJECT),UUID(DATASET),UUID(FILE),UUID(OWNER),
            UUID(request or str(uuid4())),request_hash,binding_sha(request_hash,source,SYNTHETIC_EXECUTION,family),
            canonical(source),family,'{}',canonical(SYNTHETIC_EXECUTION),'queued',1,STAMP,
            None,None,None,'confirmed',None,None,None]

def result_for(job):
    artifacts=[{'name':name,'bytes':10,'sha256':'d'*64,
       'media_type':'text/plain; charset=utf-8' if name=='R-session-info.txt' else 'text/tab-separated-values; charset=utf-8'}
       for name in sorted(OUTPUTS)]
    return {'schemaVersion':1,'job_id':job['job_id'],'binding_sha256':job['binding_sha256'],
            'execution':job['execution'],'artifacts':artifacts,'manifest_sha256':digest(canonical(artifacts)),
            'verified_at':stamp(STAMP)}

class ContractTests(unittest.TestCase):
    def fault(self,code,fn):
        with self.assertRaises(Fault) as cm: fn()
        self.assertEqual(cm.exception.code,code)

    def test_strict_request(self):
        self.assertEqual(request_input(body())['quant_family'],'Intensity')
        bad=[b'{}',b'{"schemaVersion":1,"schemaVersion":1}',b'\xef\xbb\xbf{}',b'{"x":NaN}',b'\xff']
        for key,value in [('owner_id',OWNER),('path','/tmp/x'),('matrix',[[1]]),('cmd','R')]:
            v=json.loads(body());v[key]=value;bad.append(json.dumps(v).encode())
        for field,value in [('schemaVersion',True),('schemaVersion',1.0),('quant_family','Intensity'),('params',{'log2':True}),('request_id',OWNER.upper())]:
            v=json.loads(body());v[field]=value;bad.append(json.dumps(v).encode())
        # OWNER contains no a-f uppercase letters, use a canonical UUID with letters instead.
        bad.pop();v=json.loads(body());v['request_id']='ABCDEFAB-1111-4111-8111-111111111111';bad.append(json.dumps(v).encode())
        bad += [b' '*1025,b'['*14+b'0'+b']'*14]
        for raw in bad:
            with self.subTest(raw=raw[:60]):
                self.fault('invalid_raw_qc_request',lambda:request_input(raw))

    def test_bound_source_not_owner_proof(self):
        source=bound_source(DATASET_ROW,RAW_META,PROJECT,DATASET,'Intensity')
        self.assertEqual(source['source_bytes'],len(RAW))
        for index,value in [(1,UUID(OTHER)),(3,len(RAW)+1),(4,'e'*64)]:
            raw=list(RAW_META);raw[index]=value
            self.fault('raw_qc_persisted_invalid',lambda:bound_source(DATASET_ROW,raw,PROJECT,DATASET,'Intensity'))

    def test_digest_and_binding(self):
        a=record();v=decode_record(a)
        self.assertEqual(v['params'],{})
        a[7]='0'*64
        self.fault('raw_qc_persisted_invalid',lambda:decode_record(a))

    def test_no_default_runtime_or_historical_runner_pin(self):
        self.assertFalse(ENABLED)
        e=copy.deepcopy(SYNTHETIC_EXECUTION);e['entry_kind']='Rscript'
        with self.assertRaises(ValueError):execution_value(e)
        for key,value in [('rscript',{'bytes':789,'sha256':'c'*64}),('extra_env',{}),('path','/untrusted/exe')]:
            e=copy.deepcopy(SYNTHETIC_EXECUTION);e[key]=value
            with self.assertRaises(ValueError):execution_value(e)
        e=copy.deepcopy(SYNTHETIC_EXECUTION);e['rscript']=e.pop('executable')
        with self.assertRaises(ValueError):execution_value(e)
        e=copy.deepcopy(SYNTHETIC_EXECUTION);e['script']['sha256']='0'*64
        with self.assertRaises(ValueError):execution_value(e)
        self.assertNotIn('5401737275ad52736f2bceae7f15ffada4e8348757f6d353f967787c304d36fc',canonical(SYNTHETIC_EXECUTION))

    def test_four_state_invariants_and_attempt(self):
        a=record()
        for index,value in [(12,'cancelled'),(13,True),(15,STAMP),(18,'reconciliation_pending'),(19,'runner_failed'),(21,'f'*64)]:
            b=copy.deepcopy(a);b[index]=value
            self.fault('raw_qc_persisted_invalid',lambda:decode_record(b))
        a[12]='running';a[13]=2;a[15]=STAMP;a[17]=uuid4()
        self.assertEqual(decode_record(a)['status'],'running')
        a[18]='reconciliation_pending'
        self.assertEqual(decode_record(a)['observation'],'reconciliation_pending')
        a[12]='failed';a[16]=STAMP;a[18]='confirmed';a[19]='worker_interrupted'
        self.assertEqual(decode_record(a)['error']['code'],'worker_interrupted')
        a[19]=None
        self.fault('raw_qc_persisted_invalid',lambda:decode_record(a))

    def test_seven_result_manifest_and_hashes(self):
        a=record();a[12]='running';a[13]=2;a[15]=STAMP;a[17]=uuid4()
        job=decode_record(a);result=result_for(job)
        result_value(result,job)
        a[12]='succeeded';a[13]=3;a[16]=STAMP;a[20]=canonical(result);a[21]=digest(a[20])
        self.assertEqual(len(decode_record(a)['result']['artifacts']),7)
        for mutate in [lambda x:x['artifacts'].pop(),
                       lambda x:x['artifacts'][0].update(name='../secret'),
                       lambda x:x['artifacts'][0].update(bytes=True),
                       lambda x:x.update(binding_sha256='0'*64),
                       lambda x:x.update(manifest_sha256='0'*64)]:
            b=copy.deepcopy(result);mutate(b)
            with self.assertRaises(ValueError):result_value(b,job)

    def test_status_bounds_and_corrupt_execution(self):
        a=record();a[11]=a[11]+' '
        self.fault('raw_qc_persisted_invalid',lambda:decode_record(a))
        a=record();s=json.loads(a[8]);s['row_count']=True;a[8]=canonical(s)
        self.fault('raw_qc_persisted_invalid',lambda:decode_record(a))

    def test_sql_proposal_static_privilege_boundaries_only(self):
        sql=(CANDIDATE/'migrations'/'007_private_raw_qc_jobs.sql').read_text(encoding='utf-8')
        self.assertIn('PROPOSAL ONLY',sql)
        self.assertIn('FORCE ROW LEVEL SECURITY',sql)
        self.assertIn('SECURITY DEFINER SET search_path=pg_catalog',sql)
        self.assertIn('GRANT EXECUTE ON FUNCTION',sql)
        self.assertIn('CREATE ROLE cyomicsv2_raw_qc_worker NOLOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS',sql)
        self.assertNotIn('GRANT UPDATE ON',sql)
        self.assertNotIn('GRANT SELECT ON cyomics_app.raw_qc_jobs TO cyomicsv2_raw_qc_worker',sql)
        self.assertNotIn('ALTER TABLE cyomics_app.dataset_versions ADD',sql)
        self.assertNotIn('ALTER TABLE cyomics_app.raw_uploads ADD',sql)
        self.assertNotIn('CREATE OR REPLACE',sql)
        self.assertNotIn('SET status=\'queued\'',sql)
        self.assertNotIn('EXECUTE format',sql)

if __name__=='__main__':
    unittest.main(verbosity=2)

