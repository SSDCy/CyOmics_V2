"""Synthetic artifact bytes only. POSIX tests must run unprivileged; no R/DB."""
import copy
from datetime import timedelta
import hashlib
import os
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

BASE = Path(os.environ.get('CYOMICS_ARTIFACT_BASELINE', Path(__file__).resolve().parents[1]))
os.environ['CYOMICS_RAW_QC_BASELINE'] = str(BASE)
f = runpy.run_path(str(BASE/'tests/test_raw_qc_contract.py'))
sys.modules['cyomics_projects'].__path__.insert(0,str(Path(__file__).resolve().parents[1]/'cyomics_projects'))
from cyomics_projects import raw_qc_artifacts as a
from cyomics_projects import raw_qc_contract as c


def fixture():
    row=f['record']();row[12]='running';row[13]=2
    row[15]=f['STAMP']+timedelta(seconds=1);row[17]=uuid4()
    job=c.decode_record(row)
    contents={name:('SYNTHETIC ONLY '+name+'\n').encode() for name in c.OUTPUTS}
    receipt={'job_id':job['job_id'],'dataset_version_id':job['source']['dataset_version_id'],
        'file_id':job['source']['file_id'],'input_bytes':job['source']['source_bytes'],
        'input_sha256':job['source']['source_sha256'],'quant_family':job['quant_family'],
        'script_sha256':job['execution']['script']['sha256'],
        'executable_sha256':job['execution']['executable']['sha256'],
        'entry_kind':'native_R','limits_profile':job['execution']['limits_profile'],
        'limits_sha256':job['execution']['limits_sha256'],
        'outputs':{name:{'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()} for name,data in contents.items()}}
    return job,receipt,contents


class PureTests(unittest.TestCase):
    def test_default_unconfigured_no_filesystem(self):
        with patch.object(a.os,'open',side_effect=AssertionError('no IO')) as blocked:
            with self.assertRaises(a.ArtifactFailure) as cm:a.RawQCArtifacts().build_result({}, {})
        blocked.assert_not_called()
        self.assertEqual(cm.exception.code,'artifacts_unconfigured')

    def test_wrong_job_and_unregistered_execution_before_filesystem(self):
        job,receipt,_=fixture();worker=a.RawQCArtifacts(Path('/unused'),f['REGISTRATION'])
        with patch.object(a.os,'open',side_effect=AssertionError('no IO')) as blocked:
            for key,value in [('job_id','../outside'),('status','queued'),('binding_sha256','a'*64)]:
                bad=copy.deepcopy(job);bad[key]=value
                with self.assertRaises(a.ArtifactFailure):worker.build_result(bad,receipt)
            with self.assertRaises(a.ArtifactFailure):a.RawQCArtifacts(Path('/unused'),object()).build_result(job,receipt)

        blocked.assert_not_called()

    def test_receipt_pin_types_unknown_names_and_size_caps_before_io(self):
        job,receipt,_=fixture();worker=a.RawQCArtifacts(Path('/unused'),f['REGISTRATION'])
        with patch.object(a.os,'open',side_effect=AssertionError('no IO')) as blocked:
            variants=[]
            bad=copy.deepcopy(receipt);bad['job_id']=str(uuid4());variants.append(bad)
            bad=copy.deepcopy(receipt);bad['outputs']['extra']={'bytes':1,'sha256':'a'*64};variants.append(bad)
            for value in (True,'3',0,1.5,a.PER_FILE+1):
                bad=copy.deepcopy(receipt);bad['outputs']['summary.tsv']['bytes']=value;variants.append(bad)
            bad=copy.deepcopy(receipt)
            for pin in bad['outputs'].values():pin['bytes']=40000000
            variants.append(bad)
            for bad in variants:
                with self.assertRaises(a.ArtifactFailure):worker.build_result(job,bad)

        blocked.assert_not_called()


@unittest.skipUnless(os.name=='posix' and hasattr(os,'geteuid') and os.geteuid()>0,'Requires unprivileged POSIX descriptor filesystem; Windows is not a POSIX test')
class PosixTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'publish';self.root.mkdir(mode=0o700)
        self.job,self.receipt,self.contents=fixture()
        self.folder=self.root/self.job['job_id'];self.folder.mkdir(mode=0o700)
        for name,data in self.contents.items():
            p=self.folder/name;p.write_bytes(data);p.chmod(0o600)
        self.worker=a.RawQCArtifacts(self.root,f['REGISTRATION'],clock=lambda:f['STAMP']+timedelta(seconds=2))

    def build(self,**kw):return self.worker.build_result(self.job,self.receipt,**kw)
    def denied(self):
        with self.assertRaises(a.ArtifactFailure):self.build()

    def test_exact_seven_manifest_and_nine_fsyncs_no_byte_or_permission_changes(self):
        before={p.name:(p.read_bytes(),p.stat().st_mode) for p in self.folder.iterdir()}
        original=a.os.fsync;calls=[]
        with patch.object(a.os,'fsync',side_effect=lambda fd:(calls.append(fd),original(fd))[1]):result=self.build()
        self.assertEqual(len(calls),9)
        self.assertEqual([x['name'] for x in result['artifacts']],sorted(c.OUTPUTS))
        c.result_value(result,self.job)
        self.assertEqual(result['manifest_sha256'],c.digest(c.canonical(result['artifacts'])))
        self.assertEqual(before,{p.name:(p.read_bytes(),p.stat().st_mode) for p in self.folder.iterdir()})
        self.assertEqual(set(self.root.iterdir()),{self.folder})

    def test_missing_and_extra_files(self):
        p=self.folder/'summary.tsv';data=p.read_bytes();p.unlink();self.denied()
        p.write_bytes(data);p.chmod(0o600)
        (self.folder/'extra').write_bytes(b'poison');self.denied()

    def test_wrong_job_cannot_read_sibling_even_same_receipt_names(self):
        bad=copy.deepcopy(self.job);bad['job_id']=str(uuid4())
        with self.assertRaises(a.ArtifactFailure):self.worker.build_result(bad,self.receipt)
        self.assertEqual(len(list(self.root.iterdir())),1)

    def test_root_and_job_symlink_rejected(self):
        alias=Path(self.tmp.name)/'alias';alias.symlink_to(self.root,target_is_directory=True)
        with self.assertRaises(a.ArtifactFailure):a.RawQCArtifacts(alias,f['REGISTRATION']).build_result(self.job,self.receipt)
        moved=self.root/'moved';self.folder.rename(moved);self.folder.symlink_to(moved,target_is_directory=True);self.denied()

    def test_symlink_and_hardlink_artifact_rejected(self):
        p=self.folder/'summary.tsv';p.unlink();outside=Path(self.tmp.name)/'outside';outside.write_bytes(self.contents['summary.tsv']);outside.chmod(0o600)
        p.symlink_to(outside);self.denied();p.unlink();os.link(outside,p);self.denied()

    def test_mode_rejected_and_fifo_is_nonblocking_rejected(self):
        p=self.folder/'summary.tsv';p.chmod(0o644);self.denied();p.unlink();os.mkfifo(p,0o600);self.denied()

    def test_hash_and_size_mismatch_no_fsync(self):
        for data in (b'changed',b'X'*len(self.contents['summary.tsv'])):
            (self.folder/'summary.tsv').write_bytes(data)
            with patch.object(a.os,'fsync') as sync:self.denied()
            sync.assert_not_called()

    def test_same_size_mutation_during_hash_rejected(self):
        original=a.os.pread;changed=False
        def read(fd,size,offset):
            nonlocal changed
            chunk=original(fd,size,offset)
            if not changed and chunk:
                changed=True;p=self.folder/'summary.tsv';p.write_bytes(b'X'*len(self.contents['summary.tsv']))
            return chunk
        with patch.object(a.os,'pread',side_effect=read):self.denied()

    def test_directory_extra_added_during_fsync_rejected(self):
        original=a.os.fsync;count=0
        def sync(fd):
            nonlocal count
            count+=1
            if count==9:(self.folder/'late').write_bytes(b'late')
            original(fd)
        with patch.object(a.os,'fsync',side_effect=sync):self.denied()

    def test_every_file_jobdir_and_parent_fsync_failure_no_result(self):
        original=a.os.fsync
        for failure_at in range(1,10):
            count=0
            def sync(fd):
                nonlocal count
                count+=1
                if count==failure_at:raise OSError('SYNTHETIC fsync failure')
                original(fd)
            with patch.object(a.os,'fsync',side_effect=sync):self.denied()

    def test_external_budget_expiry_and_internal_deadline(self):
        count=0
        def check():
            nonlocal count
            count+=1
            if count==5:raise TimeoutError()
        with self.assertRaises(a.ArtifactFailure):self.build(check=check)
        with patch.object(a.time,'monotonic',side_effect=[0,31]):self.denied()

    def test_time_before_started_and_naive_clock_rejected(self):
        self.worker.clock=lambda:f['STAMP'];self.denied()
        self.worker.clock=lambda:f['STAMP'].replace(tzinfo=None);self.denied()


if __name__=='__main__':unittest.main(verbosity=2)
