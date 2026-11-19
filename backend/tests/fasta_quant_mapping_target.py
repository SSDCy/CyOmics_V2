"""One finite new association target; original parsers and real ParseControl."""
import argparse
import csv
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import sys

args=argparse.ArgumentParser();args.add_argument('--frozen-backend',required=True)
opts=args.parse_args();sys.dont_write_bytecode=True
sys.path.insert(0,opts.frozen_backend)
import cyomics_projects
cyomics_projects.__path__.insert(0,str(Path(__file__).resolve().parents[1]/'cyomics_projects'))
from cyomics_projects import fasta_quant_mapping as mapping
from cyomics_projects import fasta_intake_contract as model
from cyomics_projects.import_report import SourceRef
from cyomics_projects.protein_groups_parser import parse_protein_groups

P='00000000-0000-4000-8000-000000000001'
F='00000000-0000-4000-8000-000000000002'
D='00000000-0000-4000-8000-000000000003'
S='00000000-0000-4000-8000-000000000004'
R='00000000-0000-4000-8000-000000000005'
B='00000000-0000-4000-8000-000000000006'
def sha(raw):return hashlib.sha256(raw).hexdigest()
def control():return model.ParseControl(checkpoint=lambda:None)
text=io.StringIO(newline='')
writer=csv.writer(text,delimiter='\t',lineterminator='\n')
writer.writerow(['Protein IDs','LFQ intensity X'])
ids=[' dup ;missing;; dup ;','Case','\u00a0A\u3000; B',' ; \t ;','',
    'sp|P1|NAME','bad','A; A;case','A','a']
for identifier in ids:writer.writerow([identifier,'0'])
raw=text.getvalue().encode()
source=SourceRef(P,F,F,len(raw),sha(raw))
outcome=parse_protein_groups(source,[raw]);assert outcome.ok
parsed=outcome.parsed
binding=mapping.QuantVersionBinding(P,D,F,F,len(raw),sha(raw),'protein_groups_txt_v1',
    sha(b'fixture config'),sha(b'fixture saved report'),len(ids),2,outcome.report.member_occurrences)
fraw=b'>dup valid\nACD\n>dup invalid\nA*\n>A\nAC\n>B\n\n>case\nBJouxz\n>bad\n?\n>P1\nA\n'
dto=dict(source_id=S,project_id=P,request_id=R,filename='mapping.fa',format='protein_fasta',
    bytes=len(fraw),sha256=sha(fraw),status='stored',createdAt='2026-11-20T00:00:00.000000Z')
stored=model.source_from_json(json.dumps(dto).encode(),fraw)
candidate=model.build_candidate(stored,expected_project_id=P,expected_source_id=S,
    config_json=model.configuration_json(),control=control())
verified=model.rebuild_and_verify(stored,candidate,expected_project_id=P,expected_source_id=S,
    expected_index_version_id=candidate.index_version_id,control=control())
result=mapping.map_quant_to_fasta(parsed,verified,binding,control=control())
value=result.snapshot();summary=value['summary']
assert {k:summary[k] for k in ('complete','partial','unmatched','empty')}==dict(complete=4,partial=1,unmatched=3,empty=2)
assert summary['row_count']==10 and summary['member_occurrences']==13
assert summary['matched_member_occurrences']==9 and summary['unmatched_member_occurrences']==4
assert summary['duplicate_id_member_occurrences']==2
assert summary['invalid_canonical_match_occurrences']==4
assert summary['unique_member_tokens']==9 and summary['matched_unique_member_tokens']==5
assert [row['protein_ids_raw'] for row in value['rows']]==ids
first=value['rows'][0]
assert [m['segment_ordinal'] for m in first['members']]==[0,1,3]
assert first['members'][0]['fasta_record_ordinals']==[1,2]
assert first['members'][2]['fasta_record_ordinals']==[1,2]
assert value['fasta_records']['2']['canonical_sequence_sha256'] is None
assert value['fasta_records']['2']['raw_sequence_sha256']==sha(b'A*')
assert value['rows'][2]['status']=='complete' and value['rows'][2]['has_invalid_canonical_match']
assert value['rows'][1]['status']==value['rows'][5]['status']==value['rows'][9]['status']=='unmatched'
assert value['rows'][4]['status']=='empty' and value['rows'][4]['members']==[]
assert not value['annotation_ready'] and not value['owner_authorized'] and not value['published']
again=mapping.map_quant_to_fasta(parsed,verified,binding,control=control())
assert again==result and sha(result.document)==result.sha256

rejections=[]
def fails(name,call,code=None):
    try:call()
    except (mapping.MappingError,model.IntakeError) as error:
        if code is not None:assert str(error)==code or getattr(error,'code',None)==code
        rejections.append(name);return
    raise AssertionError(name+' should reject')
fails('pure DTO',lambda:mapping.map_quant_to_fasta({},verified,binding,control=control()))
fails('client verified bool',lambda:mapping.map_quant_to_fasta(parsed,True,binding,control=control()))
fails('truncated parsed rows',lambda:mapping.map_quant_to_fasta(replace(parsed,rows=parsed.rows[:9]),verified,binding,control=control()),'mapping_incomplete_quantification')
fails('raw SHA mismatch',lambda:mapping.map_quant_to_fasta(parsed,verified,replace(binding,raw_sha256='0'*64),control=control()))
fails('cross project',lambda:mapping.map_quant_to_fasta(replace(parsed,source=replace(source,project_id=B)),verified,replace(binding,project_id=B),control=control()),'mapping_project_mismatch')
fails('truncated index',lambda:mapping.map_quant_to_fasta(parsed,replace(verified,index=replace(verified.index,records=verified.index.records[:2])),binding,control=control()))
original=mapping.MAX_MATCH_REFERENCES
try:
    mapping.MAX_MATCH_REFERENCES=1 # tighten solely to exercise whole-result failure
    fails('match fanout resource bound',lambda:mapping.map_quant_to_fasta(parsed,verified,binding,control=control()),'mapping_match_reference_limit')
finally:mapping.MAX_MATCH_REFERENCES=original
original=mapping.MAX_OUTPUT_BYTES
try:
    mapping.MAX_OUTPUT_BYTES=128
    fails('output byte resource bound',lambda:mapping.map_quant_to_fasta(parsed,verified,binding,control=control()),'mapping_output_byte_limit')
finally:mapping.MAX_OUTPUT_BYTES=original
def cancelled():raise RuntimeError('trusted cancellation')
fails('real checkpoint cancellation',lambda:mapping.map_quant_to_fasta(parsed,verified,binding,control=model.ParseControl(checkpoint=cancelled)),'fasta_intake_timeout')
print(json.dumps({'task':'S1-FASTA-QUANT-MAPPING-CORE-001','status':'PASS','rows':10,
    'summary':summary,'mapping_sha256':result.sha256,'binding_sha256':result.binding_sha256,
    'checks':['all duplicate record ordinals and raw/canonical evidence preserved','member segment ordinals including gaps','case and aliases exact','empty and partial groups retained','invalid canonical does not erase ID match','stable complete result bytes'],
    'rejections':rejections,'clock':'original real ParseControl, 8 CPU seconds maximum',
    'actual_DB_HTTP_or_authority_acceptance':False}))
