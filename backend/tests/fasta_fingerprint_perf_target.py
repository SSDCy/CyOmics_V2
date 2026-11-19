"""Finite new fingerprint-only target: frozen AST oracle and real CPU clock.

No core/old suites, database, HTTP, golden file or dependency substitutes.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import sys
import time
import types

args=argparse.ArgumentParser()
args.add_argument('--frozen-backend',required=True)
opts=args.parse_args()
sys.dont_write_bytecode=True
frozen=Path(opts.frozen_backend).resolve()
sys.path.insert(0,str(frozen))
import cyomics_projects
candidate_backend=Path(__file__).resolve().parents[1]
cyomics_projects.__path__.insert(0,str(candidate_backend/'cyomics_projects'))
from cyomics_projects import fasta_intake_contract as model
from cyomics_projects.fasta_index import parse_fasta

original_path=frozen/'cyomics_projects/fasta_intake_contract.py'
original=original_path.read_text(encoding='utf8')
patched=(candidate_backend/'cyomics_projects/fasta_intake_contract.py').read_text(encoding='utf8')
old_ast=ast.parse(original)
new_ast=ast.parse(patched)
old_node=next(n for n in old_ast.body if isinstance(n,ast.FunctionDef) and n.name=='_fingerprint')
new_node=next(n for n in new_ast.body if isinstance(n,ast.FunctionDef) and n.name=='_fingerprint')
# Enforce the narrow change envelope, including unchanged config/CPU policy.
assert ast.dump(old_node)!=ast.dump(new_node)
assert ast.dump(ast.Module(body=[n for n in old_ast.body if n is not old_node],type_ignores=[]))==ast.dump(ast.Module(body=[n for n in new_ast.body if n is not new_node],type_ignores=[]))
namespace=dict(vars(model))
exec(compile(ast.Module(body=[old_node],type_ignores=[]),str(original_path),'exec'),namespace)
oracle=namespace['_fingerprint']
assert model.ParseControl().max_cpu_seconds==8.0
assert model.ParseControl().cpu_clock is time.thread_time

class Capture:
    def __init__(self):
        self.digest=hashlib.sha256();self.feed=bytearray();self.sizes=[]
    def update(self,chunk):
        assert type(chunk) is bytes
        self.sizes.append(len(chunk));self.feed.extend(chunk);self.digest.update(chunk)
    def hexdigest(self):return self.digest.hexdigest()

def run(function,index,checkpoint=None):
    capture=Capture()
    def check():
        if checkpoint is not None:checkpoint(capture)
    control=model.ParseControl(checkpoint=check)
    assert control.cpu_clock is time.thread_time and control.max_cpu_seconds==8.0
    budget=model._Budget(control)
    old_hashlib=function.__globals__['hashlib']
    function.__globals__['hashlib']=types.SimpleNamespace(sha256=lambda:capture)
    try:
        return function(index,budget),capture
    finally:
        function.__globals__['hashlib']=old_hashlib

fixtures=[
    ('escapes_duplicates_empty_invalid',b'>dup quoted " slash\\ tab\t control\x01\nacD\n>dup invalid\nA*\n>empty\n'),
    ('max_sequence_length',b'>long sequence\n'+b'A'*1000000+b'\n'),
    ('max_header_line_length',b'>long '+b'"\t\\\x01'*249998+b'xx\nACD\n'),
    ('multi_sequence_lines_and_id_order',b'>z\r\nAC\r\n\r\nD\r\n>a\r\nBJOuxZ\r\n>z again\r\nA-\r\n'),
]
results=[]
long_index=None
for name,raw in fixtures:
    index=parse_fasta(raw)
    # Exercise unchanged complete-index validation before fingerprinting.
    model._validate_index(index,model._Budget(model.ParseControl(checkpoint=lambda:None)))
    start=time.thread_time()
    expected,old_feed=run(oracle,index)
    oracle_cpu=time.thread_time()-start
    start=time.thread_time()
    actual,new_feed=run(model._fingerprint,index)
    new_cpu=time.thread_time()-start
    assert actual==expected and new_feed.feed==old_feed.feed
    assert max(new_feed.sizes)<=65536
    assert new_feed.feed.endswith(b'\n')
    assert len(new_feed.feed.splitlines())==1+len(index.records)+len(index.id_ordinals)
    assert new_cpu<8 and oracle_cpu<8
    results.append({'case':name,'records':len(index.records),'sha256':actual,
        'encoded_bytes':len(new_feed.feed),'max_hash_chunk':max(new_feed.sizes),
        'old_hash_updates':len(old_feed.sizes),'new_hash_updates':len(new_feed.sizes),
        'old_cpu_seconds':round(oracle_cpu,6),'new_cpu_seconds':round(new_cpu,6)})
    if name=='max_sequence_length':long_index=index

cancelled={}
def reject_start(capture):
    cancelled['start']=capture
    raise RuntimeError('cancelled by trusted checkpoint')
def reject_after_chunk(capture):
    if 65536 in capture.sizes:
        cancelled['chunk']=capture
        raise RuntimeError('cancelled by trusted checkpoint')
for stage,check in (('start',reject_start),('chunk',reject_after_chunk)):
    try:run(model._fingerprint,long_index,check)
    except model.IntakeError as error:assert error.code=='fasta_intake_timeout'
    else:raise AssertionError('trusted cancellation must fail closed')
assert not cancelled['start'].sizes
assert max(cancelled['chunk'].sizes)==65536
assert sum(cancelled['chunk'].sizes)<next(r['encoded_bytes'] for r in results if r['case']=='max_sequence_length')

print(json.dumps({'task':'S1-FASTA-FINGERPRINT-PERF-001','status':'PASS',
    'oracle':'AST-extracted unchanged frozen _fingerprint; actual hash feeds compared byte for byte',
    'change_envelope':'only _fingerprint AST changed',
    'clock':'real time.thread_time; original 8 CPU second cap',
    'cancellation':['before any hash output','after first 64KiB record chunk'],
    'fixtures':results,'golden_run':False,'core_suite_run':False,'db_http_run':False}))
