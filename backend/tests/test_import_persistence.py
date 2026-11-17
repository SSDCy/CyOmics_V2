import asyncio
from concurrent.futures import Future
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import socket
import threading
import unittest
from unittest.mock import patch

from cyomics_projects import import_persistence as documents
from cyomics_projects import import_service as service_module
from cyomics_projects.import_persistence import ImportFault
from cyomics_projects.import_repository import ImportRepository
from cyomics_projects.import_service import ImportService, CooperativeImportRunner
from cyomics_projects.settings import Budget
from import_fakes import A,B,PROJECT,SECOND,FILE,OTHER_FILE,RAW,ImportPool,InlineTestRunner

MIGRATION=(Path(__file__).parents[1]/'migrations/004_raw_backed_dataset_imports.sql').read_text()
CORE={str(Path(documents.__file__).with_name(name)) for name in documents.CORE_PINS}
REAL_ACTIONS=[]

def forbidden(*args,**kwargs):
    REAL_ACTIONS.append('forbidden_real_action');raise AssertionError('Real runtime action prohibited')


class ImportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.guards=[]
        import psycopg
        from cyomics_projects import repository,settings
        original_open=os.open
        def public_open(path,*args,**kwargs):
            if str(path) not in CORE:return forbidden()
            return original_open(path,*args,**kwargs)
        for obj,name,value in [(socket,'socket',forbidden),(socket,'create_connection',forbidden),
                               (threading.Thread,'start',forbidden),(psycopg.AsyncConnection,'connect',forbidden),
                               (repository,'load_runtime_password',forbidden),(settings,'load_runtime_password',forbidden),
                               (settings,'load_approved_anon_key',forbidden),(os,'open',public_open)]:
            p=patch.object(obj,name,value);p.start();self.guards.append(p)
        self.pool=ImportPool();self.runner=InlineTestRunner();self.repo=ImportRepository(self.pool);self.service=ImportService(self.repo,self.runner)
    async def asyncTearDown(self):
        for p in reversed(self.guards):p.stop()
        self.assertEqual(REAL_ACTIONS,[])
    async def complete(self,**kwargs):
        return await self.service.complete(A,str(PROJECT),str(FILE),kwargs.pop('budget',Budget(30)),**kwargs)

    async def test_success_readback_replay_source_spans_zero_empty_and_members(self):
        parsed=[];original=documents.parse_protein_groups
        def capture(*args,**kwargs):
            result=original(*args,**kwargs);parsed.append(result.parsed);return result
        with patch.object(documents,'parse_protein_groups',capture):
            first=await self.complete();again=await self.complete();read=await self.service.read(A,str(PROJECT),str(FILE),Budget(30),first['datasetVersion']['dataset_version_id'])
        self.assertEqual(first,again);self.assertEqual(first,read);self.assertEqual(len(parsed),1)
        self.assertEqual(len(self.pool.versions),1)
        row=parsed[0].rows[0];self.assertEqual(row.cells,('P1; P2','0',''));self.assertEqual([m.token for m in row.members],['P1','P2'])
        self.assertEqual(RAW[row.source_span.start_byte:row.source_span.end_byte],b'P1; P2\t0\t')
        report=first['importReport'];self.assertEqual(report['report']['quant_exact_zero_string_cells'],2);self.assertEqual(report['report']['quant_empty_string_cells'],1)
        self.assertEqual([c['sample_label_raw'] for c in report['sample_columns']],['sample A','sample A'])
        self.assertEqual(len(report['row_source_spans']),2);self.assertFalse(report['parsed_table_materialized'])
        self.assertNotIn('"cells":',json.dumps(first));self.assertEqual(first['datasetVersion']['source']['expected_raw_sha256'],hashlib.sha256(RAW).hexdigest())

    async def test_concurrent_same_key_returns_one_atomic_winner(self):
        self.pool.pause_insert=asyncio.Event()
        first=asyncio.create_task(self.complete());await self.pool.insert_entered.wait()
        second=asyncio.create_task(self.complete());await self.pool.waiter_entered.wait()
        self.pool.pause_insert.set();a,b=await asyncio.gather(first,second)
        self.assertEqual(a,b);self.assertEqual(len(self.pool.versions),1);self.assertFalse(self.pool.locks)

    async def test_wrong_owner_and_file_wrong_project_are_404_before_parse(self):
        for subject,pid,fid,code in [(B,PROJECT,FILE,'project_not_found'),(B,SECOND,FILE,'file_not_found'),(A,PROJECT,OTHER_FILE,'file_not_found')]:
            with self.assertRaises(ImportFault) as caught:await self.service.complete(subject,str(pid),str(fid),Budget(30))
            self.assertEqual((caught.exception.status,caught.exception.code),(404,code))
        self.assertEqual(self.runner.calls,0);self.assertFalse(self.pool.versions)

    async def test_source_integrity_cannot_be_replaced_by_caller_claims(self):
        for index,value in [(3,len(RAW)+1),(4,'0'*64),(5,'not_stored')]:
            old=self.pool.files[FILE];row=list(old);row[index]=value;self.pool.files[FILE]=tuple(row)
            with self.assertRaises(ImportFault) as caught:await self.complete()
            self.assertEqual(caught.exception.code,'import_source_integrity_failed');self.pool.files[FILE]=old
        self.assertFalse(self.pool.versions)
        with self.assertRaises(TypeError):await self.service.complete(A,str(PROJECT),str(FILE),Budget(),source_ref='not accepted')

    async def test_parse_failure_and_cancel_before_during_parse_publish_nothing(self):
        self.pool.set_raw(FILE,PROJECT,b'Protein IDs\tIntensity x\na\t0\textra\n')
        with self.assertRaises(ImportFault) as caught:await self.complete()
        self.assertEqual(caught.exception.code,'txt_import_failed');self.assertEqual(caught.exception.issues[0]['code'],'TXT_ROW_WIDTH')
        self.pool.set_raw(FILE,PROJECT,RAW)
        with self.assertRaises(asyncio.CancelledError):await self.complete(cancelled=lambda:True)
        n=0
        def cancel_later():
            nonlocal n;n+=1;return n>=8
        with self.assertRaises(asyncio.CancelledError):await self.complete(cancelled=cancel_later)
        self.assertFalse(self.pool.versions)

    async def test_cancel_or_timeout_immediately_before_commit_rolls_back(self):
        flag=False
        def set_flag():
            nonlocal flag;flag=True
        self.pool.on_insert=set_flag
        with self.assertRaises(asyncio.CancelledError):await self.complete(cancelled=lambda:flag)
        self.assertFalse(self.pool.versions)
        self.pool.on_insert=None;budget=Budget(30)
        self.pool.on_insert=lambda:setattr(budget,'deadline',0)
        with self.assertRaises(ImportFault) as caught:await self.complete(budget=budget)
        self.assertEqual(caught.exception.code,'import_timeout');self.assertFalse(self.pool.versions)

    async def test_storage_rejection_invalid_return_and_commit_rejection_leave_no_version(self):
        for field,value in [('fail_insert',True),('corrupt_return',True),('commit_mode','rejected')]:
            setattr(self.pool,field,value)
            with self.assertRaises(ImportFault) as caught:await self.complete()
            self.assertIn(caught.exception.code,['import_unavailable','import_persisted_report_invalid'])
            self.assertFalse(self.pool.versions);setattr(self.pool,field,False if field!='commit_mode' else None)

    async def test_commit_unknown_is_not_retried_and_stable_key_can_read_back(self):
        for mode in ['lost','cancelled']:
            self.pool=ImportPool();self.repo=ImportRepository(self.pool);self.service=ImportService(self.repo,self.runner);self.pool.commit_mode=mode
            with self.assertRaises(ImportFault) as caught:await self.complete()
            self.assertEqual(caught.exception.code,'import_result_unknown');self.assertEqual(caught.exception.status,504)
            key=caught.exception.version_key;self.assertEqual(len(self.pool.versions),1)
            self.assertEqual(sum(sql.startswith('INSERT INTO') for sql,_ in self.pool.calls),1)
            value=await self.service.read(A,str(PROJECT),str(FILE),Budget(30),key)
            self.assertEqual(value['datasetVersion']['dataset_version_id'],key)

    async def test_post_commit_deadline_is_unknown_not_false_rollback(self):
        budget=Budget(30);self.pool.on_write_commit=lambda:setattr(budget,'deadline',0)
        with self.assertRaises(ImportFault) as caught:await self.complete(budget=budget)
        self.assertEqual(caught.exception.code,'import_result_unknown');self.assertEqual(len(self.pool.versions),1)

    async def test_report_bound_release_mismatch_and_dynamic_exception_fail_closed(self):
        with patch.object(documents,'REPORT_MAX_BYTES',100):
            with self.assertRaises(ImportFault) as caught:await self.complete()
            self.assertEqual(caught.exception.code,'import_report_too_large')
        pins={k:dict(v) for k,v in documents.CORE_PINS.items()};pins['protein_groups_parser.py']['sha256']='0'*64
        with patch.object(documents,'CORE_PINS',pins):
            with self.assertRaises(ImportFault) as caught:await self.complete()
            self.assertEqual(caught.exception.code,'parser_release_mismatch')
        with patch.object(documents,'parse_protein_groups',side_effect=RuntimeError('DO_NOT_RETURN_PRIVATE_TEXT')):
            with self.assertRaises(ImportFault) as caught:await self.complete()
            self.assertEqual(caught.exception.code,'import_unavailable');self.assertNotIn('DO_NOT_RETURN_PRIVATE_TEXT',str(caught.exception))
        self.assertFalse(self.pool.versions)

    async def test_configuration_release_and_schema_are_versioned(self):
        config=documents.configuration();encoded=documents.canonical_json(config,4096);key=documents.version_key(str(FILE),encoded)
        self.assertEqual(config['parser_release']['source_commit'],documents.CORE_SOURCE_COMMIT)
        for field,value in [('publisher_contract','raw_backed_dataset_import_v2'),('report_schema_version',2)]:
            changed=dict(config);changed[field]=value;self.assertNotEqual(documents.version_key(str(FILE),documents.canonical_json(changed,4096)),key)
        self.assertIn('FORCE ROW LEVEL SECURITY',MIGRATION);self.assertEqual(MIGRATION.count('CREATE POLICY '),2)
        self.assertIn('UNIQUE (file_id,parser_contract,config_sha256)',MIGRATION)
        self.assertNotIn('CREATE ROLE',MIGRATION);self.assertNotIn('GRANT UPDATE',MIGRATION)

    async def test_runner_one_admission_retains_slot_after_cancel_until_worker_done(self):
        class FakeExecutor:
            def __init__(self):self.future=Future();self.submitted=[]
            def submit(self,*args):self.submitted.append(args);return self.future
            def shutdown(self,**kwargs):pass
        executor=FakeExecutor();runner=CooperativeImportRunner();runner._executor=executor
        task=asyncio.create_task(runner.run(lambda stopped:None,Budget(30)));await asyncio.sleep(0)
        with self.assertRaises(ImportFault) as caught:await runner.run(lambda stopped:None,Budget())
        self.assertEqual(caught.exception.code,'import_busy');task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertTrue(executor.submitted[0][1]());self.assertTrue(runner._busy)
        with self.assertRaises(ImportFault):await runner.run(lambda stopped:None,Budget())
        executor.future.set_result(None)
        for _ in range(3):await asyncio.sleep(0)
        self.assertFalse(runner._busy);self.assertEqual(len(executor.submitted),1);runner.close()

    async def test_runner_timeout_sets_cooperative_stop_without_freeing_live_worker(self):
        class TimedBudget:
            def check(self):pass
            async def run(self,*args,**kwargs):raise TimeoutError()
        class FakeExecutor:
            def __init__(self):self.future=Future();self.stop=None
            def submit(self,operation,stopped):self.stop=stopped;return self.future
            def shutdown(self,**kwargs):pass
        executor=FakeExecutor();runner=CooperativeImportRunner();runner._executor=executor
        with self.assertRaises(ImportFault) as caught:await runner.run(lambda stopped:None,TimedBudget())
        self.assertEqual(caught.exception.code,'import_timeout');self.assertTrue(executor.stop());self.assertTrue(runner._busy)
        executor.future.set_result(None)
        for _ in range(3):await asyncio.sleep(0)
        self.assertFalse(runner._busy);runner.close()

    async def test_runner_same_key_two_waiters_share_one_worker_and_third_is_bounded(self):
        class FakeExecutor:
            def __init__(self):self.future=Future();self.calls=0
            def submit(self,*args):self.calls+=1;return self.future
            def shutdown(self,**kwargs):pass
        executor=FakeExecutor();runner=CooperativeImportRunner();runner._executor=executor
        first=asyncio.create_task(runner.run(lambda stopped:'first',Budget(30),key=('owner','file','config')))
        await asyncio.sleep(0)
        second=asyncio.create_task(runner.run(lambda stopped:'must not run',Budget(30),key=('owner','file','config')))
        await asyncio.sleep(0)
        for key in [('owner','file','config'),('other-owner','file','config')]:
            with self.assertRaises(ImportFault) as caught:await runner.run(lambda stopped:None,Budget(30),key=key)
            self.assertEqual(caught.exception.code,'import_busy')
        executor.future.set_result('shared-result')
        self.assertEqual(await first,await second);self.assertEqual(executor.calls,1);runner.close()
