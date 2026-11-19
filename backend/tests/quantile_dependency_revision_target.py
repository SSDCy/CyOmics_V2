"""One new no-R dependency closure and unchanged recorded-output validation only."""
import sys,json,copy,subprocess,socket
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
# Fixture definitions only; old full/native main is not executed.
import quantile_processed_strategy_target as fixture
from cyomics_projects import quantile_processed_profile as profile,quantile_processed_runner as runner
from cyomics_projects import settings

def denied(*a,**k):raise AssertionError('No R, socket, Auth or real DB in dependency revision target')
subprocess.Popen=denied;socket.socket.connect=denied
before=(settings.AUTH_POLICY_VERIFIED,settings.RUNTIME_START_APPROVED)
assert before==(False,False)
result=runner.QuantileProcessedRunner()._library(lambda:None)
assert len(result)==37 and len(result['digest_inventory']['files'])==43
inventory=result['digest_inventory'];root=profile.DEPENDENCIES['digest_library_root']
assert profile.validate_digest_inventory(root,inventory)==inventory
bad=copy.deepcopy(inventory);bad['files']['R/digest.rdb']['sha256']='0'*64
cases=[]
for path,observed,name in [(root,bad,'changed_digest_bytes'),('/tmp/digest',inventory,'different_digest_path'),(root,dict(inventory,root_uid=1000),'wrong_package_owner')]:
 try:profile.validate_digest_inventory(path,observed)
 except ValueError as e:assert str(e)=='quantile_digest_dependency_changed';cases.append(name)
 else:raise AssertionError('Wrong dependency observation accepted')
db,selected,legacy,proposal=fixture.make_fixture()
assert json.loads(proposal)['runner']==profile.PINS['runner'] and json.loads(proposal)['script']==profile.PINS['script']
fixture.verify_four_profile_shapes(legacy,proposal)
output=Path('/home/user01/Cy_Omics_v2.0/audit/S1-QUANTILE-PROCESSED-STRATEGY-001/attempt-003/flow/staging')
jobs=list(output.iterdir());assert len(jobs)==1
bodies={n:(jobs[0]/'output'/n).read_bytes() for n in fixture.c.ARTIFACT_NAMES}
bundle=fixture.validator.validate_bundle(db.raw,selected,'LFQ intensity',(True,True,True),bodies,check=lambda:None,
 parent=db.metadata(fixture.PARENT),parent_outputs=db.versions[fixture.PARENT][1],parameters=json.loads(bodies['parameters.json']),owner=fixture.OWNER)
assert len(bundle.artifacts)==11
assert (settings.AUTH_POLICY_VERIFIED,settings.RUNTIME_START_APPROVED)==before
print(json.dumps({'status':'PASS_DEPENDENCY_REVISION_NO_R','fixed_staged_files':36,'fixed_digest_files':43,'negative_cases':cases,
 'profile_dependencies':profile.DEPENDENCIES,'recorded003_outputs_strictly_validated':True,'manifest':bundle.manifest_sha256,
 'native_children':0,'old_full_target_calls':0,'actual_DB':False,'gates_unchanged':True,
 'limits':'New R library/namespace-path guards inspected in source only, not executed; original003 native receipt is historical old pins, not revised product Native proof'}))
