"""One actual R target over the complete existing saved-log2 fixture.

Computes real fixture/output byte pins; no database, worker or publication claim.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

args=argparse.ArgumentParser()
args.add_argument('--frozen-backend',required=True)
args.add_argument('--rscript',required=True)
args.add_argument('--evidence',required=True)
opts=args.parse_args()
root=Path(__file__).resolve().parents[1]
fixture=Path(opts.frozen_backend)/'tests/fixtures/log2_matrix/fixtures/actual/lfq'
evidence=Path(opts.evidence)
evidence.mkdir(parents=True,exist_ok=False)
names=['filter-log.tsv','row-map.tsv','summary.tsv','raw-retained.tsv','working-matrix.tsv',
    'original-blank-mask.tsv','original-zero-mask.tsv','working-NA-mask.tsv','imputed-mask.tsv',
    'parameters.json','R-session-info.txt']
def pin(path):
    raw=path.read_bytes()
    return {'name':path.name,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
pins=[pin(fixture/name) for name in sorted(names)]
manifest=json.dumps(pins,sort_keys=True,separators=(',',':')).encode()
(evidence/'input-artifacts.json').write_bytes(manifest)
(evidence/'input-pins.tsv').write_text('name\tbytes\tsha256\n'+''.join(
    f"{p['name']}\t{p['bytes']}\t{p['sha256']}\n" for p in pins),encoding='utf8')
parameters=json.loads((fixture/'parameters.json').read_bytes())
assert all(parameters[k]==v for k,v in dict(output_scale='log2',log2='positive_only',
    normalization='none',imputation='none',zero_policy='to_NA',blank_policy='to_NA',pseudocount=0).items())
output=evidence/'r-output'
run=subprocess.run([opts.rscript,'--vanilla',str(Path(__file__).with_suffix('.R')),
    str(root/'cyomics_projects/r_normalization_median/median_core.R'),str(fixture),
    str(evidence/'input-pins.tsv'),hashlib.sha256(manifest).hexdigest(),str(output)],
    capture_output=True,timeout=60)
(evidence/'R-stdout.txt').write_bytes(run.stdout)
(evidence/'R-stderr.txt').write_bytes(run.stderr)
print(run.stdout.decode('utf8',errors='replace'),end='')
if run.stderr:print(run.stderr.decode('utf8',errors='replace'),file=sys.stderr,end='')
if run.returncode:raise SystemExit(run.returncode)
assert b'NORMALIZATION_MEDIAN_CORE_PASS' in run.stdout
assert [pin(fixture/name) for name in sorted(names)]==pins
output_names=['working-matrix.tsv','normalization-factors.tsv','original_blank-mask.tsv',
    'original_zero-mask.tsv','working_NA-mask.tsv','imputed-mask.tsv','core-result.rds',
    'parameters.R.txt','R-session-info.txt']
receipt={'status':'ACTUAL_R_TARGET_PASS','Rscript':opts.rscript,'R_command_mode':'--vanilla',
    'input_manifest_sha256':hashlib.sha256(manifest).hexdigest(),'input_artifacts':pins,
    'output_artifacts':[pin(output/name) for name in output_names],
    'source_only_core':True,'input_fixture_not_real_private_version':True,
    'actual_A_runner_compatibility_verified':False,'HTTP_DB_SQL_publication':False}
(evidence/'target-receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf8')
print(json.dumps({'status':receipt['status'],'input_artifacts':len(pins),
    'output_artifacts':len(output_names),'A_runtime_acceptance':False}))
