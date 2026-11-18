"""Synthetic byte fixtures and adversarial records; no R/DB/FS execution."""
import csv,dataclasses,hashlib,importlib.util,io,itertools,json,pathlib,sys,types,unittest
from unittest.mock import patch
H=pathlib.Path(__file__).resolve().parent/'fixtures/log2_matrix'
spec=importlib.util.spec_from_file_location('validator',pathlib.Path(__file__).resolve().parents[1]/'cyomics_projects/log2_matrix_validator.py');v=importlib.util.module_from_spec(spec);sys.modules[spec.name]=v;spec.loader.exec_module(v)
spec=importlib.util.spec_from_file_location('independent_literal_oracle',H/'fixtures/oracle_reference.py');o=importlib.util.module_from_spec(spec);spec.loader.exec_module(o)
RAW=(H/'fixtures/proteinGroups.txt').read_bytes();PARAM=(H/'fixtures/parameters.json').read_bytes()
SESSION=b'R version 4.6.1 (SYNTHETIC_TEXT_NOT_EXECUTION)\n\nattached base packages:\n[1] stats graphics grDevices utils datasets methods base\n\nloaded via a namespace (and not attached):\n[1] compiler_4.6.1\n'
def tsv(rows):
 text=io.StringIO(newline='');csv.writer(text,delimiter='\t',lineterminator='\n',quoting=csv.QUOTE_ALL).writerows(rows);return text.getvalue().encode()
def big(kind='intensity'):
 family='Intensity' if kind=='intensity' else 'LFQ intensity';selected=(H/('fixtures/selected-'+kind+'.tsv')).read_bytes()
 tables,proof=o.expected(RAW,selected,family)
 outputs={name:tsv(rows) for name,rows in tables.items()};outputs.update({'parameters.json':PARAM,'R-session-info.txt':SESSION})
 return RAW,selected,family,(True,True,True),outputs
def mutate_output(case,name,row,column,value):
 parts=case[-1];table=list(csv.reader(io.StringIO(parts[name].decode()),delimiter='\t'));table[row][column]=value;parts[name]=tsv(table)
def run(case,check=lambda:None):return v.validate(*case,check=check)

SUMMARY_FIELDS=['status','input_rows','input_columns','quant_family','selected_columns','input_selected_cells','kept_rows','excluded_rows',
 'excluded_reverse','excluded_contaminant','excluded_site_only','overlap_excluded_rows','source_blank_cells','source_zero_cells','source_positive_cells',
 'retained_selected_cells','retained_blank_cells','retained_zero_cells','retained_positive_cells','working_NA_cells','imputed_cells',
 'reverse_enabled','contaminant_enabled','site_only_enabled','zero_policy','blank_policy','log2','pseudocount','normalization','imputation','analysis_ready']
def small(first='1',working='0',first_id='P'):
 # Completely literal two-row, one-column hand case, all flags disabled.
 raw=('Protein IDs\tReverse\tIntensity a\n'+first_id+'\t+\t'+first+'\nQ\t\t2\n').encode()
 selected=b'column_ordinal\tname_raw\tquant_family\tsample_label_raw\n2\tIntensity a\tIntensity\ta\n'
 prefix=['record_number','protein_ids_raw'];header=prefix+['Intensity a'];outputs={}
 outputs['raw-retained.tsv']=tsv([header,['1',first_id,first],['2','Q','2']])
 outputs['working-matrix.tsv']=tsv([header,['1',first_id,working],['2','Q','1']])
 for name in ('original-blank-mask.tsv','original-zero-mask.tsv','working-NA-mask.tsv','imputed-mask.tsv'):
  outputs[name]=tsv([header,['1',first_id,'FALSE'],['2','Q','FALSE']])
 outputs['row-map.tsv']=tsv([['source_record_number','protein_ids_raw','kept','work_row_ordinal'],['1',first_id,'TRUE','1'],['2','Q','TRUE','2']])
 outputs['filter-log.tsv']=tsv([prefix+['excluded_reverse','excluded_contaminant','excluded_site_only','excluded','reason_codes'],['1',first_id,'FALSE','FALSE','FALSE','FALSE',''],['2','Q','FALSE','FALSE','FALSE','FALSE','']])
 outputs['summary.tsv']=tsv([SUMMARY_FIELDS,['complete_positive_log2','2','3','Intensity','1','2','2','0','0','0','0','0','0','0','2','2','0','0','2','0','0','FALSE','FALSE','FALSE','to_NA','to_NA','positive_only','0','none','none','FALSE']])
 outputs['parameters.json']=b'{"blank_policy":"to_NA","filter_flags":{"contaminant":false,"reverse":false,"site_only":false},"imputation":"none","input_scale":"raw_nonnegative","log2":"positive_only","normalization":"none","output_scale":"log2","preset":"positive_log2_zero_na_no_impute_v1","pseudocount":0,"seed":null,"steps":["flags_filter","zero_and_blank_to_NA","log2_positive","normalization_none","imputation_none"],"zero_policy":"to_NA"}'
 outputs['R-session-info.txt']=SESSION
 return raw,selected,'Intensity',(False,False,False),outputs

class Tests(unittest.TestCase):
 def failure(self,case,code=None,check=lambda:None):
  with self.assertRaises(v.ValidationFailure) as caught:run(case,check)
  if code:self.assertEqual(caught.exception.code,code)
  self.assertNotIn('PRIVATE',str(caught.exception))
 def test_full_literal_families_preserve_duplicate_allNA_and_hash_actual_bytes(self):
  for kind,width in [('intensity',3),('lfq',2)]:
   case=big(kind);result=run(case)
   self.assertEqual((result.source_rows,result.retained_rows,result.selected_columns),(8,4,width))
   self.assertEqual(result.source_sha256,hashlib.sha256(case[0]).hexdigest());self.assertEqual(result.selected_sha256,hashlib.sha256(case[1]).hexdigest())
   self.assertEqual(tuple(a.name for a in result.artifacts),v.OUTPUTS)
   for a in result.artifacts:
    self.assertEqual(a.body,case[-1][a.name]);self.assertEqual(a.sha256,hashlib.sha256(a.body).hexdigest());self.assertEqual(a.length,len(a.body))
   self.assertEqual(result.manifest_sha256,hashlib.sha256(result.manifest_bytes).hexdigest())
   self.assertTrue(result.bytes_consistency_verified)
   self.assertFalse(any((result.tool_execution_attested,result.owner_authorized,result.publication_performed,result.analysis_ready)))
 def test_flag_disabled_missing_columns_and_bom_crlf_keep_source_hash(self):
  self.assertEqual(run(small()).retained_rows,2)
  case=list(big());case[0]=b'\xef\xbb\xbf'+case[0].replace(b'\n',b'\r\n');case[1]=b'\xef\xbb\xbf'+case[1].replace(b'\n',b'\r\n')
  result=run(case);self.assertEqual(result.source_sha256,hashlib.sha256(case[0]).hexdigest())
 def test_raw_and_derived_zero_are_different_and_invalid_masks_never_become_success(self):
  for name,row,col,value in [('working-matrix.tsv',1,4,'0'),('working-NA-mask.tsv',1,4,'FALSE'),('original-zero-mask.tsv',1,4,'FALSE'),
   ('original-blank-mask.tsv',2,2,'FALSE'),('imputed-mask.tsv',1,2,'TRUE'),('working-matrix.tsv',1,2,'NA')]:
   case=big();mutate_output(case,name,row,col,value);self.failure(case)
 def test_raw_tokens_and_full_lineage_and_sample_header_order_are_exact(self):
  for name,row,col,value in [('raw-retained.tsv',1,2,'1.0'),('row-map.tsv',2,1,'NOT_DUP'),('row-map.tsv',3,2,'TRUE'),
   ('working-matrix.tsv',1,1,'OTHER_ID'),('working-matrix.tsv',0,2,'Intensity changed')]:
   case=big();mutate_output(case,name,row,col,value);self.failure(case)
  case=list(big());rows=list(csv.reader(io.StringIO(case[1].decode()),delimiter='\t'));rows[1:]=list(reversed(rows[1:]));case[1]=tsv(rows)
  self.failure(case,'selected_binding_mismatch')
 def test_invalid_source_in_filtered_row_still_rejects_whole_input(self):
  for token in ('PRIVATE_TOKEN','NA','-0','NaN','Inf',' 1','1e309','1e-4000'):
   case=list(big());rows=list(csv.reader(io.StringIO(case[0].decode()),delimiter='\t'));self.assertEqual(rows[3][1],'+');rows[3][4]=token
   case[0]=('\n'.join('\t'.join(row) for row in rows)+'\n').encode();self.failure(case)
 def test_summary_overlap_is_not_sum_and_all_parameter_flags_are_bound(self):
  case=big();rows=list(csv.reader(io.StringIO(case[-1]['summary.tsv'].decode()),delimiter='\t'));index=rows[0].index('excluded_rows');rows[1][index]='5';case[-1]['summary.tsv']=tsv(rows);self.failure(case,'artifact_value_mismatch')
  for flags in itertools.product((False,True),repeat=3):
   data=json.loads(v.parameters(flags));self.assertEqual(data['filter_flags'],dict(zip(('reverse','contaminant','site_only'),flags)))
  case=list(big());case[3]=(False,True,True);self.failure(case)
 def test_parameter_bytes_extra_mask_and_trailing_records_are_rejected(self):
  case=big();case[-1]['parameters.json']+=b'\n';self.failure(case,'parameter_bytes_mismatch')
  case=big();case[-1]['invalid-mask.tsv']=b'EXTRA';self.failure(case,'missing_or_extra_artifact')
  for name in ('working-matrix.tsv','row-map.tsv','filter-log.tsv','summary.tsv'):
   case=big();case[-1][name]+=b'\n';self.failure(case,'artifact_shape_mismatch')
 def test_strict_types_flags_callback_and_untrusted_mutable_body_are_rejected(self):
  self.failure(big(),'trusted_check_required',check=None)
  for value in (False,True,0):self.failure(big(),'invalid_check_result',check=lambda value=value:value)
  for flags in ([True,True,True],(1,True,True),(True,True),None):
   case=list(big());case[3]=flags;self.failure(case,'invalid_flags')
  case=big();case[-1]['working-matrix.tsv']=bytearray(case[-1]['working-matrix.tsv']);self.failure(case,'artifact_size_limit')
 def test_relative_tolerance_without_absolute_floor_and_exact_powers(self):
  self.assertEqual(run(small('3','1.5849625007211561')).retained_rows,2)
  self.assertEqual(run(small('3',format(1.5849625007211561*(1+5e-13),'.17g'))).retained_rows,2)
  self.failure(small('3',format(1.5849625007211561*(1+2e-12),'.17g')),'log2_value_mismatch')
  self.assertEqual(run(small('1.0000000000000002','3.203426503814917e-16')).retained_rows,2)
  self.failure(small('1.0000000000000002','0'),'log2_value_mismatch')
  self.failure(small('1','0.000000000000001'),'log2_value_mismatch')
  self.assertEqual(run(small('4.9406564584124654e-324','-1074')).retained_rows,2)
 def test_all_filtered_empty_has_no_complete_bundle_even_with_only_audit_files(self):
  case=list(small());case[0]=case[0].replace(b'Q\t\t2',b'Q\t+\t2');case[3]=(True,False,False)
  case[-1]={n:b'SYNTHETIC_AUDIT_ONLY' for n in ('summary.tsv','row-map.tsv','filter-log.tsv','parameters.json','R-session-info.txt')}
  self.failure(case,'empty_not_analyzable')
 def test_loneCR_badUTF8_ragged_and_missing_enabled_flag_refuse(self):
  for raw in (b'Protein IDs\tIntensity a\rP\t1\r',b'Protein IDs\tIntensity a\nP\t\xff\n',b'Protein IDs\tIntensity a\nP\t1\textra\n'):
   case=list(big());case[0]=raw;self.failure(case)
  case=list(small());case[3]=(False,True,False);self.failure(case,'missing_enabled_flag')
 def test_session_is_text_only_but_version_and_base_shape_must_match(self):
  for session in (SESSION.replace(b'4.6.1',b'4.6.10'),b'R version 4.6.1\n',SESSION.replace(b'compiler_4.6.1',b'PRIVATE_PACKAGE_1.0')):
   case=big();case[-1]['R-session-info.txt']=session;self.failure(case,'invalid_session_text')
 def test_imported_caps_enforced_for_artifact_total_input_and_field(self):
  case=big();case[-1]['working-matrix.tsv']=b'x'*(v.MAX_FILE+1);self.failure(case,'artifact_size_limit')
  case=big();body=b'x'*(v.MAX_TOTAL//11+1);case[-1].update({name:body for name in v.OUTPUTS});self.failure(case,'artifact_size_limit')
  case=list(big());case[0]=b'x'*(v.MAX_INPUT+1);self.failure(case,'invalid_source_bytes')
  self.assertEqual(run(small(first_id='P'*v.MAX_FIELD)).retained_rows,2)
  self.failure(small(first_id='P'*(v.MAX_FIELD+1)),'invalid_source_shape')
 def test_trusted_budget_failure_at_start_and_final_gate_returns_no_partial_bundle(self):
  def expired():raise RuntimeError('PRIVATE_BUDGET')
  self.failure(big(),'validation_interrupted',check=expired)
  counter=[0]
  def count():counter[0]+=1
  run(big(),count);last=counter[0];counter[0]=0
  def late():
   counter[0]+=1
   if counter[0]==last:raise RuntimeError('PRIVATE_LATE_BUDGET')
  self.failure(big(),'validation_interrupted',check=late);self.assertEqual(counter[0],last)
 def test_bundle_is_detached_immutable_repr_safe_without_execution_or_Owner_upgrade(self):
  case=big();saved=case[-1]['working-matrix.tsv'];counter=[0]
  def callback():
   counter[0]+=1
   if counter[0]==2:case[-1]['working-matrix.tsv']=b'PRIVATE_MUTATION'
  result=run(case,callback);self.assertEqual(next(a.body for a in result.artifacts if a.name=='working-matrix.tsv'),saved)
  with self.assertRaises(dataclasses.FrozenInstanceError):result.family='OTHER'
  with self.assertRaises(dataclasses.FrozenInstanceError):result.artifacts[0].sha256='x'
  self.assertNotIn('DUP',repr(result));self.assertNotIn('SYNTHETIC_TEXT',repr(result));self.assertNotIn('PRIVATE_MUTATION',repr(result))

class SessionRegression(unittest.TestCase):
 def actual(self,kind):
  raw,selected,family,flags,_=big(kind)
  outputs={name:(H/'fixtures/actual'/kind/name).read_bytes() for name in v.OUTPUTS}
  return raw,selected,family,flags,outputs
 def test_existing_actual_Intensity_and_LFQ_complete_bytes_still_pass_unchanged(self):
  for kind,sha in [('intensity','92700058f8054386db096ec4be41d07373d167a1d04ad4d709c4f5e008787e70'),('lfq','46cdefa54c0b6e2fc1879640132cc901875147e1a0d1dc2f45fb87ececb95e40')]:
   result=run(self.actual(kind));self.assertEqual(result.manifest_sha256,sha);self.assertFalse(result.tool_execution_attested)
 def test_original_independent_real_session_other_attached_counterexample_refuses(self):
  for kind in ('intensity','lfq'):
   case=self.actual(kind)
   case[-1]['R-session-info.txt']=case[-1]['R-session-info.txt'].replace(b'attached base packages:',b'other attached packages:\n[1] limma_3.68.5\n\nattached base packages:')
   with self.assertRaises(v.ValidationFailure) as error:run(case)
   self.assertEqual(error.exception.code,'invalid_session_text')
 def test_namespace_whole_text_unknown_or_duplicate_tokens_cannot_be_skipped(self):
  for tail in (b'UNRECOGNIZED\n',b'[2] UNRECOGNIZED\n',b'[2] compiler_4.6.1\n',b'other attached packages:\n[1] limma_3.68.5\n'):
   case=self.actual('intensity');case[-1]['R-session-info.txt']+=tail
   with self.assertRaises(v.ValidationFailure) as error:run(case)
   self.assertEqual(error.exception.code,'invalid_session_text')
  for replacement in (b'compiler_4.6.1 trailing_unknown',b'limma_3.68.5',b'compiler_4.6.1_UNKNOWN'):
   case=self.actual('lfq');case[-1]['R-session-info.txt']=case[-1]['R-session-info.txt'].replace(b'compiler_4.6.1',replacement)
   with self.assertRaises(v.ValidationFailure):run(case)
 def test_at_most_compiler_keeps_legal_absent_or_empty_namespace(self):
  for empty_marker in (False,True):
   case=self.actual('intensity');before=case[-1]['R-session-info.txt'].split(b'loaded via a namespace (and not attached):')[0]
   case[-1]['R-session-info.txt']=before+(b'loaded via a namespace (and not attached):\n' if empty_marker else b'')
   self.assertFalse(run(case).tool_execution_attested)
