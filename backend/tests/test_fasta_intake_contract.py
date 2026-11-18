"""Synthetic model tests only; no Owner/DB/HTTP/engine or real FASTA input."""
import ast
import builtins
from dataclasses import FrozenInstanceError, asdict, replace
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
from types import MappingProxyType
import unittest
from unittest.mock import patch
from uuid import UUID, uuid5
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cyomics_projects import fasta_intake_contract as m
from cyomics_projects.fasta_index import parse_fasta

P='11111111-1111-4111-8111-111111111111'
S='22222222-2222-4222-8222-222222222222'
R='33333333-3333-4333-8333-333333333333'
OTHER='44444444-4444-4444-8444-444444444444'
STAMP='2026-11-18T01:02:03.000004Z'

def sha(raw):return hashlib.sha256(raw).hexdigest()
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False)
def control():return m.ParseControl(lambda:None,lambda:0.0)
def source(raw=b'>synthetic\nacD\n',**updates):
    meta=dict(source_id=S,project_id=P,request_id=R,filename='Synthetic.FASTA',format='protein_fasta',
        bytes=len(raw),sha256=sha(raw),status='stored',createdAt=STAMP)
    meta.update(updates)
    return m.source_from_json(canonical(meta).encode(),raw)
def build(src=None,**updates):
    kwargs=dict(expected_project_id=P,expected_source_id=S,control=control(),config_json=m.configuration_json())
    kwargs.update(updates)
    return m.build_candidate(source() if src is None else src,**kwargs)
def rebuild(src,candidate,**updates):
    kwargs=dict(expected_project_id=P,expected_source_id=S,expected_index_version_id=candidate.index_version_id,control=control())
    kwargs.update(updates)
    return m.rebuild_and_verify(src,candidate,**kwargs)

class IntakeTests(unittest.TestCase):
    def refusal(self,operation,code=None):
        with self.assertRaises(m.IntakeError) as caught:operation()
        if code:self.assertEqual(caught.exception.code,code)
        self.assertEqual(caught.exception.args,(caught.exception.code,))
        self.assertNotIn('PRIVATE',repr(caught.exception))
        return caught.exception

    def test_default_unconfigured_no_runtime_io_or_control(self):
        src=source()
        hook=unittest.mock.Mock(side_effect=AssertionError('PRIVATE_CONTROL'))
        with patch.object(builtins,'open',side_effect=AssertionError()),patch.object(Path,'read_bytes',side_effect=AssertionError()),patch.object(socket,'socket',side_effect=AssertionError()),patch.object(subprocess,'run',side_effect=AssertionError()):
            self.refusal(lambda:m.build_candidate(),'fasta_intake_unavailable')
            self.refusal(lambda:m.build_candidate(src,control=m.ParseControl(hook,hook)),'fasta_intake_unavailable')
            m.ParseControl()
        hook.assert_not_called()

    def test_import_module_does_not_open_start_thread_or_call_clock(self):
        raw=Path(m.__file__).read_bytes()
        namespace={'__name__':'cyomics_projects.synthetic_intake_probe','__package__':'cyomics_projects'}
        with patch.object(builtins,'open',side_effect=AssertionError()),patch.object(Path,'read_bytes',side_effect=AssertionError()),patch.object(os,'open',side_effect=AssertionError()),patch.object(os,'scandir',side_effect=AssertionError()),patch.object(threading.Thread,'start',side_effect=AssertionError()),patch.object(socket,'socket',side_effect=AssertionError()),patch.object(subprocess,'run',side_effect=AssertionError()),patch.object(m.time,'thread_time',side_effect=AssertionError()):
            exec(compile(raw,'<synthetic-intake>','exec'),namespace)
            self.assertIsNone(namespace['ParseControl']().checkpoint)

    def test_config_exact_pin_hash_and_fresh_snapshot(self):
        raw=m.configuration_json().encode('ascii')
        self.assertEqual(len(raw),848)
        self.assertEqual(sha(raw),m.CONFIG_SHA256)
        snapshot=m.configuration();snapshot['limits']['max_records']=1
        self.assertEqual(m.configuration()['limits']['max_records'],50000)
        for bad in (None,{},m.configuration_json()+' ',m.configuration_json().replace('50000','50001')):
            self.refusal(lambda bad=bad:m.validate_config(bad),'fasta_parser_release_mismatch')

    def test_version_uuid_independent_preimage_and_source_identity(self):
        src=source();candidate=build(src)
        name=':'.join((P,S,src.sha256,'fasta_ascii_identity_index_v0.1',m.CONFIG_SHA256))
        self.assertEqual(candidate.index_version_id,str(uuid5(UUID('ae925a0c-6877-58a0-a4f8-1ec6a8301191'),name)))
        self.assertEqual(build(src),candidate)
        second=source(source_id=OTHER)
        self.assertNotEqual(build(second,expected_source_id=OTHER).index_version_id,candidate.index_version_id)

    def test_independent_complete_jsonl_oracle(self):
        raw=b'\n>  x desc\r\nac\r\n>x duplicate\nAA*\n>X\n\n'
        src=source(raw);candidate=build(src);index=parse_fasta(raw)
        docs=[{'kind':'index','value':{'schemaVersion':1,'index_contract':'fasta_ascii_identity_index_v0.1',
            'sequence_identity_contract':'sequence_identity_review_v0.1','source_bytes':len(raw),
            'source_sha256':sha(raw),'record_count':3,'blank_lines':2,'line_count':7}}]
        docs.extend({'kind':'record','value':asdict(record)} for record in index.records)
        docs.extend({'kind':'id_ordinals','id':key,'ordinals':list(index.id_ordinals[key])} for key in sorted(index.id_ordinals))
        expected=sha(b''.join(canonical(doc).encode('ascii')+b'\n' for doc in docs))
        self.assertEqual(candidate.index_manifest_sha256,expected)
        self.assertNotEqual(candidate.summary_sha256,expected)

    def test_complete_rebuild_returns_all_duplicate_invalid_and_raw_values(self):
        raw=b'>  x raw\r\nac\r\n>x same\nAC\n>x invalid\nA C\n>empty\n\n'
        src=source(raw);candidate=build(src);verified=rebuild(src,candidate)
        self.assertEqual(verified.index,parse_fasta(raw))
        self.assertEqual(verified.index.id_ordinals['x'],(1,2,3))
        self.assertEqual(verified.index.records[0].header_raw,'  x raw')
        self.assertEqual(verified.index.records[0].sequence_lines,('ac',))
        self.assertEqual(verified.index.records[0].sequence_raw,'ac')
        self.assertEqual(verified.index.records[0].canonical_sequence,'AC')
        self.assertIsNone(verified.index.records[2].canonical_sequence)
        summary=json.loads(candidate.summary_json)
        self.assertEqual((summary['record_count'],summary['duplicate_record_excess'],summary['invalid_canonical_record_count']),(4,2,2))
        with self.assertRaises(TypeError):verified.index.id_ordinals['x']=(1,)
        with self.assertRaises(FrozenInstanceError):candidate.source_id=OTHER

    def test_headers_case_sequence_order_wrapping_each_change_full_fingerprint(self):
        raws=[b'>x\nac\n>y\nAA\n',b'>x changed\nac\n>y\nAA\n',b'>x\nAC\n>y\nAA\n',
            b'>y\nAA\n>x\nac\n',b'>x\na\nc\n>y\nAA\n',b'>z\nac\n>y\nAA\n']
        values=[build(source(raw)) for raw in raws]
        self.assertEqual(len({v.index_manifest_sha256 for v in values}),len(raws))
        self.assertEqual(json.loads(values[0].summary_json),json.loads(values[1].summary_json))
        self.assertEqual(json.loads(values[0].summary_json),json.loads(values[2].summary_json))

    def test_unchanged_summary_cannot_replace_full_manifest(self):
        src=source();candidate=build(src)
        self.refusal(lambda:rebuild(src,replace(candidate,index_manifest_sha256='0'*64)))
        self.refusal(lambda:rebuild(src,replace(candidate,index_manifest_sha256=candidate.summary_sha256)))

    def test_internal_record_map_and_null_canonical_tampering_rejected(self):
        index=parse_fasta(b'>x\nAA\n>x\nCC\n')
        bad_records=[replace(index.records[0],canonical_sequence=None),replace(index.records[0],sequence_sha256=None),
            replace(index.records[0],canonical_issue='illegal_residue'),replace(index.records[0],ordinal=True),
            replace(index.records[0],header_raw='x\nPRIVATE'),replace(index.records[0],sequence_raw='PRIVATE')]
        for record in bad_records:
            self.refusal(lambda record=record:m.fingerprint_index(replace(index,records=(record,index.records[1])),control=control()))
        for mapping in ({'x':(1,)},{'x':(True,2)},{'x':(2,1)},{'x':(1,2),'extra':(1,)}):
            self.refusal(lambda mapping=mapping:m.fingerprint_index(replace(index,id_ordinals=MappingProxyType(mapping)),control=control()))

    def test_source_metadata_length_sha_and_scope_mismatches(self):
        src=source()
        for bad in (replace(src,bytes=src.bytes+1),replace(src,sha256='0'*64),replace(src,content=b'PRIVATE'),
                    replace(src,format='proteinGroups_txt'),replace(src,status='parsed')):
            self.refusal(lambda bad=bad:build(bad),'fasta_source_integrity_failed')
        self.refusal(lambda:build(src,expected_project_id=OTHER),'fasta_source_integrity_failed')
        self.refusal(lambda:build(src,expected_source_id=OTHER),'fasta_source_integrity_failed')

    def test_parseable_prefix_normal_eof_is_not_complete_source(self):
        prefix=b'>x\nAA\n';src=source(prefix+b'>y\nCC\n')
        with patch.object(m,'_chunks',return_value=iter((prefix,))):
            self.refusal(lambda:build(src),'fasta_source_integrity_failed')

    def test_late_iterator_exception_after_valid_prefix_is_fixed_and_context_free(self):
        src=source(b'>x\nAA\n>y\nCC\n')
        def broken():
            yield b'>x\nAA\n'
            raise RuntimeError('PRIVATE_SEQUENCE_CONTENT')
        with patch.object(m,'_chunks',return_value=broken()):
            error=self.refusal(lambda:build(src),'fasta_intake_unavailable')
        self.assertIsNone(error.__context__)

    def test_cancel_and_control_failure_no_returned_candidate(self):
        for exception in (TimeoutError('PRIVATE'),KeyboardInterrupt('PRIVATE')):
            ctl=m.ParseControl(unittest.mock.Mock(side_effect=exception),lambda:0)
            error=self.refusal(lambda:build(control=ctl),'fasta_intake_timeout')
            self.assertIsNone(error.__context__)
        self.refusal(lambda:build(control=m.ParseControl(lambda:False,lambda:0)),'fasta_intake_unavailable')

    def test_soft_cpu_and_invalid_clock_fail_closed(self):
        for clock in (iter([0,0,9]).__next__,lambda:float('nan'),lambda:10**1000,lambda:True):
            self.refusal(lambda clock=clock:build(control=m.ParseControl(lambda:None,clock)))
        for cap in (True,0,9,float('inf'),10**1000):
            self.refusal(lambda cap=cap:build(control=m.ParseControl(lambda:None,lambda:0,cap)),'fasta_intake_unavailable')
        self.refusal(lambda:build(control=m.ParseControl(lambda:None,iter([1,0]).__next__)),'fasta_intake_unavailable')

    def test_cancel_at_final_candidate_boundary_and_late_cpu_completion(self):
        original=m.decode_candidate
        after={'done':False}
        def decode(value):
            result=original(value);after['done']=True;return result
        def check():
            if after['done']:raise TimeoutError('PRIVATE_LATE')
        with patch.object(m,'decode_candidate',side_effect=decode):
            self.refusal(lambda:build(control=m.ParseControl(check,lambda:0)),'fasta_intake_timeout')
        after['done']=False
        with patch.object(m,'decode_candidate',side_effect=decode):
            self.refusal(lambda:build(control=m.ParseControl(lambda:None,lambda:9 if after['done'] else 0)),
                'fasta_intake_timeout')

    def test_parse_failures_no_candidate_and_safe_issue_only(self):
        cases=[(b'\xff','INVALID_UTF8'),(b'\xef\xbb\xbf>x\nAA','UTF8_BOM'),('>x\nÅ'.encode(),'NON_ASCII_FASTA'),
            (b'>x\rAA','LONE_CR'),(b'AA','SEQUENCE_BEFORE_HEADER'),(b'> \nAA','EMPTY_ID')]
        for raw,issue in cases:
            error=self.refusal(lambda raw=raw:build(source(raw)),'fasta_index_failed')
            self.assertEqual(error.issue.code,issue)
            self.assertIsNone(error.__context__)

    def test_strict_json_duplicate_extra_unicode_and_nonfinite(self):
        meta=dict(source_id=S,project_id=P,request_id=R,filename='synthetic.fasta',format='protein_fasta',
            bytes=1,sha256=sha(b'x'),status='stored',createdAt=STAMP)
        for raw in (b'{"bytes":1,"bytes":2}',b'{"bytes":NaN}',b'\xff',b'{"private":"PRIVATE"}'):
            error=self.refusal(lambda raw=raw:m.source_from_json(raw,b'x'))
            self.assertIsNone(error.__context__)
        for filename in ('PRIVATE\ud800.fasta','../PRIVATE.fasta','PRIVATE\x00.fasta','x.txt','é'*130+'.fa'):
            bad=meta.copy();bad['filename']=filename
            self.refusal(lambda bad=bad:m.validate_source_dto(bad),'invalid_fasta_request')
        candidate=build()
        extra=m.candidate_snapshot(candidate);extra['Owner']='PRIVATE'
        self.refusal(lambda:m.decode_candidate(extra))
        bad=m.candidate_snapshot(candidate);bad['summary_json']='PRIVATE\ud800'
        error=self.refusal(lambda:m.decode_candidate(bad))
        self.assertIsNone(error.__context__)

    def test_cross_language_integer_semantics_and_summary_invariants(self):
        dto=m.index_dto(build(),STAMP)
        good=dict(dto,source_bytes=float(dto['source_bytes']),summary={k:float(v) for k,v in dto['summary'].items()})
        normalized=m.validate_index_dto(good)
        self.assertIs(type(normalized['source_bytes']),int)
        self.assertTrue(all(type(n) is int for n in normalized['summary'].values()))
        for value in (True,1.25,float('nan'),float('inf'),9007199254740992):
            self.refusal(lambda value=value:m.validate_index_dto(dict(dto,source_bytes=value)))
        for key,value in [('record_count',True),('line_count',0),('blank_lines',dto['summary']['line_count']),
                          ('valid_canonical_record_count',0),('total_raw_sequence_characters',0)]:
            bad=dto['summary'].copy();bad[key]=value
            self.refusal(lambda bad=bad:m.validate_summary(bad,dto['source_bytes']))

    def test_candidate_snapshot_and_public_dto_bindings_not_approval(self):
        candidate=build();snapshot=m.candidate_snapshot(candidate)
        self.assertEqual(m.candidate_from_json(canonical(snapshot).encode()),candidate)
        dto=m.index_dto(candidate,STAMP)
        self.assertEqual(set(dto),m.INDEX_KEYS)
        self.assertFalse(dto['index_materialized']);self.assertTrue(dto['source_complete'])
        dto['summary']['record_count']=100
        self.assertEqual(json.loads(candidate.summary_json)['record_count'],1)
        for key,value in [('index_version_id',OTHER),('config_sha256','0'*64),('source_complete',False),
                          ('index_materialized',True),('status','partial'),('createdAt','2026-02-30T00:00:00.000000Z')]:
            good=m.index_dto(candidate,STAMP);good[key]=value
            self.refusal(lambda good=good:m.validate_index_dto(good))
        for key,value in [('index_version_id',OTHER),('summary_sha256','0'*64),('config_json',m.CONFIG_JSON+' '),('summary_json','{}')]:
            bad=snapshot.copy();bad[key]=value
            self.refusal(lambda bad=bad:m.decode_candidate(bad))

    def test_rebuild_expected_version_and_scope_all_must_match(self):
        src=source();candidate=build(src)
        self.refusal(lambda:rebuild(src,candidate,expected_project_id=OTHER),'fasta_source_integrity_failed')
        self.refusal(lambda:rebuild(src,candidate,expected_source_id=OTHER),'fasta_source_integrity_failed')
        self.refusal(lambda:rebuild(src,candidate,expected_index_version_id=OTHER))
        changed=source(b'>synthetic\nACD\n')
        self.refusal(lambda:rebuild(changed,candidate))

    def test_parser_id_acceptance_not_silently_narrowed_to_provider(self):
        identifier='a'*256
        src=source(('>'+identifier+'\nAA\n').encode())
        verified=rebuild(src,build(src))
        self.assertEqual(verified.index.records[0].id,identifier)
        # Existing domain_runner boundary documented by the contract; no engine/adapter here.
        self.assertIsNone(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:|+-]{0,199}',identifier))

    def test_private_fields_hidden_from_all_model_reprs_and_errors(self):
        src=source(b'>PRIVATE_HEADER\nPRIVATESEQ\n')
        candidate=build(src);verified=rebuild(src,candidate)
        for value in (src,candidate,verified,verified.index,verified.index.records[0],m.ParseControl(lambda:None),m.ParseIssue('PRIVATE',1,'unsupported')):
            self.assertNotIn('PRIVATE',repr(value))
        self.assertEqual(str(m.IntakeError('PRIVATE')),'fasta_intake_unavailable')
        error=self.refusal(lambda:build(replace(src,content=b'PRIVATE')))
        self.assertNotIn('PRIVATE',str(error))

if __name__=='__main__':unittest.main(verbosity=2)
