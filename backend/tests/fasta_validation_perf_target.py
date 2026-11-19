"""One new validation-only target. Frozen AST oracle, real clock/8 CPU cap.

No parser suite, core suite, previous perf target, Golden, DB or server access.
"""
import argparse
import ast
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import string
import sys
import time
from types import MappingProxyType

args=argparse.ArgumentParser()
args.add_argument('--frozen-backend',required=True)
args.add_argument('--prior-perf-model',required=True)
opts=args.parse_args()
sys.dont_write_bytecode=True
frozen=Path(opts.frozen_backend).resolve()
sys.path.insert(0,str(frozen))
import cyomics_projects
candidate_backend=Path(__file__).resolve().parents[1]
cyomics_projects.__path__.insert(0,str(candidate_backend/'cyomics_projects'))
from cyomics_projects import fasta_intake_contract as model
from cyomics_projects.fasta_index import FastaIndex,Record
from cyomics_projects.sequence.vendor_domain_r1 import STANDARD_AA,EXTENDED_AA,normalize_sequence

assert STANDARD_AA|EXTENDED_AA==set(string.ascii_uppercase)
assert len(STANDARD_AA)==20 and len(EXTENDED_AA)==6 and not STANDARD_AA&EXTENDED_AA
model_path=candidate_backend/'cyomics_projects/fasta_intake_contract.py'
frozen_path=frozen/'cyomics_projects/fasta_intake_contract.py'
prior_path=Path(opts.prior_perf_model)
original=frozen_path.read_text(encoding='utf8')
prior=prior_path.read_text(encoding='utf8')
patched=model_path.read_text(encoding='utf8')
old_ast=ast.parse(original);prior_ast=ast.parse(prior);new_ast=ast.parse(patched)
def function(tree,name):return next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
old_validate=function(old_ast,'_validate_index')
new_validate=function(new_ast,'_validate_index')
assert ast.dump(old_validate)!=ast.dump(new_validate)
# Byte-level envelope: PERF001 stays exactly unchanged outside this small block.
old_block='''        canonical = None
        try:
            canonical = normalize_sequence(raw)
        except ValueError:
            pass
'''
new_block='''        # Fixed vendor STANDARD20 + BJOUXZ is exactly all 26 ASCII letters.
        # The checks above already prove exact str, ASCII lines and raw equality.
        canonical = raw.upper() if raw and raw.isascii() and raw.isalpha() else None
'''
assert prior.count(old_block)==1 and patched==prior.replace(old_block,new_block)
assert ast.dump(function(prior_ast,'_fingerprint'))==ast.dump(function(new_ast,'_fingerprint'))
def without(tree,names):
    return ast.dump(ast.Module(body=[n for n in tree.body if not isinstance(n,ast.FunctionDef) or n.name not in names],type_ignores=[]))
assert without(old_ast,{'_validate_index','_fingerprint'})==without(new_ast,{'_validate_index','_fingerprint'})
namespace=dict(vars(model))
exec(compile(ast.Module(body=[old_validate],type_ignores=[]),str(frozen_path),'exec'),namespace)
oracle=namespace['_validate_index']

def digest(raw):return hashlib.sha256(raw).hexdigest()
def make_index(raw_values):
    records=[];mapping={};previous=0;source=bytearray()
    for ordinal,raw in enumerate(raw_values,1):
        identifier='dup' if ordinal in (1,2) else 'id'+str(ordinal)
        header=identifier+' full header'
        try:canonical=normalize_sequence(raw)
        except ValueError:canonical=None
        issue=None if canonical is not None else ('empty_sequence' if not raw else
            'sequence_whitespace' if any(ch.isspace() for ch in raw) else
            'stop_or_gap' if '*' in raw or '-' in raw else 'illegal_residue')
        previous+=1
        records.append(Record(ordinal,previous,header,identifier,(raw,),raw,
            digest(raw.encode('utf8')),canonical,
            digest(canonical.encode('ascii')) if canonical is not None else None,
            'valid_canonical_residues' if canonical is not None else 'invalid_no_canonical_sequence_identity',issue))
        previous+=1
        mapping.setdefault(identifier,[]).append(ordinal)
        source.extend(('>'+header+'\n'+raw+'\n').encode('utf8'))
    return FastaIndex(len(source),digest(source),tuple(records),
        MappingProxyType({k:tuple(v) for k,v in mapping.items()}),
        sum(not raw for raw in raw_values),previous)

def result(function,index,checkpoint=lambda:None):
    control=model.ParseControl(checkpoint=checkpoint)
    assert control.cpu_clock is time.thread_time and control.max_cpu_seconds==8.0
    try:
        function(index,model._Budget(control))
        return 'accepted'
    except model.IntakeError as error:return error.code

cases=0;accepted=0;rejected=0
for code in range(128):
    raw=chr(code)
    try:expected=normalize_sequence(raw)
    except ValueError:expected=None
    assert (raw.upper() if raw and raw.isascii() and raw.isalpha() else None)==expected
    index=make_index([raw])
    old=result(oracle,index);new=result(model._validate_index,index)
    assert old==new
    assert new==('fasta_index_persisted_invalid' if code in (10,13) else 'accepted')
    cases+=1;accepted+=new=='accepted';rejected+=new!='accepted'

mixed=['aBcDeFgHiJkLmNoPqRsTuVwXyZ','BJOUXZbjouxz','','A C','A\tC','A\vC','A\fC','A*','A-','123','A1','?','é']
for raw in mixed:
    old=result(oracle,make_index([raw]));new=result(model._validate_index,make_index([raw]))
    assert old==new
    assert new==('fasta_index_persisted_invalid' if not raw.isascii() else 'accepted')
    cases+=1;accepted+=new=='accepted';rejected+=new!='accepted'

base=make_index(['acD','BJouxz','A*',''])
assert result(oracle,base)==result(model._validate_index,base)=='accepted'
def change_record(ordinal,**values):
    records=list(base.records);records[ordinal]=replace(records[ordinal],**values)
    return replace(base,records=tuple(records))
malicious={
    'canonical_mismatch':change_record(0,canonical_sequence='XYZ'),
    'canonical_missing':change_record(0,canonical_sequence=None),
    'canonical_type':change_record(0,canonical_sequence=123),
    'invalid_canonical_forged':change_record(2,canonical_sequence='A'),
    'empty_canonical_forged':change_record(3,canonical_sequence=''),
    'raw_sha':change_record(0,raw_sequence_sha256='0'*64),
    'canonical_sha':change_record(0,sequence_sha256='0'*64),
    'invalid_canonical_sha_forged':change_record(2,sequence_sha256='0'*64),
    'normalization_status':change_record(0,normalization_status='invalid_no_canonical_sequence_identity'),
    'canonical_issue':change_record(2,canonical_issue='empty_sequence'),
    'raw_vs_lines':change_record(0,sequence_raw='XYZ'),
    'duplicate_map':replace(base,id_ordinals=MappingProxyType({'dup':(2,1),'id3':(3,),'id4':(4,)})),
}
for name,index in malicious.items():
    assert result(oracle,index)==result(model._validate_index,index)=='fasta_index_persisted_invalid',name

cancellations=[]
for function_under_test in (oracle,model._validate_index):
    count=[0]
    def checkpoint():
        count[0]+=1
        if count[0]==2:raise RuntimeError('trusted cancellation')
    assert result(function_under_test,base,checkpoint)=='fasta_intake_timeout'
    assert count[0]==2
    cancellations.append(count[0])

print(json.dumps({'task':'S1-FASTA-VALIDATION-PERF-002','status':'PASS',
    'fixed_vendor_alphabet':'STANDARD20 union BJOUXZ6 equals all 26 ASCII letters',
    'oracle':'AST-extracted original frozen _validate_index',
    'single_ascii_cases':128,'mixed_cases':len(mixed),'legal_index_accepts':accepted,
    'structural_rejects':rejected,'malicious_rejects':list(malicious),
    'duplicate_and_invalid_record_fixture':'both validators accept unchanged complete index',
    'change_envelope':'exact bytes outside canonical block equal PERF001; AST outside _validate_index/_fingerprint equals frozen; PERF001 fingerprint unchanged',
    'budget':'real time.thread_time, original 8 CPU seconds; both checkpoint cancellations rejected at call 2',
    'golden_run':False,'old_targets_run':False,'db_http_server_run':False}))
