"""One new synthetic binder target. Actual original metadata/design validators and
StoredArtifact interface; explicit Owner repository read doubles, no DB/raw/R/auth.
"""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import asyncio,copy,dataclasses,json,socket,subprocess,unittest
from unittest.mock import patch
from uuid import uuid4
from cyomics_projects import preprocess_contract as c,preprocess_repository as p,qc_distribution_input as qc
from cyomics_projects import median_processed_profile as median,total_processed_profile as total,quantile_processed_profile as quantile
from cyomics_projects.sample_repository import SampleDesignRepository
from cyomics_projects.sample_persistence import canonical as canonical_design,sha as design_sha
from cyomics_projects.settings import Budget,Fault

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=json.loads((ROOT/'tests/fixtures/qc-distribution-descriptor.fixture.json').read_bytes())
BASE=FIXTURE['parent']['metadata'];A=BASE['created_by'];P=BASE['project_id'];B=str(uuid4())
WORK=b'"record_number"\t"protein_ids_raw"\t"LFQ intensity Sample1"\t"LFQ intensity Sample2"\n"1"\t"p; q"\t"-1"\t"NA"\n"2"\t"p2"\t"0"\t"2"\n"3"\t"p3"\t"NA"\t"-0.5"\n'
def mask(values):
 lines=['"record_number"\t"protein_ids_raw"\t"LFQ intensity Sample1"\t"LFQ intensity Sample2"']
 for n,(protein,v) in enumerate(zip(['p; q','p2','p3'],values),1):lines.append('"'+str(n)+'"\t"'+protein+'"\t"'+v[0]+'"\t"'+v[1]+'"')
 return ('\n'.join(lines)+'\n').encode()
class Reads:
 def __init__(self,normalization='none'):
  self.meta=copy.deepcopy(BASE);self.meta['processed_dataset_version_id']=str(uuid4());self.meta['source']['row_count']=3
  flags=self.meta['parameters']['filter_flags'];reference=dict(processed_dataset_version_id=BASE['processed_dataset_version_id'],manifest_sha256=BASE['manifest_sha256'])
  self.meta['parameters']=c._parameter_value(flags) if normalization=='none' else {'median':median,'total':total,'quantile':quantile}[normalization].parameters(flags,reference)
  self.meta['steps']=self.meta['parameters']['steps'];self.meta['parameters_sha256']=c.digest(self.meta['parameters'])
  self.bodies={name:('synthetic saved artifact '+name+'\n').encode() for name in c.ARTIFACT_NAMES};self.bodies['working-matrix.tsv']=WORK
  self.bodies['original-blank-mask.tsv']=mask([('FALSE','TRUE'),('FALSE','FALSE'),('FALSE','FALSE')])
  self.bodies['original-zero-mask.tsv']=mask([('FALSE','FALSE'),('FALSE','FALSE'),('TRUE','FALSE')])
  self.bodies['working-NA-mask.tsv']=mask([('FALSE','TRUE'),('FALSE','FALSE'),('TRUE','FALSE')])
  self.bodies['imputed-mask.tsv']=mask([('FALSE','FALSE')]*3);self.bodies['parameters.json']=c.canonical(self.meta['parameters'])
  self.meta['artifacts']=[dict(name=n,bytes=len(self.bodies[n]),sha256=c.bytes_sha(self.bodies[n]),media_type='application/json' if n.endswith('.json') else 'text/plain; charset=utf-8' if n.endswith('.txt') else 'text/tab-separated-values; charset=utf-8') for n in c.ARTIFACT_NAMES]
  self.meta['manifest_sha256']=c.digest(self.meta['artifacts']);c.version_value(self.meta)
  self.design=copy.deepcopy(FIXTURE['design']['envelope']);record=self.design['record'];record['sample_design_version_id']=str(uuid4());record['request_id']=str(uuid4())
  self.design['project_id']=P;self.design['dataset_version_id']=self.meta['parent_dataset_version_id'];record['design']['dataset_version_id']=self.meta['parent_dataset_version_id']
  record['design']['rows']=record['design']['rows'][:2]
  selected=[dict(ordinal=r['column_ordinal'],name_raw=r['name_raw'],sample_label_raw=r['sample_label_raw'],quant_family='LFQ intensity') for r in record['design']['rows']]
  self.meta['source']['selected_columns_sha256']=p.digest(p.canonical(selected))
  record['design_sha256']=design_sha(canonical_design(record['design']))
  self.pool=object();self.repo=p.PreprocessRepository(self.pool,enabled=True);self.designs=SampleDesignRepository(self.pool)
  self.version_calls=0;self.artifact_calls=[];self.design_calls=[];self.wrapper_fault=None;self.drift=False;self.design_drift=False
  async def version(subject,project,vid,budget):
   self.version_calls+=1
   if subject!=A or project!=P or vid!=self.meta['processed_dataset_version_id']:raise Fault(404,'processed_dataset_version_not_found')
   value=copy.deepcopy(self.meta)
   if self.drift and self.version_calls>1:value['proposal_sha256']='f'*64
   return value
  async def artifact(subject,project,vid,name,budget):
   self.artifact_calls.append(name);assert subject==A and project==P and vid==self.meta['processed_dataset_version_id']
   pin=next(a for a in self.meta['artifacts'] if a['name']==name);v=p.StoredArtifact(name,pin['media_type'],self.bodies[name],pin['sha256'])
   return self.wrapper_fault(v) if self.wrapper_fault else v
  async def design(subject,project,dataset,family,budget,request_id=None):
   self.design_calls.append(request_id);assert subject==A and project==P and dataset==self.meta['parent_dataset_version_id'] and family=='LFQ intensity' and request_id is not None
   result=copy.deepcopy(self.design)
   if self.design_drift and len(self.design_calls)>1:result['record']['created_at']='2026-11-18T00:00:00.000000Z'
   return result
  self.repo.read_version=version;self.repo.read_artifact=artifact;self.designs.read=design
 def binder(self,on=True):return qc.Binder(self.repo,self.designs,config=qc.Config(enabled=on))
 async def bind(self,grouped=True,subject=A):
  kw=dict(design_request_id=self.design['record']['request_id'],design_version_id=self.design['record']['sample_design_version_id']) if grouped else {}
  return await self.binder().bind(subject,P,self.meta['processed_dataset_version_id'],Budget(),**kw)

class SavedQCBinderTarget(unittest.IsolatedAsyncioTestCase):
 async def test_one_new_synthetic_binder_target(self):
  groups=[]
  with patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')) as net,patch.object(subprocess,'Popen',side_effect=AssertionError('native forbidden')) as native,patch('asyncio.create_subprocess_exec',side_effect=AssertionError('native forbidden')) as asyncnative:
   off=Reads()
   with self.assertRaises(Fault):await off.binder(False).bind(A,P,off.meta['processed_dataset_version_id'],Budget())
   self.assertEqual((off.version_calls,off.artifact_calls,off.design_calls),(0,[],[]))
   snapshots=[]
   for method in ['none','median','total','quantile']:
    data=Reads(method);before=copy.deepcopy((data.meta,data.design,data.bodies));snapshot=await data.bind();snapshots.append(snapshot)
    self.assertEqual(snapshot.provenance()['normalization'],method);self.assertEqual(json.loads(snapshot.parent_json)['parameters']['normalization'],method)
    self.assertEqual((data.version_calls,len(data.artifact_calls),len(data.design_calls)),(2,11,2));self.assertEqual(tuple(data.artifact_calls),c.ARTIFACT_NAMES)
    self.assertTrue(all(type(a) is p.StoredArtifact for a in snapshot.artifacts));self.assertEqual({a.name:a.body for a in snapshot.artifacts},data.bodies)
    self.assertEqual(snapshot.rows[0].tokens,('-1','NA'));self.assertEqual(snapshot.rows[1].tokens,('0','2'));self.assertEqual(snapshot.rows[2].tokens,('NA','-0.5'))
    self.assertEqual(snapshot.rows[0].protein_ids_raw,'p; q');self.assertEqual([s.source_column_ordinal for s in snapshot.samples],[1,2]);self.assertEqual(snapshot.provenance()['grouping'],'confirmed_saved_design')
    self.assertEqual(before,(data.meta,data.design,data.bodies));self.assertEqual(snapshot.binding_sha256,c.bytes_sha(snapshot.provenance_json))
    with self.assertRaises(dataclasses.FrozenInstanceError):snapshot.rows=()
    with self.assertRaises(dataclasses.FrozenInstanceError):snapshot.artifacts[0].name='changed'
    mutated=snapshot.provenance();mutated['parent']['parameters']['normalization']='fake';self.assertEqual(snapshot.provenance()['normalization'],method)
    data.meta['source']['row_count']=999;data.design['record']['design']['rows'][0]['group']='changed';self.assertEqual(snapshot.provenance()['parent']['source']['row_count'],3)
   self.assertEqual(len(set(s.binding_sha256 for s in snapshots)),4)
   data=Reads('median');snapshot=await data.bind(False);self.assertIsNone(snapshot.design_json);self.assertEqual(data.design_calls,[]);self.assertTrue(all(s.group_label_raw is None and s.source_column_ordinal is None for s in snapshot.samples))
   self.assertEqual(snapshot.provenance()['grouping'],'ungrouped_explicit')
   groups.append('original four metadata parameter validators; actual StoredArtifact(name,media_type,body,sha256) all11/fresh metadata/design; signed/zero/NA tokens and masks preserved; immutable copies and explicit ungrouped0designreads')
   # Original wrapper shape must not be mistaken for raw bytes or an authorized DTO.
   for mutate in [lambda v:v.body,lambda v:dict(name=v.name,body=v.body,sha256=v.sha256,media_type=v.media_type),
     lambda v:p.StoredArtifact(v.name,v.media_type,v.body,'0'*64),lambda v:p.StoredArtifact(v.name,'wrong',v.body,v.sha256),
     lambda v:p.StoredArtifact('wrong',v.media_type,v.body,v.sha256),lambda v:p.StoredArtifact(v.name,v.media_type,bytearray(v.body),v.sha256),
     lambda v:p.StoredArtifact(v.name,v.media_type,v.body+b'x',v.sha256)]:
    data=Reads();data.wrapper_fault=mutate
    with self.assertRaises(Fault):await data.bind()
   data=Reads()
   with self.assertRaises(Fault) as denied:await data.bind(subject=B)
   self.assertEqual(denied.exception.status,404);self.assertFalse(data.artifact_calls or data.design_calls)
   data=Reads();data.meta['created_by']=B
   with self.assertRaises(Fault) as denied:await data.bind()
   self.assertEqual(denied.exception.status,404);self.assertFalse(data.artifact_calls)
   data=Reads();data.meta['source']['dataset_version_id']=str(uuid4())
   with self.assertRaises(Fault):await data.bind()
   self.assertFalse(data.artifact_calls)
   groups.append('bytes/dict/wrong wrapper fields, mutable body, corrupt SHA/length refused; wrong Owner before artifact/design access; source/parent mismatch refused')
   for change in ['version','request','names','unconfirmed','source_ordinal','design_hash','selected_source_hash']:
    data=Reads();d=data.design['record']
    if change=='version':chosen=str(uuid4())
    else:chosen=d['sample_design_version_id']
    if change=='request':d['request_id']=str(uuid4())
    if change=='names':d['design']['rows'][0]['name_raw']='LFQ intensity other'
    if change=='unconfirmed':d['design']['rows'][0]['confirmed']=False
    if change=='source_ordinal':d['design']['rows'][1]['column_ordinal']=500
    if change=='design_hash':d['design_sha256']='f'*64
    if change=='selected_source_hash':data.meta['source']['selected_columns_sha256']='f'*64
    # Except hash-corruption case, make design itself internally canonical so QC
    # tests actual cross-binding checks, not only the sample schema.
    if change!='design_hash':d['design_sha256']=design_sha(canonical_design(d['design']))
    with self.assertRaises(Fault):await data.binder().bind(A,P,data.meta['processed_dataset_version_id'],Budget(),design_request_id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' if change=='request' else d['request_id'],design_version_id=chosen)
   data=Reads();data.drift=True
   with self.assertRaises(Fault):await data.bind()
   data=Reads();data.design_drift=True
   with self.assertRaises(Fault):await data.bind()
   data=Reads();data.bodies['imputed-mask.tsv']=mask([('TRUE','FALSE')]*3)
   pin=next(a for a in data.meta['artifacts'] if a['name']=='imputed-mask.tsv');pin.update(bytes=len(data.bodies[pin['name']]),sha256=c.bytes_sha(data.bodies[pin['name']]));data.meta['manifest_sha256']=c.digest(data.meta['artifacts'])
   with self.assertRaises(Fault):await data.bind()
   data=Reads()
   with self.assertRaises(Fault):await data.binder().bind(A,P,data.meta['processed_dataset_version_id'],Budget(),design_request_id=str(uuid4()))
   self.assertEqual(data.version_calls,0)
   self.assertEqual((net.call_count,native.call_count,asyncnative.call_count),(0,0,0))
   groups.append('explicit design pair/version/request/header/confirmation/source ordinal/hash checks; fresh parent/design drift and internally hashed wrong masks refused; no mutable-head query')
   print(json.dumps(dict(target='PASS_ONE_SYNTHETIC_OWNER_QC_BINDER_TARGET',groups=groups,actual_wrapper_and_original_model_validators=True,repositories='actual original classes with explicit synthetic Owner/version/artifact/design read methods',actual_SQL_Auth_R_raw_services=0,old_targets=0,scientific_generalization_proof=False),sort_keys=True))
if __name__=='__main__':unittest.main()
