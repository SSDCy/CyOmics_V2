"""Synthetic factory tests. No real R, procfs, native files, DB or services."""
from contextlib import ExitStack
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cyomics_projects import raw_qc_runtime as r


def config():
    base=Path(__file__).absolute().parent
    pins=tuple(r.NativeDirectoryPin(k,Path(p),1,2,0,0o755) for k,p in r.NATIVE_DIRS)
    return r.Config(base/'inputs',base/'staging',base/'published',123,123,pins)


def info(mode,uid=123,size=0,nlink=1):
    return SimpleNamespace(st_mode=mode,st_uid=uid,st_size=size,st_nlink=nlink,
                           st_dev=1,st_ino=2,st_mtime_ns=3,st_ctime_ns=4)


class FactoryTests(unittest.TestCase):
    def test_disabled_zero_IO_and_no_executor_import(self):
        with patch.object(r,'verify_current',side_effect=AssertionError('IO')),patch.object(r,'_load_runner',side_effect=AssertionError('import')):
            self.assertIsNone(r.create_runtime())

    def test_untrusted_config_rejected_before_IO_or_construction(self):
        for value in ({},'path',[],object(),replace(config(),input_root=None),
                      replace(config(),native_directory_pins=()),replace(config(),expected_uid=True)):
            with self.subTest(value=type(value).__name__),patch.object(r,'_safe_path',side_effect=AssertionError('IO')),patch.object(r,'_load_runner',side_effect=AssertionError('import')):
                with self.assertRaises(r.FactoryError):r.create_runtime(value)

    def test_native_pins_no_capture_or_unknown_env(self):
        c=config()
        for pin in (replace(c.native_directory_pins[0],device=True),
                    replace(c.native_directory_pins[0],env_key='EVIL'),
                    replace(c.native_directory_pins[0],mode=0o777),
                    replace(c.native_directory_pins[0],owner=1),
                    replace(c.native_directory_pins[0],path=Path('/tmp/evil'))):
            with self.subTest(pin=pin),self.assertRaises(r.FactoryError):
                r._validate_config(replace(c,native_directory_pins=(pin,*c.native_directory_pins[1:])))

    def test_complete_manifest_exact_fixed_identity(self):
        raw=r._canonical({'schemaVersion':1,'python_sources':{
            n:{'bytes':s,'sha256':h} for n,s,h in r.SOURCE_PINS}}).encode()
        self.assertEqual(len(raw),477);self.assertEqual(hashlib.sha256(raw).hexdigest(),r.BUNDLE_SHA)
        self.assertIn('process_policy.py',json.loads(raw)['python_sources'])

    def test_file_digest_and_all_metadata_rejections(self):
        data=b'synthetic';p=Path(__file__).absolute();good=info(stat.S_IFREG|0o644,size=len(data))
        for changed in (info(stat.S_IFLNK|0o644,size=len(data)),info(stat.S_IFREG|0o666,size=len(data)),
                        info(stat.S_IFREG|0o644,size=len(data),nlink=2),info(stat.S_IFREG|0o644,uid=0,size=len(data)),
                        info(stat.S_IFREG|0o644,size=99)):
            with patch.object(r,'_safe_path'),patch.object(Path,'lstat',return_value=changed),patch.object(r.os,'open') as opened:
                with self.assertRaises(r.FactoryError):r._file(p,len(data),hashlib.sha256(data).hexdigest(),123,(0o644,))
                opened.assert_not_called()
        for content,expected in ((data,True),(b'Synthetic',False),(data+b'!',False)):
            # Flags exist only in the supported POSIX product path; stub them
            # explicitly here for a portable synthetic test, never in product.
            with patch.object(r.os,'O_CLOEXEC',0,create=True),patch.object(r.os,'O_NOFOLLOW',0,create=True),patch.object(r.os,'O_NONBLOCK',0,create=True),patch.object(r,'_safe_path'),patch.object(Path,'lstat',return_value=good),patch.object(r.os,'open',return_value=7),patch.object(r.os,'fstat',return_value=good),patch.object(r.os,'read',side_effect=[content,b'']),patch.object(r.os,'close') as close:
                if expected:r._file(p,len(data),hashlib.sha256(data).hexdigest(),123,(0o644,))
                else:
                    with self.assertRaises(r.FactoryError):r._file(p,len(data),hashlib.sha256(data).hexdigest(),123,(0o644,))
                close.assert_called_once_with(7)

    def fake_disk(self,stack,c,corrupt=None,root_mode=0o700,extra=False):
        package=r._package_path()
        def lstat(p):
            if p==package:return info(stat.S_IFDIR|0o755)
            if p in (c.input_root,c.staging_root,c.publish_root):return info(stat.S_IFDIR|root_mode)
            return info(stat.S_IFDIR|0o755,uid=0)
        stack.enter_context(patch.object(r,'_platform_available',return_value=True))
        stack.enter_context(patch.object(r.os,'getuid',return_value=123,create=True))
        stack.enter_context(patch.object(r,'_safe_path'))
        stack.enter_context(patch.object(Path,'lstat',lstat))
        stack.enter_context(patch.object(Path,'stat',lstat))
        entries=[package/n for n,_,_ in r.SOURCE_PINS]+[package/'approved_raw_qc.R']
        if extra:entries.append(package/'__pycache__')
        stack.enter_context(patch.object(Path,'iterdir',return_value=iter(entries)))
        def check(p,*args):
            if p.name==corrupt:raise r.FactoryError('synthetic_pin_mismatch')
        return stack.enter_context(patch.object(r,'_file',side_effect=check))

    def test_full_verification_calls_every_fixed_file(self):
        with ExitStack() as s:
            checked=self.fake_disk(s,config());value=r.verify_current(config())
            self.assertEqual(set(value),{'runner','script','output_validator','executable','r_version','entry_kind','limits_profile','limits_sha256'})
            self.assertEqual(checked.call_count,6)
            self.assertEqual({a.args[0].name for a in checked.call_args_list},
                             {n for n,_,_ in r.SOURCE_PINS}|{'approved_raw_qc.R','R'})

    def test_each_source_script_native_failure_prevents_import(self):
        for name in [n for n,_,_ in r.SOURCE_PINS]+['approved_raw_qc.R','R']:
            with self.subTest(name=name),ExitStack() as s:
                self.fake_disk(s,config(),corrupt=name)
                loaded=s.enter_context(patch.object(r,'_load_runner'))
                with self.assertRaises(r.FactoryError):r.create_runtime(config())
                loaded.assert_not_called()

    def test_private_roots_and_no_extra_package_entries(self):
        for options in ({'root_mode':0o755},{'extra':True}):
            with self.subTest(options=options),ExitStack() as s:
                self.fake_disk(s,config(),**options)
                with self.assertRaises(r.FactoryError):r.verify_current(config())

    def test_wrong_uid_and_unsupported_platform(self):
        with patch.object(r,'_platform_available',return_value=False),patch.object(r,'_safe_path') as io:
            with self.assertRaises(r.FactoryError):r.verify_current(config())
            io.assert_not_called()

    def test_stale_native_pins_and_overlapping_roots(self):
        for c in (replace(config(),staging_root=config().input_root),
                  replace(config(),native_directory_pins=(replace(config().native_directory_pins[0],inode=999),*config().native_directory_pins[1:]))):
            with ExitStack() as s:
                self.fake_disk(s,c)
                loaded=s.enter_context(patch.object(r,'_load_runner'))
                with self.assertRaises(r.FactoryError):r.create_runtime(c)
                loaded.assert_not_called()
        with patch.object(r,'_platform_available',return_value=True),patch.object(r.os,'getuid',return_value=999,create=True),patch.object(r,'_safe_path') as io:
            with self.assertRaises(r.FactoryError):r.verify_current(config())
            io.assert_not_called()

    def test_v3_contract_pair_with_synthetic_ordinary_runner(self):
        # Actual candidate contract decoder, but entirely synthetic runner/disk.
        with ExitStack() as s:
            self.fake_disk(s,config());execution=r.verify_current(config())
        package=r._package_path();helper=SimpleNamespace(__file__=str(package/'output_contract.py'))
        helper_name=r.__package__+'.r_raw_qc.output_contract'
        validate=Mock();validate.__module__=helper_name
        module=SimpleNamespace(__file__=str(package/'raw_qc_runner.py'),
            process_policy=SimpleNamespace(__file__=str(package/'process_policy.py'),__name__=r.__package__+'.r_raw_qc.process_policy'),
            validate_outputs=validate,ENV=r.ENV,NATIVE_ENV=dict(r.NATIVE_DIRS),
            NATIVE_EXECUTABLE=Path(r.NATIVE_PATH),NATIVE_EXECUTABLE_SHA256=r.EXECUTABLE_SHA,
            APPROVED_SCRIPT_SHA256=r.SCRIPT_SHA,LIMITS_PROFILE_ID='raw_qc_limits_v3',
            Limits=lambda:object(),limits_metadata=lambda _: {'limits_sha256':r.LIMITS_SHA},
            DirectoryPin=lambda *args:args,Pin=lambda *args:args,Config=lambda **kw:kw,
            RawQCRunner=Mock(return_value=object()))
        initializer=SimpleNamespace(__file__=str(package/'__init__.py'))
        with patch.object(r,'verify_current',return_value=execution),patch.object(r,'_load_runner',return_value=module),patch.dict(sys.modules,{helper_name:helper,r.__package__+'.r_raw_qc':initializer}),patch.object(sys,'dont_write_bytecode',True):
            runner,registration=r.create_runtime(config())
            self.assertIs(runner,module.RawQCRunner.return_value)
            self.assertEqual(registration.snapshot(),execution)
            self.assertEqual(module.RawQCRunner.call_count,1)
            self.assertEqual(module.RawQCRunner.call_args.args[0]['entry_kind'],'native_R')
            module.ENV={'EVIL':'yes'}
            with self.assertRaises(r.FactoryError):r.create_runtime(config())
            self.assertEqual(module.RawQCRunner.call_count,1)
            module.ENV=r.ENV
            helper.__file__=str(package.parent/'foreign.py')
            with self.assertRaises(r.FactoryError):r.create_runtime(config())
            self.assertEqual(module.RawQCRunner.call_count,1)


if __name__=='__main__':unittest.main(verbosity=2)
