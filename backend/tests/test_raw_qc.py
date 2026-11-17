"""Portable synthetic behavior checks. Invoke with --rscript and --script paths."""
import argparse,csv,pathlib,subprocess,tempfile

def main():
 p=argparse.ArgumentParser();p.add_argument('--rscript',required=True);p.add_argument('--script',required=True);p.add_argument('--temp-root',required=True);a=p.parse_args()
 cases={
  'valid':('Protein IDs\tIntensity A\tIntensity B\tLFQ intensity A\nP1;P2\t0\t\t5\nP3\t0.0\t2\t0\nP4\t1e2\t3\t\n',True),
  'duplicate':('Protein IDs\tIntensity A\tIntensity A\nP1\t1\t2\n',False),
  'invalid':('Protein IDs\tIntensity A\nP1\tNA\n',False),
  'negative':('Protein IDs\tIntensity A\nP1\t-1\n',False),
  'nonfinite':('Protein IDs\tIntensity A\nP1\tInf\n',False),
  'overflow':('Protein IDs\tIntensity A\nP1\t1e400\n',False),
  'underflow':('Protein IDs\tIntensity A\nP1\t1e-400\n',False),
  'ragged':('Protein IDs\tIntensity A\nP1\t1\t2\n',False)}
 cases.update({'bad-utf8':(b'Protein IDs\tIntensity A\nP1\t1\xff\n',False),
  'truncated-utf8':(b'Protein IDs\tIntensity A\nP1\t1\xe4',False),
  'truncated-row':(b'Protein IDs\tIntensity A\tIntensity B\nP1\t1',False),
  'nul':(b'Protein IDs\tIntensity A\nP1\t1\x00\n',False),
  'no-final-newline':('Protein IDs\tIntensity A\nP1;P2\t0',True)})
 with tempfile.TemporaryDirectory(prefix='raw-qc-synthetic-',dir=a.temp_root) as work:
  root=pathlib.Path(work)
  for name,(body,ok) in cases.items():
   source=root/(name+'.txt');source.write_bytes(body.encode('utf-8') if isinstance(body,str) else body);out=root/name
   d=subprocess.run([a.rscript,'--vanilla',a.script,str(source),'Intensity',str(out)],capture_output=True,text=True,timeout=10)
   assert (d.returncode==0)==ok,(name,d.stderr)
   if not ok:assert not out.exists()
  def table(name):
   with (root/'valid'/name).open(newline='',encoding='utf-8') as f:return list(csv.DictReader(f,delimiter='\t'))
  raw=table('raw-matrix.tsv');numeric=table('numeric-matrix.tsv');mask=table('blank-mask.tsv');summary=table('summary.tsv')[0]
  assert len(raw)==3 and raw[0]['protein_ids_raw']=='P1;P2' and raw[0]['Intensity B']==''
  assert numeric[0]['Intensity A']=='0' and numeric[0]['Intensity B']=='NA' and numeric[2]['Intensity A']=='100'
  assert mask[0]['Intensity B']=='TRUE'
  assert (summary['zero'],summary['blank'],summary['positive'],summary['exact_zero_literal'])==('2','1','3','1')
  for family,name,ok in [('LFQ intensity','lfq',True),('Demo','unsupported',False)]:
   d=subprocess.run([a.rscript,'--vanilla',a.script,str(root/'valid.txt'),family,str(root/name)],capture_output=True,text=True,timeout=10)
   assert (d.returncode==0)==ok,(name,d.stderr)
   if ok:
    with (root/name/'summary.tsv').open(newline='') as f:assert next(csv.DictReader(f,delimiter='\t'))['selected_columns']=='1'
 print('15 synthetic raw-QC cases passed; no real dataset used by this test.')
if __name__=='__main__':main()
