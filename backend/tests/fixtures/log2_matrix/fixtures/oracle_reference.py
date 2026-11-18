"""Pure literal/Fraction oracle, independent of R calculations. No filesystem I/O."""
import csv,io,json,re
from fractions import Fraction
OUTPUTS=('filter-log.tsv','row-map.tsv','summary.tsv','raw-retained.tsv','working-matrix.tsv',
 'original-blank-mask.tsv','original-zero-mask.tsv','working-NA-mask.tsv','imputed-mask.tsv','R-session-info.txt','parameters.json')
POWERS={Fraction(1,4):'-2',Fraction(1,2):'-1',Fraction(1):'0',Fraction(2):'1',Fraction(4):'2',Fraction(8):'3',Fraction(16):'4'}
class Invalid(Exception):pass
def need(v):
 if not v:raise Invalid('synthetic_log2_oracle_refused')
def table(raw):
 need(type(raw)is bytes and 0<len(raw)<=262144 and b'\0' not in raw and b'\r' not in raw and not raw.startswith(b'\xef\xbb\xbf'))
 return list(csv.reader(io.StringIO(raw.decode('utf8'),newline=''),delimiter='\t',strict=True))
def expected(input_bytes,selected_bytes,family):
 rows=table(input_bytes);header=rows[0];source=rows[1:];need(len(source)==8 and all(len(row)==len(header) for row in source))
 need(family in ('Intensity','LFQ intensity'))
 indices=[i for i,name in enumerate(header) if name.startswith(family+' ')];need(len(indices)==(3 if family=='Intensity' else 2))
 selected=table(selected_bytes)
 need(selected[0]==['column_ordinal','name_raw','quant_family','sample_label_raw'])
 need(selected[1:]==[[str(i),header[i],family,header[i][len(family)+1:]] for i in indices])
 need(header[:4]==['Protein IDs','Reverse','Potential contaminant','Only identified by site'])
 prefix=['record_number','protein_ids_raw'];out={};log=[];mapping=[];keep=[];reasons=('reverse','potential_contaminant','site_only')
 for number,row in enumerate(source,1):
  flags=[v=='+' for v in row[1:4]];need(all(v in ('','+') for v in row[1:4]))
  excluded=any(flags);position='NA' if excluded else str(len(keep)+1)
  log.append([str(number),row[0],*[str(v).upper() for v in flags],str(excluded).upper(),'|'.join(name for name,flag in zip(reasons,flags) if flag)])
  mapping.append([str(number),row[0],str(not excluded).upper(),position])
  if not excluded:keep.append((number,row))
 need([n for n,row in keep]==[1,2,7,8] and [row[0] for n,row in keep][:2]==['DUP','DUP'])
 out['filter-log.tsv']=[prefix+['excluded_reverse','excluded_contaminant','excluded_site_only','excluded','reason_codes'],*log]
 out['row-map.tsv']=[['source_record_number','protein_ids_raw','kept','work_row_ordinal'],*mapping]
 names=('raw-retained.tsv','working-matrix.tsv','original-blank-mask.tsv','original-zero-mask.tsv','working-NA-mask.tsv','imputed-mask.tsv')
 matrix_header=prefix+[header[i] for i in indices]
 for name in names:out[name]=[matrix_header]
 retained_blank=retained_zero=retained_positive=0
 for number,row in keep:
  tokens=[row[i] for i in indices];work=[];blank=[];zero=[];missing=[];imputed=[]
  for token in tokens:
   value=None if token=='' else Fraction(token)
   need(value is None or value>=0)
   is_blank=value is None;is_zero=value==0 if value is not None else False
   if not is_blank and not is_zero:need(value in POWERS)
   work.append('NA' if is_blank or is_zero else POWERS[value])
   blank.append(str(is_blank).upper());zero.append(str(is_zero).upper());missing.append(str(is_blank or is_zero).upper());imputed.append('FALSE')
   retained_blank+=is_blank;retained_zero+=is_zero;retained_positive+=not is_blank and not is_zero
  for name,cells in zip(names,(tokens,work,blank,zero,missing,imputed)):out[name].append([str(number),row[0],*cells])
 raw=[row[i] for row in source for i in indices]
 source_blank=sum(v=='' for v in raw);source_zero=sum(v!='' and Fraction(v)==0 for v in raw);source_positive=sum(v!='' and Fraction(v)>0 for v in raw)
 fields=['status','input_rows','input_columns','quant_family','selected_columns','input_selected_cells','kept_rows','excluded_rows',
 'excluded_reverse','excluded_contaminant','excluded_site_only','overlap_excluded_rows','source_blank_cells','source_zero_cells','source_positive_cells',
 'retained_selected_cells','retained_blank_cells','retained_zero_cells','retained_positive_cells','working_NA_cells','imputed_cells',
 'reverse_enabled','contaminant_enabled','site_only_enabled','zero_policy','blank_policy','log2','pseudocount','normalization','imputation','analysis_ready']
 values=['complete_positive_log2',8,len(header),family,len(indices),8*len(indices),4,4,
 *[sum(row[j]=='+' for row in source) for j in (1,2,3)],sum(sum(v=='+' for v in row[1:4])>1 for row in source),
 source_blank,source_zero,source_positive,4*len(indices),retained_blank,retained_zero,retained_positive,retained_blank+retained_zero,0,
 'TRUE','TRUE','TRUE','to_NA','to_NA','positive_only',0,'none','none','FALSE']
 need(len(fields)==len(values));out['summary.tsv']=[fields,[str(v) for v in values]]
 return out,{'family':family,'source_rows':8,'kept_rows':4,'selected_columns':len(indices),
 'finite_cells':retained_positive,'working_NA_cells':retained_blank+retained_zero,'all_eleven_files_verified':True}
def session(raw):
 need(type(raw)is bytes and 0<len(raw)<=262144 and b'\0' not in raw)
 text=raw.decode('utf8');need(text.startswith('R version 4.6.1') and 'attached base packages:' in text)
 attached=text.split('attached base packages:',1)[1].split('\n\n',1)[0]
 need(set(re.sub(r'\[[0-9]+\]','',attached).split())=={'stats','graphics','grDevices','utils','datasets','methods','base'})
 if 'loaded via a namespace (and not attached):' in text:
  namespaces=text.split('loaded via a namespace (and not attached):',1)[1]
  need(set(re.findall(r'([A-Za-z][A-Za-z0-9.]*)_[0-9]',namespaces))<=set(('compiler',)))
 return True
def family(outputs,input_bytes,selected_bytes,parameters_bytes,family_name):
 need(type(outputs)is dict and set(outputs)==set(OUTPUTS))
 want,proof=expected(input_bytes,selected_bytes,family_name)
 for name,rows in want.items():need(table(outputs[name])==rows)
 # Compare actual written JSON bytes, not a caller-provided SHA or R echo.
 need(outputs['parameters.json']==parameters_bytes)
 parameters=json.loads(parameters_bytes)
 need(parameters_bytes==json.dumps(parameters,sort_keys=True,separators=(',',':')).encode())
 need(parameters['filter_flags']=={'contaminant':True,'reverse':True,'site_only':True} and parameters['pseudocount']==0)
 session(outputs['R-session-info.txt']);return proof
