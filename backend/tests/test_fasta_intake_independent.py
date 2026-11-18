"""Independent tiny synthetic oracle and corruption/cancellation counterexamples."""
import asyncio
from dataclasses import replace, FrozenInstanceError
import hashlib
import json
from types import MappingProxyType
import unittest
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid5
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cyomics_projects import fasta_intake_contract as m

P='11111111-1111-4111-8111-111111111111'
S='22222222-2222-4222-8222-222222222222'
R='33333333-3333-4333-8333-333333333333'
OTHER='44444444-4444-4444-8444-444444444444'
STAMP='2026-11-18T01:02:03.000004Z'
RAW=(b'\n> z header\r\nac\r\n\r\n>A\tone\nAA\n>z repeat\nAC\n'
     b'>gap\nA-C\n>stop\nA*\n>space\nA C\n>illegal\nA?\n>empty\n\n')
def sha(raw):return hashlib.sha256(raw).hexdigest()
def cj(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False)
def ctl():return m.ParseControl(lambda:None,lambda:0)
def source(raw=RAW,**changes):
    meta=dict(source_id=S,project_id=P,request_id=R,filename='Synthetic.fasta',
        format='protein_fasta',bytes=len(raw),sha256=sha(raw),status='stored',createdAt=STAMP)
    meta.update(changes)
    return m.source_from_json(cj(meta).encode(),raw)
def build(src=None,**changes):
    args=dict(expected_project_id=P,expected_source_id=S,control=ctl(),config_json=m.configuration_json())
    args.update(changes)
    return m.build_candidate(source() if src is None else src,**args)
def verify(src,candidate,**changes):
    args=dict(expected_project_id=P,expected_source_id=S,
        expected_index_version_id=candidate.index_version_id,control=ctl())
    args.update(changes)
    return m.rebuild_and_verify(src,candidate,**args)

def literal_oracle():
    # Deliberately no production parser, fields(), asdict(), or vendor normalizer.
    specs=[(2,' z header','z',['ac',''],'ac','AC',None),
        (5,'A\tone','A',['AA'],'AA','AA',None),
        (7,'z repeat','z',['AC'],'AC','AC',None),
        (9,'gap','gap',['A-C'],'A-C',None,'stop_or_gap'),
        (11,'stop','stop',['A*'],'A*',None,'stop_or_gap'),
        (13,'space','space',['A C'],'A C',None,'sequence_whitespace'),
        (15,'illegal','illegal',['A?'],'A?',None,'illegal_residue'),
        (17,'empty','empty',[''],'',None,'empty_sequence')]
    records=[]
    for n,(line,header,identifier,lines,raw,canonical,issue) in enumerate(specs,1):
        records.append({'ordinal':n,'header_line':line,'header_raw':header,'id':identifier,
            'sequence_lines':lines,'sequence_raw':raw,'raw_sequence_sha256':sha(raw.encode()),
            'canonical_sequence':canonical,'sequence_sha256':sha(canonical.encode()) if canonical else None,
            'normalization_status':'valid_canonical_residues' if canonical else 'invalid_no_canonical_sequence_identity',
            'canonical_issue':issue})
    mapping={'A':[2],'empty':[8],'gap':[4],'illegal':[7],'space':[6],'stop':[5],'z':[1,3]}
    head={'kind':'index','value':{'schemaVersion':1,'index_contract':'fasta_ascii_identity_index_v0.1',
        'sequence_identity_contract':'sequence_identity_review_v0.1','source_bytes':len(RAW),
        'source_sha256':sha(RAW),'record_count':8,'blank_lines':3,'line_count':18}}
    docs=[head]+[{'kind':'record','value':r} for r in records]+[
        {'kind':'id_ordinals','id':identifier,'ordinals':ordinals} for identifier,ordinals in mapping.items()]
    return docs,records,mapping

class Independent(unittest.TestCase):
    def rejected(self,operation,code=None):
        with self.assertRaises(m.IntakeError) as caught:operation()
        error=caught.exception
        if code:self.assertEqual(error.code,code)
        self.assertEqual(error.args,(error.code,))
        self.assertNotIn('SYNTHETIC_PRIVATE',str(error))
        self.assertIsNone(error.__context__)
        return error

    def test_literal_jsonl_oracle_covers_all_eleven_fields_and_exact_order(self):
        docs,records,mapping=literal_oracle()
        candidate=build();result=verify(source(),candidate)
        expected=sha(b''.join(cj(d).encode()+b'\n' for d in docs))
        self.assertEqual(candidate.index_manifest_sha256,expected)
        self.assertEqual(result.index.id_ordinals, {k:tuple(v) for k,v in mapping.items()})
        for observed,expected_record in zip(result.index.records,records):
            self.assertEqual(set(observed.__dataclass_fields__),set(expected_record))
            for key,value in expected_record.items():
                self.assertEqual(getattr(observed,key),tuple(value) if key=='sequence_lines' else value)
        self.assertEqual(json.loads(candidate.summary_json),dict(record_count=8,distinct_id_count=7,
            duplicate_record_excess=1,valid_canonical_record_count=3,invalid_canonical_record_count=5,
            total_raw_sequence_characters=16,line_count=18,blank_lines=3))
        # Every record field and map ordering/final LF participates in the oracle.
        for key in records[0]:
            changed=json.loads(cj(docs));del changed[1]['value'][key]
            self.assertNotEqual(expected,sha(b''.join(cj(d).encode()+b'\n' for d in changed)))
        self.assertNotEqual(expected,sha(b''.join(cj(d).encode()+b'\n' for d in docs)[:-1]))
        changed=docs[:-7]+list(reversed(docs[-7:]))
        self.assertNotEqual(expected,sha(b''.join(cj(d).encode()+b'\n' for d in changed)))

    def test_summary_equal_different_wrapping_full_rebuild_refuses(self):
        a=source(b'>x\nAA\n>y\nCC\n')
        b=source(b'>y\nCC\n>x\nAA\n')
        ca,cb=build(a),build(b)
        self.assertEqual(ca.summary_json,cb.summary_json)
        self.assertNotEqual(ca.index_manifest_sha256,cb.index_manifest_sha256)
        self.rejected(lambda:verify(b,ca))
        self.rejected(lambda:verify(a,replace(ca,index_manifest_sha256=cb.index_manifest_sha256)))

    def test_same_length_observed_substitution_is_not_expected_source_evidence(self):
        src=source(b'>x\nAA\n')
        for observed in (b'>x\nCC\n',b'>x\nAA',b'>x\nAA\n>y\nCC\n'):
            with patch.object(m,'_chunks',return_value=iter((observed,))):
                self.rejected(lambda:build(src),'fasta_source_integrity_failed')
        with patch.object(m,'_digest',wraps=m._digest) as digest:
            self.rejected(lambda:build(replace(src,content=b'>x\nCC\n')),'fasta_source_integrity_failed')
            self.assertTrue(any(call.args==(b'>x\nCC\n',) for call in digest.call_args_list))

    def test_all_records_map_and_raw_values_checked_after_rebuild(self):
        src=source();candidate=build(src);index=verify(src,candidate).index
        corruptions=[replace(index,records=index.records[:-1]),
            replace(index,id_ordinals=MappingProxyType({**dict(index.id_ordinals),'z':(1,)})),
            replace(index,id_ordinals=MappingProxyType({**dict(index.id_ordinals),'z':(3,1)})),
            replace(index,records=(replace(index.records[0],sequence_lines=('a','c','')),)+index.records[1:]),
            replace(index,records=(replace(index.records[0],header_line=3),)+index.records[1:])]
        for corrupted in corruptions:
            with self.subTest(kind=type(corrupted).__name__),patch.object(m,'parse_fasta',return_value=corrupted):
                self.rejected(lambda:verify(src,candidate))

    def test_same_content_new_source_and_project_are_separate_not_owner_evidence(self):
        src=source();a=build(src)
        b=build(source(source_id=OTHER),expected_source_id=OTHER)
        c=build(source(project_id=OTHER),expected_project_id=OTHER)
        self.assertEqual(len({a.index_version_id,b.index_version_id,c.index_version_id}),3)
        ns=uuid5(NAMESPACE_URL,'urn:cyomics:fasta-source-index:v1')
        self.assertEqual(a.index_version_id,str(uuid5(ns,':'.join((P,S,sha(RAW),
            'fasta_ascii_identity_index_v0.1',sha(m.configuration_json().encode()))))))
        self.rejected(lambda:verify(src,a,expected_project_id=OTHER),'fasta_source_integrity_failed')
        self.rejected(lambda:verify(src,a,expected_source_id=OTHER),'fasta_source_integrity_failed')
        self.rejected(lambda:verify(src,a,expected_index_version_id=OTHER))

    def test_late_cancelled_iterator_and_final_fingerprint_boundary(self):
        def broken():
            yield b'>x\nAA\n'
            raise asyncio.CancelledError('SYNTHETIC_PRIVATE_CANCEL')
        with patch.object(m,'_chunks',return_value=broken()):
            self.rejected(lambda:build(),'fasta_intake_unavailable')
        state={'late':False};original=m._fingerprint
        def fingerprint(*args):
            result=original(*args);state['late']=True;return result
        def check():
            if state['late']:raise RuntimeError('SYNTHETIC_PRIVATE_LATE')
        with patch.object(m,'_fingerprint',side_effect=fingerprint):
            self.rejected(lambda:build(control=m.ParseControl(check,lambda:0)),'fasta_intake_timeout')

    def test_final_cpu_and_clock_regression_no_success(self):
        state={'late':False};original=m._fingerprint
        def fingerprint(*args):
            value=original(*args);state['late']=True;return value
        with patch.object(m,'_fingerprint',side_effect=fingerprint):
            self.rejected(lambda:build(control=m.ParseControl(lambda:None,
                lambda:8.000001 if state['late'] else 0)),'fasta_intake_timeout')
        for value in (True,False,None,float('nan'),float('inf'),-1,10**1000):
            self.rejected(lambda value=value:build(control=m.ParseControl(lambda:None,lambda:value)))

    def test_exact_config_not_client_selected_or_mutably_shared(self):
        config=m.configuration();config['limits']['max_bytes']=1
        config['parser_release']['source_pins']['fasta_index.py']['sha256']='0'*64
        self.rejected(lambda:build(config_json=cj(config)),'fasta_parser_release_mismatch')
        self.assertEqual(m.configuration()['limits']['max_bytes'],26214400)
        for key in ('config_sha256','config_json','index_contract'):
            snapshot=m.candidate_snapshot(build())
            snapshot[key]='SYNTHETIC_PRIVATE_CONFIG'
            self.rejected(lambda snapshot=snapshot:m.decode_candidate(snapshot))

    def test_json_duplicate_keys_nested_and_cap_and_no_repr_leak(self):
        candidate=build();raw=cj(m.candidate_snapshot(candidate)).encode()
        duplicate=raw[:-1]+b',"status":"indexed"}'
        self.rejected(lambda:m.candidate_from_json(duplicate))
        snapshot=m.candidate_snapshot(candidate)
        summary=candidate.summary_json
        snapshot['summary_json']=summary[:-1]+',"record_count":8}'
        snapshot['summary_sha256']=sha(snapshot['summary_json'].encode())
        self.rejected(lambda:m.decode_candidate(snapshot))
        self.rejected(lambda:m.candidate_from_json(raw+b' '*(16385-len(raw))))
        for value in (source(),candidate,verify(source(),candidate)):
            self.assertNotIn('z header',repr(value));self.assertNotIn('A-C',repr(value))

    def test_every_numeric_and_boolean_dto_field_refuses_unsafe_values(self):
        dto=m.index_dto(build(),STAMP)
        for key in dto['summary']:
            for value in (True,False,0.25,9007199254740992,float('inf')):
                bad={**dto,'summary':{**dto['summary'],key:value}}
                self.rejected(lambda bad=bad:m.validate_index_dto(bad))
        for key in ('index_materialized','source_complete'):
            for value in (0,1,'true',None):
                self.rejected(lambda key=key,value=value:m.validate_index_dto({**dto,key:value}))
        for key in ('source_id','project_id','index_version_id'):
            for value in (dto[key].upper(),dto[key]+' ',True,None):
                if value==dto[key]:continue
                self.rejected(lambda key=key,value=value:m.validate_index_dto({**dto,key:value}))

    def test_scalar_filename_and_timestamp_edges_and_detached_dto(self):
        raw=b'>x\nAA\n'
        good=source(raw,filename='中.fa')
        candidate=build(good);dto=m.index_dto(candidate,STAMP)
        dto['summary']['record_count']=999
        self.assertEqual(json.loads(candidate.summary_json)['record_count'],1)
        for name in ('a\x7f.fa','a\x85.fa','a\udfff.fa','a/b.fa','a\\b.fa','x.fa\n'):
            self.rejected(lambda name=name:source(raw,filename=name),'invalid_fasta_request')
        for stamp in ('2026-11-18T01:02:03Z','2026-11-18T01:02:03.000004+00:00',
                '0000-01-01T00:00:00.000000Z','2026-11-18T01:02:60.000004Z'):
            self.rejected(lambda stamp=stamp:m.index_dto(candidate,stamp))
        with self.assertRaises(FrozenInstanceError):candidate.source_complete=False

if __name__=='__main__':unittest.main(verbosity=2)
