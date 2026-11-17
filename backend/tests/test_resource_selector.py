"""Behavior tests use synthetic metadata only. No API or worker is invoked."""
from dataclasses import FrozenInstanceError,replace
import json
import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cyomics_projects.resource_selector import *
from provider_configuration import INPUT,CONTEXT,JOB_ID,LOCAL,STRING_MAPPING,partial_string_local

def plan(caps=('pfam.domains',),local=LOCAL,official=(),ctx=CONTEXT,identity=INPUT):
    return select_job({'job_id':JOB_ID,'capabilities':caps},identity,ctx,local,official)

def selected(p):return {s.capability:s for s in p.selections}

class SelectorTests(unittest.TestCase):
    def test_usable_local_always_wins_even_official_and_external_option_enabled(self):
        official=OfficialCapability('pfam.domains','synthetic-hmmer-official','synthetic-doc',True,True,True,'selected_sequences')
        p=plan(official=(official,))
        self.assertEqual(p.selections[0].provider,'local')
        self.assertEqual(p.external_calls,0)
        self.assertFalse(p.execution_enabled)
        self.assertFalse(p.authorization_verified)
        self.assertEqual(json.loads(p.selections[0].provenance_json)['external_payload_scope'],'none')

    def test_unknown_release_stays_unverified_but_local_is_usable(self):
        p=plan();record=json.loads(p.selections[0].provenance_json)['local_registration']
        self.assertEqual(p.selections[0].provider,'local')
        self.assertIsNone(record['database_version']);self.assertEqual(record['version_status'],'unverified')

    def test_missing_mapping_falls_back_only_mapping_network_stays_local(self):
        p=plan(('string.network','string.id_mapping'),partial_string_local(),(STRING_MAPPING,))
        self.assertEqual({c:s.provider for c,s in selected(p).items()},{'string.network':'local','string.id_mapping':'official_api'})
        self.assertEqual(p.external_calls,0)
        self.assertEqual(json.loads(selected(p)['string.id_mapping'].provenance_json)['external_payload_scope'],'selected_ids')

    def test_missing_requires_owner_project_external_choice(self):
        local=(partial_string_local()[1],)
        for option,expected in ((False,'unavailable'),(None,'needs_verification'),(True,'official_api')):
            p=plan(('string.id_mapping',),local,(STRING_MAPPING,),replace(CONTEXT,external_allowed=option))
            self.assertEqual(p.selections[0].provider,expected)

    def test_absent_or_unknown_local_is_not_missing(self):
        for resources in ((),(replace(partial_string_local()[1],state='unknown'),),(replace(partial_string_local()[1],state='unverified'),)):
            p=plan(('string.id_mapping',),resources,(STRING_MAPPING,))
            self.assertEqual(p.selections[0].provider,'needs_verification')

    def test_local_error_does_not_silently_use_available_official(self):
        p=plan(('string.id_mapping',),(replace(partial_string_local()[1],state='error'),),(STRING_MAPPING,))
        self.assertEqual(p.selections[0].provider,'error')
        self.assertEqual(p.external_calls,0)

    def test_no_registered_api_is_unavailable_without_fabricated_smart_or_alphafold(self):
        for c in ('smart.domains','alphafold.sequence_search'):
            r=LocalResource(c,'synthetic-missing','missing','synthetic-checked')
            p=plan((c,),(r,))
            self.assertEqual(p.selections[0].provider,'unavailable')
            self.assertIsNone(p.selections[0].resource_key)

    def test_official_identity_docs_and_parameter_verification_all_required(self):
        for field in ('identity_verified','capability_documented','parameters_verified'):
            for value in (False,None):
                s=replace(STRING_MAPPING,**{field:value})
                p=plan(('string.id_mapping',),(partial_string_local()[1],),(s,))
                self.assertEqual(p.selections[0].provider,'needs_verification')

    def test_official_string_species_must_bind_this_input_not_a_nearby_species(self):
        local=(partial_string_local()[1],)
        for params,expected in (('{}','needs_verification'),('{"species":9606}','error'),('{"species":true}','error'),('{"species":3702}','official_api')):
            s=replace(STRING_MAPPING,parameters_json=params)
            self.assertEqual(plan(('string.id_mapping',),local,(s,)).selections[0].provider,expected)

    def test_wrong_species_network_is_not_relabelled_and_fallback_is_explicit(self):
        local=replace(partial_string_local()[0],taxonomy_id=9606)
        service=replace(STRING_MAPPING,capability='string.network')
        p=plan(('string.network',),(local,),(service,))
        s=p.selections[0];provenance=json.loads(s.provenance_json)
        self.assertEqual(s.provider,'official_api');self.assertIn('species_mismatch',s.reason)
        self.assertEqual(provenance['local_registration']['taxonomy_id'],9606)
        self.assertEqual(provenance['input']['taxonomy_id'],3702)

    def test_string_requires_exact_species_and_known_mapping(self):
        base=partial_string_local()[0]
        for r,expected in ((replace(base,species_scope='any'),'unavailable'),(replace(base,mapping_method=None),'needs_verification'),(replace(base,taxonomy_id=None),'needs_verification')):
            self.assertEqual(plan(('string.network',),(r,)).selections[0].provider,expected)

    def test_failed_identifier_or_index_compatibility_is_capability_gap(self):
        for position in (2,4):
            checks=[True]*5;checks[position]=False
            r=replace(partial_string_local()[0],checks=tuple(checks))
            p=plan(('string.network',),(r,),(replace(STRING_MAPPING,capability='string.network'),))
            self.assertEqual(p.selections[0].provider,'official_api')
            self.assertIn('local_checks_incompatible',p.selections[0].reason)

    def test_pending_local_checks_never_trigger_external_fallback(self):
        for checks in ((),(True,True,None,True,True)):
            r=replace(LOCAL[1],checks=checks)
            self.assertEqual(plan(local=(r,)).selections[0].provider,'needs_verification')

    def test_client_cannot_supply_path_command_url_owner_or_resource_status(self):
        for k,v in (('path','/tmp/input'),('command','sh'),('url','https://evil.invalid'),('owner',True),('resource_status','usable'),('external_allowed',True),('project_id',CONTEXT.project_id)):
            request={'job_id':JOB_ID,'capabilities':('pfam.domains',),k:v}
            with self.subTest(k=k),self.assertRaises(ValueError):select_job(request,INPUT,CONTEXT,LOCAL)

    def test_no_hit_is_valid_and_cannot_change_locked_provider(self):
        p=plan()
        obs=observe_result(p,job_id=JOB_ID,capability_key='pfam.domains',provider='local',result_state='no_hit')
        self.assertEqual(obs.provider,'local');self.assertEqual(obs.result_state,'no_hit')
        self.assertFalse(obs.provider_changed);self.assertEqual(obs.external_calls,0)
        with self.assertRaises(ValueError):observe_result(p,job_id=JOB_ID,capability_key='pfam.domains',provider='official_api',result_state='hit')

    def test_runtime_errors_and_rate_limits_preserve_provider_and_do_not_retry(self):
        for p,c,provider in ((plan(),'pfam.domains','local'),(plan(('string.id_mapping',),(partial_string_local()[1],),(STRING_MAPPING,)),'string.id_mapping','official_api')):
            for state in ('error','timeout','rate_limited','cancelled'):
                obs=observe_result(p,job_id=JOB_ID,capability_key=c,provider=provider,result_state=state)
                self.assertEqual(obs.provider,provider);self.assertFalse(obs.provider_changed);self.assertEqual(obs.external_calls,0)

    def test_provider_provenance_and_job_hash_are_immutable_and_rechecked(self):
        p=plan()
        with self.assertRaises(FrozenInstanceError):p.project_id=JOB_ID
        for changed in (replace(p,selections=(replace(p.selections[0],provider='official_api'),)),replace(p,selections=(replace(p.selections[0],provenance_json='{}'),)),replace(p,execution_enabled=True)):
            with self.assertRaises(ValueError):validate_locked_plan(changed)
        with self.assertRaises(ValueError):observe_result(p,job_id=INPUT.version,capability_key='pfam.domains',provider='local',result_state='hit')

    def test_sequence_fallback_needs_exact_selected_id_hash_bindings(self):
        r=replace(LOCAL[1],state='missing');s=OfficialCapability('pfam.domains','synthetic-hmmer-official','synthetic-doc',True,True,True,'selected_sequences')
        self.assertEqual(plan(local=(r,),official=(s,),identity=replace(INPUT,sequence_sha256=())).selections[0].provider,'error')
        self.assertEqual(plan(local=(r,),official=(s,)).selections[0].provider,'official_api')
        for pairs in ((('OTHER','a'*64),),(('SYNTHETIC_P1','bad'),),(INPUT.sequence_sha256[0],INPUT.sequence_sha256[0])):
            with self.assertRaises(ValueError):plan(identity=replace(INPUT,sequence_sha256=pairs))

    def test_whole_project_upload_and_unbounded_timeouts_are_not_supported(self):
        for altered in (replace(STRING_MAPPING,payload_scope='whole_project'),replace(STRING_MAPPING,timeout_seconds=0),replace(STRING_MAPPING,timeout_seconds=61),replace(STRING_MAPPING,timeout_seconds=True)):
            with self.assertRaises(ValueError):plan(official=(altered,))

    def test_ambiguous_registration_and_invalid_metadata_fail_closed(self):
        for locals in ((LOCAL[1],LOCAL[1]),(replace(LOCAL[1],state='not_found'),),(replace(LOCAL[1],version_status='verified',database_version=None),)):
            with self.assertRaises(ValueError):plan(local=locals)
        with self.assertRaises(ValueError):plan(official=(STRING_MAPPING,STRING_MAPPING))
        with self.assertRaises(ValueError):plan(ctx={'project_id':CONTEXT.project_id,'owner':True})

    def test_parameter_provenance_cannot_smuggle_executor_or_endpoint_fields(self):
        for raw in ('{"url":"https://evil.invalid"}','{"argv":["sh"]}','{"x":"/tmp/file"}','{"x":NaN}','{"x":1,"x":2}'):
            with self.subTest(raw=raw),self.assertRaises(ValueError):plan(local=(replace(LOCAL[1],parameters_json=raw),))

    def test_domain_registry_seam_returns_only_existing_registered_local_key(self):
        p=plan(('blast.protein_homology','pfam.domains'))
        self.assertEqual(domain_runner_resource_key(p,'blast.protein_homology'),'swissprot-reviewed')
        self.assertEqual(domain_runner_resource_key(p,'pfam.domains'),'pfam-raw-reviewed')
        with self.assertRaises(ValueError):domain_runner_resource_key(p,'string.network')
        with self.assertRaises(ValueError):domain_runner_resource_key(plan(local=(replace(LOCAL[1],resource_key='other-pfam'),)),'pfam.domains')

if __name__=='__main__':unittest.main(verbosity=2)
