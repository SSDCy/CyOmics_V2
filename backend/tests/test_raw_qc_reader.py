"""Portable Linux reader tests: synthetic DTO/files only; no R/DB/Auth.

The reader is the public source, with no managed ancestor UID adapter.
"""
import os,sys,unittest
if os.name!='posix' or not sys.platform.startswith('linux'):
    raise unittest.SkipTest('Reader tests require unprivileged Linux; no POSIX stub fallback')
from collections import Counter
from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import datetime,timedelta,timezone
import errno
import hashlib
import itertools
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

BACKEND=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BACKEND))
from cyomics_projects import raw_qc_reader as r,raw_qc_contract as c

def digest(data):return hashlib.sha256(data).hexdigest()

def make_job(contents):
    # Public DTO only: no fabricated Owner, attempt, database row or registration.
    source={'project_id':'11111111-1111-4111-8111-111111111111',
        'dataset_version_id':'22222222-2222-4222-8222-222222222222',
        'file_id':'33333333-3333-4333-8333-333333333333','source_bytes':123,
        'source_sha256':'1'*64,'parser_contract':'protein_groups_txt_v1',
        'config_sha256':'2'*64,'report_sha256':'3'*64,'row_count':2,'column_count':3,
        'selected_columns_sha256':'4'*64}
    execution={'script':{'bytes':4949,'sha256':c.SCRIPT_SHA},'runner':{'bytes':477,'sha256':'a'*64},
        'output_validator':{'bytes':8474,'sha256':'b'*64},'executable':{'bytes':14488,'sha256':'c'*64},
        'r_version':'R version 4.6.1','entry_kind':'native_R','limits_profile':'raw_qc_limits_v3','limits_sha256':c.limits_sha256()}
    request=c.request_sha(source['project_id'],source['dataset_version_id'],'Intensity')
    start=datetime(2026,11,18,tzinfo=timezone.utc)
    job={'schemaVersion':1,'job_id':str(uuid4()),'request_id':str(uuid4()),'request_sha256':request,
        'binding_sha256':c.binding_sha(request,source,execution,'Intensity'),
        'source':source,'quant_family':'Intensity','params':{},'execution':execution,
        'status':'succeeded','revision':3,'created_at':c.stamp(start),
        'started_at':c.stamp(start+timedelta(seconds=1)),'finished_at':c.stamp(start+timedelta(seconds=3)),
        'observation':'confirmed','error':None,'result':None}
    items=[{'name':name,'bytes':len(data),'sha256':digest(data),
        'media_type':'text/plain; charset=utf-8' if name=='R-session-info.txt' else 'text/tab-separated-values; charset=utf-8'}
        for name,data in sorted(contents.items())]
    job['result']={'schemaVersion':1,'job_id':job['job_id'],'binding_sha256':job['binding_sha256'],
        'execution':deepcopy(execution),'artifacts':items,'manifest_sha256':c.digest(c.canonical(items)),
        'verified_at':c.stamp(start+timedelta(seconds=2))}
    return job


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.root=self.base/'publish';self.root.mkdir(mode=0o700)
        self.contents={name:('SYNTHETIC bytes '+name+'\n').encode() for name in c.OUTPUTS}
        self.contents['R-session-info.txt']=b'R version 4.6.1\nBLAS: /usr/lib/x86_64-linux-gnu/blas/libblas.so\n'
        self.job=make_job(self.contents);self.folder=self.root/self.job['job_id'];self.folder.mkdir(mode=0o700)
        for name,data in self.contents.items():
            p=self.folder/name;p.write_bytes(data);p.chmod(0o600)
        self.reader=r.RawQCArtifactReader(self.root)

    def read(self,name='raw-matrix.tsv',job=None,**kwargs):return self.reader.read_verified(self.job if job is None else job,name,**kwargs)
    def denied(self,**kwargs):
        with self.assertRaises(r.ArtifactReadFailure) as error:self.read(**kwargs)
        self.assertIn(error.exception.code,(r.UNAVAILABLE,r.TIMEOUT,r.CLOSE_UNCONFIRMED))
        self.assertEqual(str(error.exception),error.exception.code)
        return error.exception
    def rebind_manifest(self):
        self.job['result']['manifest_sha256']=c.digest(c.canonical(self.job['result']['artifacts']))
    def rewrite(self,name,data):
        p=self.folder/name;p.write_bytes(data);p.chmod(0o600);self.contents[name]=data
        item=next(x for x in self.job['result']['artifacts'] if x['name']==name)
        item.update(bytes=len(data),sha256=digest(data));self.rebind_manifest()

    def test_default_None_zero_filesystem_and_no_check_callback(self):
        with patch.object(r.os,'open',side_effect=AssertionError('no FS')),patch.object(r.os,'stat',side_effect=AssertionError('no FS')):
            with self.assertRaises(r.ArtifactReadFailure) as error:r.RawQCArtifactReader().read_verified({},'../x',check=lambda:(_ for _ in ()).throw(AssertionError('no callback')))
        self.assertEqual(error.exception.code,r.OFF)

    def test_all_seven_return_exact_immutable_bytes_and_fixed_metadata(self):
        before=deepcopy(self.job)
        for name in sorted(c.OUTPUTS):
            value=self.read(name=name)
            self.assertIs(type(value.body),bytes);self.assertEqual(value.body,self.contents[name])
            self.assertEqual(value.name,name);self.assertEqual(value.length,len(value.body));self.assertEqual(value.sha256,digest(value.body))
            expected='text/plain; charset=utf-8' if name=='R-session-info.txt' else 'text/tab-separated-values; charset=utf-8'
            self.assertEqual(value.media_type,expected)
            with self.assertRaises(FrozenInstanceError):value.name='other'
        self.assertEqual(self.job,before)
        self.assertIn(b'/usr/lib/',self.read(name='R-session-info.txt').body)

    def test_no_write_fsync_build_result_or_database_row_fabrication(self):
        opened=[];original=r.os.open
        def read_only(path,flags,*args,**kwargs):
            self.assertFalse(flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND))
            opened.append(path);return original(path,flags,*args,**kwargs)
        with patch.object(r.os,'open',side_effect=read_only),patch.object(r.os,'fsync',side_effect=AssertionError('no fsync')),patch.object(r.os,'write',side_effect=AssertionError('no write')),patch.object(c,'decode_record',side_effect=AssertionError('no fake DB row')):
            self.assertEqual(self.read().body,self.contents['raw-matrix.tsv'])
        self.assertTrue(opened)

    def test_invalid_names_and_succeeded_shape_before_filesystem(self):
        variants=[]
        for key,value in [('schemaVersion',True),('status','running'),('status','failed'),('observation','reconciliation_pending'),('job_id','../x'),('params',{'x':1}),('error',{'code':'runner_failed'}),('finished_at',None)]:
            job=deepcopy(self.job);job[key]=value;variants.append(job)
        job=deepcopy(self.job);job['owner_id']=str(uuid4());variants.append(job)
        with patch.object(r.os,'open',side_effect=AssertionError('invalid metadata must not open')):
            for job in variants:self.denied(job=job)
            for name in ('../raw-matrix.tsv','other.tsv','/etc/passwd','%2e%2e','raw-matrix.tsv\x00',Path('raw-matrix.tsv'),None):self.denied(name=name)

    def test_request_binding_execution_and_time_relations_strict_before_FS(self):
        variants=[]
        for key in ('request_sha256','binding_sha256'):
            job=deepcopy(self.job);job[key]='f'*64;variants.append(job)
        job=deepcopy(self.job);job['result']['execution']['runner']['sha256']='f'*64;variants.append(job)
        job=deepcopy(self.job);job['result']['job_id']=str(uuid4());variants.append(job)
        job=deepcopy(self.job);job['result']['verified_at']=job['created_at'];variants.append(job)
        job=deepcopy(self.job);job['finished_at']=job['started_at'];variants.append(job)
        with patch.object(r.os,'open',side_effect=AssertionError('no FS')):
            for job in variants:self.denied(job=job)

    def test_manifest_exact_names_unique_order_hash_media_and_size(self):
        variants=[]
        for change in (
            lambda j:j['result']['artifacts'].pop(),
            lambda j:j['result']['artifacts'].reverse(),
            lambda j:j['result']['artifacts'][0].update(name='../x'),
            lambda j:j['result']['artifacts'][0].update(media_type='text/html'),
            lambda j:j['result']['artifacts'][0].update(bytes=True),
            lambda j:j['result']['artifacts'][0].update(bytes=r.PER_FILE+1),
            lambda j:j['result']['artifacts'][0].update(sha256='X'*64),
            lambda j:j['result'].update(manifest_sha256='f'*64),
        ):
            job=deepcopy(self.job);change(job);variants.append(job)
        with patch.object(r.os,'open',side_effect=AssertionError('no FS')):
            for job in variants:self.denied(job=job)

    def test_metadata_snapshot_independent_of_mutation_during_check(self):
        original=r.os.pread;seen=[False];sha=digest(self.contents['raw-matrix.tsv'])
        def pread(*args):data=original(*args);seen[0]=True;return data
        def check():
            if seen[0]:self.job['result']['artifacts'][0]['sha256']='f'*64
        with patch.object(r.os,'pread',side_effect=pread):value=self.read(check=check)
        self.assertEqual(value.sha256,sha);self.assertEqual(value.body,self.contents['raw-matrix.tsv'])

    def test_root_and_ancestor_symlinks_rejected(self):
        alias=self.base/'alias';alias.symlink_to(self.root,target_is_directory=True)
        self.reader=r.RawQCArtifactReader(alias);self.denied()
        parent_alias=self.base/'alias-parent';parent_alias.symlink_to(self.base,target_is_directory=True)
        self.reader=r.RawQCArtifactReader(parent_alias/'publish');self.denied()

    def test_root_and_job_permissions_rejected(self):
        self.root.chmod(0o777);self.denied();self.root.chmod(0o700)
        self.folder.chmod(0o755);self.denied();self.folder.chmod(0o700)
        self.assertEqual(self.read().body,self.contents['raw-matrix.tsv'])

    def test_unsafe_writable_nonsticky_ancestor_rejected(self):
        self.base.chmod(0o777)
        try:self.denied()
        finally:self.base.chmod(0o700)

    def test_leaf_permissions_wrong_uid_hardlink_and_symlink_rejected(self):
        leaf=self.folder/'raw-matrix.tsv';leaf.chmod(0o644);self.denied();leaf.chmod(0o600)
        original=r.os.fstat
        def wrong_uid(fd):
            s=original(fd)
            if stat.S_ISREG(s.st_mode):
                fields=list(s);fields[4]=os.geteuid()+1;return os.stat_result(fields)
            return s
        with patch.object(r.os,'fstat',side_effect=wrong_uid):self.denied()
        extra=self.base/'linked';os.link(leaf,extra);self.denied();extra.unlink()
        leaf.unlink();outside=self.base/'own-outside';outside.write_bytes(self.contents['raw-matrix.tsv']);leaf.symlink_to(outside);self.denied()

    def test_fifo_leaf_nonblocking_no_read(self):
        leaf=self.folder/'raw-matrix.tsv';leaf.unlink();os.mkfifo(leaf,0o600)
        with patch.object(r.os,'pread',side_effect=AssertionError('FIFO not read')):self.denied()

    def test_missing_extra_or_symlink_job_rejected(self):
        extra=self.folder/'extra';extra.write_bytes(b'x');self.denied();extra.unlink()
        leaf=self.folder/'summary.tsv';data=leaf.read_bytes();leaf.unlink();self.denied();leaf.write_bytes(data);leaf.chmod(0o600)
        moved=self.root/'moved';self.folder.rename(moved);self.folder.symlink_to(moved,target_is_directory=True);self.denied()

    def test_other_six_contents_are_not_claimed_byte_verified(self):
        (self.folder/'summary.tsv').write_bytes(b'SYNTHETIC later corruption of another artifact')
        self.assertEqual(self.read().body,self.contents['raw-matrix.tsv'])
        self.denied(name='summary.tsv')

    def test_hash_and_size_mismatch_rejected_without_changing_history(self):
        before=deepcopy(self.job);leaf=self.folder/'raw-matrix.tsv'
        leaf.write_bytes(b'X'*len(self.contents['raw-matrix.tsv']));self.denied()
        leaf.write_bytes(b'X');self.denied()
        self.assertEqual(self.job,before);self.assertEqual(self.job['status'],'succeeded')

    def test_same_size_mutation_during_hash_is_rejected(self):
        original=r.os.pread;changed=[False]
        def pread(fd,size,offset):
            data=original(fd,size,offset)
            if data and not changed[0]:
                changed[0]=True;(self.folder/'raw-matrix.tsv').write_bytes(b'Y'*len(self.contents['raw-matrix.tsv']))
            return data
        with patch.object(r.os,'pread',side_effect=pread):self.denied()

    def test_leaf_path_replaced_while_open_is_rejected(self):
        original=r.os.pread;changed=[False]
        def pread(fd,size,offset):
            data=original(fd,size,offset)
            if data and not changed[0]:
                changed[0]=True;leaf=self.folder/'raw-matrix.tsv';leaf.rename(self.base/'old-leaf')
                leaf.write_bytes(self.contents['raw-matrix.tsv']);leaf.chmod(0o600)
            return data
        with patch.object(r.os,'pread',side_effect=pread):self.denied()

    def test_job_directory_replaced_during_read_rejected(self):
        original=r.os.pread;changed=[False]
        def pread(fd,size,offset):
            data=original(fd,size,offset)
            if data and not changed[0]:
                changed[0]=True;self.folder.rename(self.root/'old-job')
                self.folder.mkdir(mode=0o700)
                for name,value in self.contents.items():
                    path=self.folder/name;path.write_bytes(value);path.chmod(0o600)
            return data
        with patch.object(r.os,'pread',side_effect=pread):self.denied()

    def test_publish_directory_replaced_during_read_rejected(self):
        original=r.os.pread;changed=[False]
        def pread(fd,size,offset):
            data=original(fd,size,offset)
            if data and not changed[0]:
                changed[0]=True;self.root.rename(self.base/'old-publish')
                self.root.mkdir(mode=0o700);self.folder.mkdir(mode=0o700)
                for name,value in self.contents.items():
                    path=self.folder/name;path.write_bytes(value);path.chmod(0o600)
            return data
        with patch.object(r.os,'pread',side_effect=pread):self.denied()

    def test_read_only_private_directories_and_file_succeed(self):
        leaf=self.folder/'raw-matrix.tsv';leaf.chmod(0o400)
        self.folder.chmod(0o500);self.root.chmod(0o500)
        try:self.assertEqual(self.read().body,self.contents['raw-matrix.tsv'])
        finally:self.root.chmod(0o700);self.folder.chmod(0o700)

    def test_manifest_total_cap_and_duplicate_name_before_FS(self):
        job=deepcopy(self.job)
        for item in job['result']['artifacts']:item['bytes']=r.PER_FILE
        job['result']['manifest_sha256']=c.digest(c.canonical(job['result']['artifacts']))
        duplicate=deepcopy(self.job)
        duplicate['result']['artifacts'][0]=deepcopy(duplicate['result']['artifacts'][1])
        duplicate['result']['manifest_sha256']=c.digest(c.canonical(duplicate['result']['artifacts']))
        with patch.object(r.os,'open',side_effect=AssertionError('invalid manifest before FS')):
            self.denied(job=job);self.denied(job=duplicate)

    def test_descriptor_tracking_allocation_failure_closes_new_fd(self):
        class NoAllocation(list):
            def append(self,item):raise MemoryError('SYNTHETIC tracking allocation failure')
        owned=r._Owned();owned.fds=NoAllocation();opened=[];closed=[]
        original_open=r.os.open;original_close=r.os.close
        def op(*args,**kw):fd=original_open(*args,**kw);opened.append(fd);return fd
        def cl(fd):closed.append(fd);return original_close(fd)
        with patch.object(r.os,'open',side_effect=op),patch.object(r.os,'close',side_effect=cl):
            with self.assertRaises(MemoryError):owned.open(self.folder/'raw-matrix.tsv',os.O_RDONLY)
        self.assertEqual(opened,closed);self.assertFalse(owned.fds)
        for fd in opened:
            with self.assertRaises(OSError):os.fstat(fd)

    def test_early_EOF_and_read_error_are_sanitized(self):
        with patch.object(r.os,'pread',return_value=b''):self.denied()
        with patch.object(r.os,'pread',side_effect=OSError('PRIVATE_SERVER_PATH must not escape')):
            self.assertNotIn('PRIVATE',str(self.denied()))

    def test_external_and_internal_budget_timeout_close_fds(self):
        original=r.os.pread;read=[False]
        def pread(*args):result=original(*args);read[0]=True;return result
        def check():
            if read[0]:raise TimeoutError('SECRET_CONTEXT')
        with patch.object(r.os,'pread',side_effect=pread):self.assertEqual(self.denied(check=check).code,r.TIMEOUT)
        clock=iter([0,10])
        with patch.object(r.time,'monotonic',side_effect=lambda:next(clock,10)):
            self.assertEqual(self.denied().code,r.TIMEOUT)

    def test_async_check_and_non_None_return_rejected_without_FS(self):
        async def check():return None
        with patch.object(r.os,'open',side_effect=AssertionError('no FS')):
            self.denied(check=check);self.denied(check=lambda:True);self.denied(check=None)

    def test_all_open_failure_positions_close_previously_owned_descriptors(self):
        original_open=r.os.open;original_close=r.os.close
        # Capture the real positive count, then inject each open-position failure.
        opened=[]
        def capture(*a,**kw):fd=original_open(*a,**kw);opened.append(fd);return fd
        with patch.object(r.os,'open',side_effect=capture):self.read()
        total=len(opened)
        for failure_at in range(1,total+1):
            seen=[];closed=[];count=[0]
            def op(*a,**kw):
                count[0]+=1
                if count[0]==failure_at:raise OSError('SYNTHETIC open failure')
                fd=original_open(*a,**kw);seen.append(fd);return fd
            def cl(fd):closed.append(fd);return original_close(fd)
            with patch.object(r.os,'open',side_effect=op),patch.object(r.os,'close',side_effect=cl):self.denied()
            self.assertEqual(Counter(seen),Counter(closed))

    def test_close_errors_after_kernel_release_close_all_other_owned_fds_once(self):
        original_open=r.os.open;original_close=r.os.close;opened=[];closed=[]
        def op(*a,**kw):fd=original_open(*a,**kw);opened.append(fd);return fd
        def cl(fd):
            closed.append(fd);original_close(fd);raise OSError(errno.EIO,'SYNTHETIC Linux late close error')
        with patch.object(r.os,'open',side_effect=op),patch.object(r.os,'close',side_effect=cl):
            self.assertEqual(self.denied().code,r.CLOSE_UNCONFIRMED)
        self.assertEqual(Counter(opened),Counter(closed));self.assertEqual(len(closed),len(set(closed)))
        for fd in opened:
            with self.assertRaises(OSError):os.fstat(fd)

    def test_close_error_then_fd_reuse_is_never_blindly_closed(self):
        replacement=self.base/'replacement-owned-by-test';replacement.write_bytes(b'own replacement')
        real_open=r.os.open;real_close=r.os.close;opened=[];closed=[];reused=[None]
        def op(*a,**kw):fd=real_open(*a,**kw);opened.append(fd);return fd
        def cl(fd):
            closed.append(fd);real_close(fd)
            if reused[0] is None:
                reused[0]=real_open(replacement,os.O_RDONLY);self.assertEqual(reused[0],fd)
                raise OSError(errno.EINTR,'SYNTHETIC released and reused FD')
        try:
            with patch.object(r.os,'open',side_effect=op),patch.object(r.os,'close',side_effect=cl):
                self.assertEqual(self.denied().code,r.CLOSE_UNCONFIRMED)
            self.assertEqual(Counter(opened),Counter(closed));self.assertEqual(os.pread(reused[0],32,0),b'own replacement')
        finally:
            if reused[0] is not None:real_close(reused[0])

    def test_close_uncertainty_quarantines_instance_and_repeated_reads_open_nothing(self):
        real_open=r.os.open;real_close=r.os.close;opened=[];closed=[];unconfirmed=[]
        def op(*args,**kw):fd=real_open(*args,**kw);opened.append(fd);return fd
        def cl(fd):
            closed.append(fd)
            if not unconfirmed:
                unconfirmed.append(fd)
                raise OSError(errno.EIO,'PRIVATE fd/path/raw error must not escape')
            return real_close(fd)
        try:
            with patch.object(r.os,'open',side_effect=op),patch.object(r.os,'close',side_effect=cl):
                error=self.denied();self.assertEqual(error.code,r.CLOSE_UNCONFIRMED)
            self.assertTrue(self.reader._quarantined)
            self.assertEqual(Counter(opened),Counter(closed));self.assertEqual(len(closed),len(set(closed)))
            # The synthetic failure deliberately left one fd open. Caller cannot
            # assume cleanup, retry that number, or accumulate another read.
            self.assertTrue(stat.S_ISDIR(os.fstat(unconfirmed[0]).st_mode))
            for fd in opened:
                if fd not in unconfirmed:
                    with self.assertRaises(OSError):os.fstat(fd)
            def no_check():raise AssertionError('quarantine must precede check')
            with patch.object(r,'_Owned',side_effect=AssertionError('no new acquisition context')),patch.object(r.os,'open',side_effect=AssertionError('quarantine must precede filesystem')):
                for _ in range(8):self.assertEqual(self.denied(check=no_check).code,r.CLOSE_UNCONFIRMED)
            self.assertTrue(self.reader._quarantined)
        finally:
            # Test alone owns the injected pre-kernel failure and resolves it;
            # the production helper never blindly retries an uncertain number.
            for fd in unconfirmed:real_close(fd)

    def test_tracking_failure_close_uncertainty_also_quarantines_instance(self):
        class NoAllocation(list):
            def append(self,item):raise MemoryError('SYNTHETIC tracking allocation failure')
        owned=r._Owned();owned.fds=NoAllocation();opened=[];closed=[]
        real_open=r.os.open;real_close=r.os.close
        def op(*args,**kw):fd=real_open(*args,**kw);opened.append(fd);return fd
        def cl(fd):closed.append(fd);raise OSError(errno.EIO,'SYNTHETIC close uncertainty')
        try:
            with patch.object(r,'_Owned',return_value=owned),patch.object(r.os,'open',side_effect=op),patch.object(r.os,'close',side_effect=cl):
                self.assertEqual(self.denied().code,r.CLOSE_UNCONFIRMED)
            self.assertTrue(owned.cleanup_uncertain);self.assertTrue(self.reader._quarantined)
            self.assertEqual(opened,closed);self.assertEqual(len(opened),1)
            with patch.object(r.os,'open',side_effect=AssertionError('quarantine must not reacquire')):
                self.assertEqual(self.denied().code,r.CLOSE_UNCONFIRMED)
        finally:
            for fd in opened:real_close(fd)

    def test_directory_iterator_close_error_also_quarantines_instance(self):
        real_scandir=r.os.scandir;closed=[]
        class IteratorWithCloseError:
            def __init__(self,fd):self.entries=real_scandir(fd)
            def __iter__(self):return self
            def __next__(self):return next(self.entries)
            def close(self):
                closed.append(self);self.entries.close()
                raise OSError(errno.EIO,'PRIVATE iterator cleanup detail')
        with patch.object(r.os,'scandir',side_effect=IteratorWithCloseError):
            self.assertEqual(self.denied().code,r.CLOSE_UNCONFIRMED)
        self.assertEqual(len(closed),1);self.assertTrue(self.reader._quarantined)
        with patch.object(r.os,'open',side_effect=AssertionError('quarantine before filesystem')):
            self.assertEqual(self.denied().code,r.CLOSE_UNCONFIRMED)

    def test_success_closes_all_owned_fds_before_return_and_keeps_instance_usable(self):
        real_open=r.os.open;real_close=r.os.close;opened=[];closed=[]
        def op(*args,**kw):fd=real_open(*args,**kw);opened.append(fd);return fd
        def cl(fd):closed.append(fd);return real_close(fd)
        with patch.object(r.os,'open',side_effect=op),patch.object(r.os,'close',side_effect=cl):
            self.assertEqual(self.read().body,self.contents['raw-matrix.tsv'])
        self.assertEqual(Counter(opened),Counter(closed));self.assertFalse(self.reader._quarantined)
        for fd in opened:
            with self.assertRaises(OSError):os.fstat(fd)
        self.assertEqual(self.read(name='R-session-info.txt').body,self.contents['R-session-info.txt'])

    def test_check_BaseException_still_closes_owned_fds(self):
        original_open=r.os.open;original_close=r.os.close;original_read=r.os.pread
        opened=[];closed=[];seen=[False]
        def op(*a,**kw):fd=original_open(*a,**kw);opened.append(fd);return fd
        def cl(fd):closed.append(fd);return original_close(fd)
        def pread(*args):data=original_read(*args);seen[0]=True;return data
        def check():
            if seen[0]:raise KeyboardInterrupt('SYNTHETIC control stop')
        with patch.object(r.os,'open',side_effect=op),patch.object(r.os,'close',side_effect=cl),patch.object(r.os,'pread',side_effect=pread):
            with self.assertRaises(KeyboardInterrupt):self.read(check=check)
        self.assertEqual(Counter(opened),Counter(closed))

    def test_exact_64MiB_success_and_plus_one_metadata_or_file_rejected(self):
        data=b'x'*r.PER_FILE;self.rewrite('raw-matrix.tsv',data)
        value=self.read();self.assertEqual(value.length,r.PER_FILE);self.assertEqual(value.body,data)
        job=deepcopy(self.job);item=next(x for x in job['result']['artifacts'] if x['name']=='raw-matrix.tsv');item['bytes']+=1
        job['result']['manifest_sha256']=c.digest(c.canonical(job['result']['artifacts']))
        with patch.object(r.os,'open',side_effect=AssertionError('cap before FS')):self.denied(job=job)
        with (self.folder/'raw-matrix.tsv').open('ab') as f:f.write(b'x')
        with patch.object(r.os,'pread',side_effect=AssertionError('size before read')):self.denied()


from types import SimpleNamespace
f=SimpleNamespace(r=r,c=c,make_job=make_job)

class PureAttacks(unittest.TestCase):
    def test_disabled_before_check_or_acquisition(self):
        with patch.object(r,'_Owned',side_effect=AssertionError('no acquisition')):
            with self.assertRaises(r.ArtifactReadFailure) as error:r.RawQCArtifactReader().read_verified({},'raw-matrix.tsv',check=lambda:(_ for _ in ()).throw(AssertionError('no check')))
        self.assertEqual(error.exception.code,r.OFF)
    def test_multiple_close_BaseExceptions_never_skip_other_numeric_FDs(self):
        owned=r._Owned();owned.fds=[31,32,33];seen=[]
        def close(fd):seen.append(fd);raise BaseException('SYNTHETIC_CLOSE_PRIVATE')
        with patch.object(r.os,'close',side_effect=close):self.assertFalse(owned.close_all())
        self.assertEqual(seen,[33,32,31]);self.assertEqual(owned.fds,[]);self.assertTrue(owned.cleanup_uncertain)
        with patch.object(r.os,'close',side_effect=AssertionError('no retry')):self.assertFalse(owned.close_all())
    def test_owned_close_failure_poison_is_before_repeated_acquisition(self):
        owned=r._Owned();owned.fds=[71,72];reader=r.RawQCArtifactReader('server-only-root')
        with patch.object(r,'_Owned',return_value=owned),patch.object(r.os,'close',side_effect=BaseException('SYNTHETIC_CLOSE_PRIVATE')):
            with self.assertRaises(r.ArtifactReadFailure) as error:reader.read_verified({},'raw-matrix.tsv')
        self.assertEqual(error.exception.code,r.CLOSE_UNCONFIRMED);self.assertTrue(reader._quarantined)
        with patch.object(r,'_Owned',side_effect=AssertionError('no new owner')),patch.object(r.os,'open',side_effect=AssertionError('no FS')):
            for _ in range(3):
                with self.assertRaises(r.ArtifactReadFailure) as error:reader.read_verified({},'raw-matrix.tsv',check=lambda:(_ for _ in ()).throw(AssertionError('no check')))
                self.assertEqual(error.exception.code,r.CLOSE_UNCONFIRMED)
    def test_iterator_timeout_plus_close_BaseException_preserves_cleanup_unknown(self):
        owned=r._Owned();closed=[]
        class Entries:
            def __iter__(self):return self
            def __next__(self):return type('Item',(),{'name':'raw-matrix.tsv'})()
            def close(self):closed.append(True);raise BaseException('SYNTHETIC_ITERATOR_PRIVATE')
        def timeout():raise TimeoutError('synthetic deadline')
        with patch.object(r.os,'scandir',return_value=Entries()):
            with self.assertRaises(r.ArtifactReadFailure) as error:r.exact_names(99,timeout,owned)
        self.assertEqual(error.exception.code,r.CLOSE_UNCONFIRMED);self.assertEqual(closed,[True]);self.assertTrue(owned.cleanup_uncertain)
    def test_tracking_allocation_failure_close_unknown_not_numeric_retry(self):
        class Full(list):
            def append(self,value):raise MemoryError('synthetic memory')
        owned=r._Owned();owned.fds=Full()
        with patch.object(r.os,'open',return_value=81),patch.object(r.os,'close',side_effect=BaseException('SYNTHETIC_CLOSE_PRIVATE')) as close:
            with self.assertRaises(r.ArtifactReadFailure) as error:owned.open('synthetic',0)
            self.assertEqual(error.exception.code,r.CLOSE_UNCONFIRMED);close.assert_called_once_with(81)
        self.assertFalse(owned.close_all());self.assertEqual(owned.fds,[])
    def test_public_dto_snapshot_detaches_and_bad_outer_keys_refuse(self):
        contents={name:b'synthetic-only' for name in f.c.OUTPUTS};job=f.make_job(contents)
        snapshot,pin=r.checked_public(job,'raw-matrix.tsv')
        job['result']['artifacts'][0]['bytes']=999
        self.assertEqual(pin['bytes'],14);self.assertEqual(snapshot['result']['artifacts'][0]['bytes'],14)
        job=f.make_job(contents);job['owner_id']='not-an-owner-capability'
        with self.assertRaises(Exception):r.checked_public(job,'raw-matrix.tsv')

if __name__=='__main__':unittest.main(verbosity=2)
