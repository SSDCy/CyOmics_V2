"""Pure request bytes and real malformed/forged inputs; no Owner or R fixture run."""
import dataclasses,hashlib,importlib.util,itertools,json,pathlib,sys,unittest

H=pathlib.Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('preprocess_contract_checked',H/'cyomics_projects/preprocess_contract.py')
c=importlib.util.module_from_spec(spec);sys.modules[spec.name]=c;spec.loader.exec_module(c)
REFERENCE_BYTES=(pathlib.Path(__file__).parent/'fixtures/preprocess_contract/parameters.reference.json').read_bytes()
REFERENCE=json.loads(REFERENCE_BYTES)
ID='11111111-1111-4111-8111-111111111111'
OTHER='22222222-2222-4222-8222-222222222222'
def body():return {'request_id':ID,'quant_family':'Intensity','parameters':json.loads(REFERENCE_BYTES)}
def encoded(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()

class Tests(unittest.TestCase):
    def reject(self,value,code='invalid_preprocessing_request',raw=False):
        with self.assertRaises(c.RequestError) as error:c.request_input(value if raw else encoded(value))
        self.assertEqual(error.exception.code,code)
        self.assertNotIn('PRIVATE',str(error.exception));self.assertNotIn('PRIVATE',repr(error.exception))

    def test_default_and_actual12_key_reference_are_exact(self):
        self.assertFalse(c.ENABLED);self.assertEqual(c.CONTRACT,'preprocessing_log2_job_v0.1')
        self.assertEqual(len(REFERENCE),12);self.assertEqual(set(REFERENCE),c.PARAMETER_KEYS)
        self.assertEqual(hashlib.sha256(REFERENCE_BYTES).hexdigest(),'c84e77dc7c9910238356add0f78e31874ebfa3b20ad6175dae59439c3c806e07')
        parsed=c.request_input(encoded(body()));self.assertEqual(parsed.parameters_bytes,REFERENCE_BYTES)

    def test_all8_named_bool_combinations_for_both_exact_family_names(self):
        for flags in itertools.product((False,True),repeat=3):
            wanted=dict(zip(('contaminant','reverse','site_only'),flags))
            for family in ('Intensity','LFQ intensity'):
                value=body();value['parameters']['filter_flags']=wanted;value['quant_family']=family
                request=c.request_input(encoded(value));expected=encoded({**REFERENCE,'filter_flags':wanted})
                self.assertEqual(request.parameters_bytes,expected)
                self.assertEqual(c.canonical_parameters(wanted),expected)
                self.assertEqual(request.parameters_sha256,hashlib.sha256(expected).hexdigest())
                self.assertEqual(request.canonical_request_bytes,encoded(value))
                self.assertEqual(request.request_sha256,hashlib.sha256(encoded(value)).hexdigest())
                self.assertEqual(dict(request.filter_flags),wanted)

    def test_body_missing_unknown_and_client_server_bindings_are_all_rejected(self):
        for key in ('request_id','quant_family','parameters'):
            value=body();del value[key];self.reject(value)
        for key in ('schemaVersion','params','contextKey','source','owner_id','subject','project_id','dataset_version_id','file_id','source_sha256','path','execution','SourceRef'):
            value=body();value[key]='PRIVATE_FORGED_BINDING';self.reject(value)
        self.reject({'contextKey':'PRIVATE_CONTEXT','source':{},'quant_family':'Intensity','parameters':REFERENCE})

    def test_every_fixed_parameter_is_required_and_not_editable(self):
        wrong={'blank_policy':'preserve','imputation':'mean','input_scale':'log2','log2':'none','normalization':'median',
            'output_scale':'raw','preset':'OTHER','pseudocount':1,'seed':0,'steps':list(reversed(REFERENCE['steps'])),'zero_policy':'preserve'}
        for key in REFERENCE:
            value=body();del value['parameters'][key];self.reject(value)
        for key,item in wrong.items():
            value=body();value['parameters'][key]=item;self.reject(value)
        value=body();value['parameters']['invalid_policy']='PRIVATE_AS_NA';self.reject(value)
        value=body();value['parameters']['steps']+=['fit'];self.reject(value)

    def test_booleans_numbers_null_and_steps_have_strict_distinct_types(self):
        for flag in ('contaminant','reverse','site_only'):
            for bad in (0,1,'true',None,[],{}):
                value=body();value['parameters']['filter_flags'][flag]=bad;self.reject(value)
        for flags in ({'reverse':True,'contaminant':False},{**REFERENCE['filter_flags'],'extra':False},[],True,None):
            value=body();value['parameters']['filter_flags']=flags;self.reject(value)
        for key,bad in (('pseudocount',False),('pseudocount',0.0),('seed',False),('seed',0),('steps','flags_filter'),('steps',[1]*5)):
            value=body();value['parameters'][key]=bad;self.reject(value)
        for flags in ({'contaminant':False,'reverse':True,'site_only':0},None):
            with self.assertRaises(c.RequestError):c.canonical_parameters(flags)

    def test_duplicate_keys_at_root_parameter_and_flags_depth_rejected(self):
        raw=encoded(body())
        for duplicate in (raw[:-1]+b',"request_id":"'+ID.encode()+b'"}',
            raw[:-1]+b',"request\\u005fid":"'+ID.encode()+b'"}',
            raw.replace(b'"normalization":"none"',b'"normalization":"none","normalization":"none"'),
            raw.replace(b'"reverse":true',b'"reverse":true,"reverse":false')):
            self.reject(duplicate,raw=True)

    def test_malformed_unicode_constants_nonobject_and_trailing_values_refuse(self):
        for raw in (b'',b'\xff',b'\xef\xbb\xbf'+encoded(body()),b'\0'+encoded(body()),b'{',encoded(body())+b'{}',
            b'[]',b'null',b'true',b'1',b'{"PRIVATE":"\\ud800"}',b'{"PRIVATE":NaN}',b'{"PRIVATE":Infinity}',b'{"PRIVATE":-Infinity}',b'{"PRIVATE":1e9999}'):
            self.reject(raw,raw=True)
        self.reject(b'['*1500+b'0'+b']'*1500,raw=True)

    def test_exact_body_cap_and_immutable_bytes_input_only(self):
        raw=encoded(body());at_cap=raw+b' '*(c.MAX_REQUEST_BYTES-len(raw))
        self.assertEqual(c.request_input(at_cap).request_sha256,c.request_input(raw).request_sha256)
        self.reject(at_cap+b' ','preprocessing_request_too_large',raw=True)
        for bad in (bytearray(raw),memoryview(raw),raw.decode(),body(),None):self.reject(bad,raw=True)

    def test_uuid_family_tokens_are_canonical_no_trim_alias_or_autoselect(self):
        for value in ('bad-id',ID.upper().replace('1111','ABCD',1),' '+ID,ID.replace('-',''),1,True,None):
            data=body();data['request_id']=value;self.reject(data)
        for family in ('intensity','lfq','LFQ','Intensity ','LFQ intensity ',None,[],True):
            data=body();data['quant_family']=family;self.reject(data)

    def test_canonical_snapshot_detached_and_hashes_track_semantics(self):
        value=body();pretty=json.dumps(value,indent=2).encode();request=c.request_input(pretty)
        self.assertEqual(request.request_sha256,c.request_input(encoded(value)).request_sha256)
        changed=request.parameters_value();changed['filter_flags']['reverse']=False;changed['steps'].append('PRIVATE_MUTATION')
        self.assertEqual(request.parameters_value(),REFERENCE)
        changed=request.request_value();changed['parameters']['normalization']='PRIVATE_MUTATION'
        self.assertEqual(request.request_value(),value)
        with self.assertRaises(dataclasses.FrozenInstanceError):request.quant_family='LFQ intensity'
        with self.assertRaises(TypeError):request.filter_flags[0]=('contaminant',False)
        value['request_id']=OTHER;self.assertNotEqual(c.request_input(encoded(value)).request_sha256,request.request_sha256)
        value=body();value['quant_family']='LFQ intensity';self.assertNotEqual(c.request_input(encoded(value)).request_sha256,request.request_sha256)
        value=body();value['parameters']['filter_flags']['reverse']=False;self.assertNotEqual(c.request_input(encoded(value)).parameters_sha256,request.parameters_sha256)
        self.assertFalse(hasattr(request,'owner_authorized'));self.assertFalse(hasattr(request,'analysis_ready'))

if __name__=='__main__':unittest.main(verbosity=2)
