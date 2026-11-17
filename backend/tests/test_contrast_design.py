"""Portable shared-fixture tests. No runtime, resource, statistical or auth proof."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
from types import ModuleType
import unittest
from unittest.mock import patch
from uuid import UUID

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
from cyomics_projects import contrast_design as model

FIXTURE = json.loads((HERE.parents[1]/'tests'/'fixtures'/'contrast-common.synthetic.json').read_bytes())
HITS = []
def forbidden(*a, **kw):
    HITS.append('side_effect'); raise AssertionError('No runtime operation')
def audit(event, args):
    if event.startswith(('socket.', 'subprocess.')) or event in ('os.system','os.kill','os.killpg'):
        HITS.append(event); raise AssertionError('No network/process action')
    if event == 'open' and isinstance(args[0], (str, bytes)):
        p = str(args[0])
        if '/candidate/' in p or '/proc/' in p or '/.env' in p or '/cyomics/data/' in p:
            HITS.append('private_read'); raise AssertionError('No private/candidate/resource read')
sys.addaudithook(audit)

def fresh(): return deepcopy(FIXTURE['source']), deepcopy(FIXTURE['comparisonDraft'])
def seal(source):
    source['sample_design_record']['design_sha256'] = model.sha(source['sample_design_record']['design'])
    return source
def row_source(groups, types=None, confirmed=True):
    src, draft = fresh(); columns=[]; rows=[]
    for i, group in enumerate(groups):
        ordinal = 3 + i*2
        columns.append({'ordinal':ordinal,'name_raw':'Intensity synthetic-'+str(i),'sample_label_raw':'synthetic-'+str(i),'quant_family':'Intensity'})
        rows.append({'column_ordinal':ordinal,'name_raw':columns[-1]['name_raw'],'sample_label_raw':columns[-1]['sample_label_raw'],'group':group,'condition':'synthetic condition','control':'yes' if group==groups[0] else 'no','replicate':str(i),'replicate_type':'biological' if types is None else types[i],'confirmed':confirmed})
    src['sample_columns']=columns;src['sample_design_record']['design']['rows']=rows
    return seal(src)


class BindingTests(unittest.TestCase):
    def test_shared_fixture_canonical_digest_state_summary_and_no_computed_result(self):
        src, payload = fresh(); bound = model.bind_source(src, expected_project_id=src['project_id'])
        self.assertEqual(model.sha(src['sample_design_record']['design']), '5e56e3d7b7dd337fe2b7dcb53e132fd4f58404cbfc3aba090e3255ec27b20666')
        result = model.review_draft(payload, bound)
        self.assertEqual(result.state, FIXTURE['expectedState']); self.assertFalse(result.analysis_ready)
        self.assertEqual([(g.group,g.total_columns,g.confirmed_columns,g.column_ordinals) for g in result.groups], [('control-synthetic',3,3,(3,4,5)),('treatment-synthetic',3,3,(6,7,8))])
        self.assertEqual(result.issues,());self.assertEqual(result.draft.to_payload(),payload)
        self.assertEqual(result.to_payload()['direction']['FC'],'numerator/denominator')
        self.assertFalse(result.to_payload()['direction']['computed_statistics'])

    def test_source_schema_digest_metadata_ids_and_server_project_context(self):
        src,_=fresh()
        mutations=[lambda s:s.update(owner_id='forged'),lambda s:s.update(project_id='bad'),lambda s:s.update(dataset_version_id='99999999-9999-4999-8999-999999999999'),lambda s:s.update(quant_family='LFQ intensity'),lambda s:s['sample_design_record'].update(extra='forged'),lambda s:s['sample_design_record'].update(design_sha256='0'*64),lambda s:s['sample_design_record'].update(sample_design_version_id='bad'),lambda s:s['sample_design_record'].update(request_id='bad'),lambda s:s['sample_design_record'].update(revision=0),lambda s:s['sample_design_record'].update(revision=True),lambda s:s['sample_design_record'].update(revision=101),lambda s:s['sample_design_record'].update(parent_version_id='99999999-9999-4999-8999-999999999999'),lambda s:s['sample_design_record'].update(created_at='2026-01-01'),lambda s:s['sample_design_record'].update(created_at='2026-01-01T00:00:00Z')]
        for mutate in mutations:
            value=deepcopy(src);mutate(value)
            with self.assertRaises(model.ContrastError):model.bind_source(value)
        with self.assertRaises(model.ContrastError) as error:model.bind_source(src,expected_project_id='99999999-9999-4999-8999-999999999999')
        self.assertEqual(error.exception.code,'source_project_mismatch')

    def test_independent_descriptors_not_reconstructed_from_tampered_saved_design(self):
        src,_=fresh();src['sample_design_record']['design']['rows'][0]['name_raw']='Intensity forged';seal(src)
        with self.assertRaises(model.ContrastError):model.bind_source(src)
        src,_=fresh();src['sample_design_record']['design']['rows'].reverse();seal(src)
        with self.assertRaises(model.ContrastError):model.bind_source(src)
        src,_=fresh();src['sample_columns'][0]['ordinal']=True
        with self.assertRaises(model.ContrastError):model.bind_source(src)

    def test_payload_identifiers_family_extra_keys_and_safe_integers(self):
        src,payload=fresh();bound=model.bind_source(src)
        for key,value in [('dataset_version_id','99999999-9999-4999-8999-999999999999'),('sample_design_version_id','99999999-9999-4999-8999-999999999999'),('quant_family','LFQ intensity'),('schemaVersion',True),('schemaVersion','1'),('schemaVersion',1.5),('schemaVersion',float('nan')),('schemaVersion',float('inf')),('schemaVersion',2**53),('project_id',src['project_id'])]:
            value_payload=deepcopy(payload);value_payload[key]=value
            with self.assertRaises(model.ContrastError):model.review_draft(value_payload,bound)
        payload['schemaVersion']=1.0;src['sample_design_record']['revision']=1.0
        for c in src['sample_columns']:c['ordinal']=float(c['ordinal'])
        for r in src['sample_design_record']['design']['rows']:r['column_ordinal']=float(r['column_ordinal'])
        src['sample_design_record']['design']['schemaVersion']=1.0
        result=model.review_draft(payload,model.bind_source(src))
        self.assertIs(type(result.draft.to_payload()['schemaVersion']),int)
        self.assertEqual(result.state,'ready_for_method_selection')

    def test_source_defensive_snapshot_and_fingerprint_covers_every_top_context(self):
        src,payload=fresh();bound=model.bind_source(src);before=bound.binding
        src['sample_design_record']['design']['rows'][0]['condition']='different';seal(src)
        changed=model.bind_source(src)
        self.assertNotEqual(before.source_sha256,changed.binding.source_sha256)
        self.assertEqual(bound.sample_review.design.rows[0].condition,'synthetic fixture only')
        self.assertNotEqual(before.record_sha256,changed.binding.record_sha256)
        src,_=fresh();src['project_id']='99999999-9999-4999-8999-999999999999'
        self.assertNotEqual(before.source_sha256,model.bind_source(src).binding.source_sha256)
        with self.assertRaises(FrozenInstanceError):bound.binding.project_id='forged'


class DraftTests(unittest.TestCase):
    def test_direction_reverse_distinct_same_and_duplicate_ordered_pair_invalid(self):
        src,payload=fresh();bound=model.bind_source(src);a=payload['contrasts'][0]
        reverse={'contrast_id':'66666666-6666-4666-8666-666666666666','numerator_group':a['denominator_group'],'denominator_group':a['numerator_group']}
        payload['contrasts'].append(reverse)
        self.assertEqual(model.review_draft(payload,bound).state,'ready_for_method_selection')
        payload['contrasts'].append(dict(a,contrast_id='77777777-7777-4777-8777-777777777777'))
        with self.assertRaises(model.ContrastError) as error:model.review_draft(payload,bound)
        self.assertEqual(error.exception.code,'duplicate_ordered_pair')
        payload['contrasts']=[dict(a,denominator_group=a['numerator_group'])]
        with self.assertRaises(model.ContrastError) as error:model.review_draft(payload,bound)
        self.assertEqual(error.exception.code,'same_group')

    def test_contrast_ids_schema_and_32_limit_including_incomplete_rows(self):
        src,payload=fresh();bound=model.bind_source(src)
        for bad in [dict(payload['contrasts'][0],contrast_id='invalid'),dict(payload['contrasts'][0],contrast_id='AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA'),dict(payload['contrasts'][0],direction='forged')]:
            value=deepcopy(payload);value['contrasts']=[bad]
            with self.assertRaises(model.ContrastError):model.review_draft(value,bound)
        value=deepcopy(payload);value['contrasts']*=2
        with self.assertRaises(model.ContrastError) as error:model.review_draft(value,bound)
        self.assertEqual(error.exception.code,'duplicate_contrast_id')
        payload['contrasts']=[{'contrast_id':str(UUID(int=i+1)),'numerator_group':'','denominator_group':''} for i in range(32)]
        result=model.review_draft(payload,bound);self.assertEqual(len(result.draft.contrasts),32);self.assertEqual(result.state,'draft_incomplete')
        payload['contrasts'].append({'contrast_id':str(UUID(int=33)),'numerator_group':'','denominator_group':''})
        with self.assertRaises(model.ContrastError):model.review_draft(payload,bound)

    def test_clarification_partial_rows_never_ignored_and_source_issues_still_visible(self):
        src,payload=fresh()
        for row in src['sample_design_record']['design']['rows']:row['confirmed']=False
        seal(src);bound=model.bind_source(src)
        self.assertEqual(model.review_draft(payload,bound).state,'blocked_by_sample_design')
        payload['contrasts'].append({'contrast_id':'66666666-6666-4666-8666-666666666666','numerator_group':'','denominator_group':'control-synthetic'})
        result=model.review_draft(payload,bound);self.assertEqual(result.state,'draft_incomplete')
        self.assertIn('unconfirmed_rows',[i.code for i in result.issues]);self.assertIn('contrasts_incomplete',[i.code for i in result.issues])
        payload['contrasts']=[];self.assertEqual(model.review_draft(payload,bound).state,'draft_incomplete')

    def test_clarification_edge_whitespace_group_exact_draft_choice_blank_invalid(self):
        src=row_source([' A ','B'],confirmed=False);payload=fresh()[1]
        payload['contrasts'][0].update(numerator_group=' A ',denominator_group='B')
        result=model.review_draft(payload,model.bind_source(src));self.assertEqual(result.state,'blocked_by_sample_design')
        self.assertEqual(result.draft.contrasts[0].numerator_group,' A ')
        for choice in ('A',' ','\ufeff','\ud800','x\n','x'*81):
            bad=deepcopy(payload);bad['contrasts'][0]['numerator_group']=choice
            with self.assertRaises(model.ContrastError):model.review_draft(bad,model.bind_source(src))

    def test_first_occurrence_group_order_and_no_fixed_groups_or_minimum_repeats(self):
        src=row_source(['z arbitrary','a arbitrary']);payload=fresh()[1]
        payload['contrasts'][0].update(numerator_group='a arbitrary',denominator_group='z arbitrary')
        result=model.review_draft(payload,model.bind_source(src))
        self.assertEqual([g.group for g in result.groups],['z arbitrary','a arbitrary'])
        self.assertEqual([g.total_columns for g in result.groups],[1,1])
        self.assertEqual(result.state,'ready_for_method_selection');self.assertFalse(result.analysis_ready)
        self.assertNotIn('effective_n',result.to_payload())

    def test_unicode_scalar_group_codepoint_boundary_preserves_exact_text(self):
        group='\U0001f600'*80;src=row_source([group,'other']);payload=fresh()[1]
        payload['contrasts'][0].update(numerator_group=group,denominator_group='other')
        result=model.review_draft(payload,model.bind_source(src))
        self.assertEqual(result.state,'ready_for_method_selection');self.assertEqual(result.groups[0].group,group)
        for bad in (group+'\U0001f600','\ud800','\udfff','other\u0085'):
            value=deepcopy(payload);value['contrasts'][0]['numerator_group']=bad
            with self.assertRaises(model.ContrastError):model.review_draft(value,model.bind_source(src))

    def test_technical_mixed_unknown_duplicate_replicates_and_control_conflicts_block(self):
        for kind,expected in [('technical','technical_replicates_require_policy'),('mixed','mixed_replicate_types'),('unknown','unknown_replicate_type'),('duplicate','duplicate_replicate_labels'),('control','included_group_control_inconsistent')]:
            src,payload=fresh();rows=src['sample_design_record']['design']['rows']
            if kind=='technical':
                for row in rows[:3]:row['replicate_type']='technical'
            if kind=='mixed':rows[0]['replicate_type']='technical'
            if kind=='unknown':
                for row in rows[:3]:row.update(replicate_type='unknown',confirmed=False)
            if kind=='duplicate':rows[0]['replicate']=rows[1]['replicate']
            if kind=='control':rows[0]['control']='no'
            seal(src);result=model.review_draft(payload,model.bind_source(src))
            self.assertEqual(result.state,'blocked_by_sample_design');self.assertIn(expected,[i.code for i in result.issues]);self.assertFalse(result.analysis_ready)

    def test_single_unconfirmed_saved_source_is_valid_draft_not_analysis_ready(self):
        src=row_source(['arbitrary single'],confirmed=False);payload=fresh()[1];payload['contrasts']=[]
        result=model.review_draft(payload,model.bind_source(src));self.assertEqual(result.state,'draft_incomplete')
        self.assertEqual((result.groups[0].total_columns,result.groups[0].confirmed_columns),(1,0))
        self.assertFalse(result.analysis_ready);self.assertIn('unconfirmed_rows',[i.code for i in result.issues])


class LifecycleTests(unittest.TestCase):
    def test_new_source_context_or_mutation_clears_previous_draft_results(self):
        session=model.ReviewSession();self.assertEqual(session.view().state,'waiting_source')
        for mutation in ('dataset','family','version','project','content','descriptors'):
            src,payload=fresh();session.bind_source(src);session.review(payload);self.assertIsNotNone(session.export_draft())
            new=deepcopy(src)
            if mutation=='dataset':
                new['dataset_version_id']='99999999-9999-4999-8999-999999999999';new['sample_design_record']['design']['dataset_version_id']=new['dataset_version_id']
            if mutation=='family':
                new['quant_family']='LFQ intensity';new['sample_design_record']['design']['quant_family']='LFQ intensity'
                for c in new['sample_columns']:c['quant_family']='LFQ intensity';c['name_raw']='LFQ intensity '+c['sample_label_raw']
                for r in new['sample_design_record']['design']['rows']:r['name_raw']='LFQ intensity '+r['sample_label_raw']
            if mutation=='version':new['sample_design_record']['sample_design_version_id']='99999999-9999-4999-8999-999999999999'
            if mutation=='project':new['project_id']='99999999-9999-4999-8999-999999999999'
            if mutation=='content':new['sample_design_record']['design']['rows'][0]['condition']='changed'
            if mutation=='descriptors':new['sample_columns'].append({'ordinal':20,'name_raw':'LFQ intensity unused','sample_label_raw':'unused','quant_family':'LFQ intensity'})
            seal(new);old_hash=session.view().binding.source_sha256;session.bind_source(new)
            self.assertIsNone(session.export_draft());self.assertEqual(session.view().draft.contrasts,())
            self.assertNotEqual(session.view().binding.source_sha256,old_hash)
            if mutation in ('dataset','family','version'):
                with self.assertRaises(model.ContrastError):session.review(payload)
                self.assertEqual(session.view().state,'invalid');self.assertIsNone(session.export_draft())

    def test_invalid_source_or_payload_clears_old_result_keeps_source_issues_and_disposes(self):
        src,payload=fresh();s=model.ReviewSession();s.bind_source(src);s.review(payload)
        invalid=deepcopy(src);invalid['sample_design_record']['design_sha256']='0'*64
        with self.assertRaises(model.ContrastError):s.bind_source(invalid)
        self.assertEqual(s.view().state,'invalid');self.assertEqual(s.view().groups,());self.assertIsNone(s.export_draft())
        self.assertEqual(s.review(payload).state,'invalid')
        src['sample_design_record']['design']['rows'][0]['confirmed']=False;seal(src);s.bind_source(src)
        wrong=deepcopy(payload);wrong['contrasts'][0]['numerator_group']='not present'
        with self.assertRaises(model.ContrastError):s.review(wrong)
        self.assertEqual(s.view().state,'invalid');self.assertIn('unconfirmed_rows',[i.code for i in s.view().issues])
        self.assertIsNone(s.export_draft());s.bind_source(None);self.assertEqual(s.view().state,'waiting_source')
        s.dispose();self.assertEqual(s.view().state,'disposed');self.assertFalse(s.view().analysis_ready)
        with self.assertRaises(model.ContrastError):s.bind_source(src)

    def test_inert_import_and_error_messages_do_not_echo_manual_values(self):
        body=compile((HERE.parent/'cyomics_projects'/'contrast_design.py').read_bytes(),'contrast_design.py','exec');m=ModuleType('contrast_inert');m.__package__='cyomics_projects';sys.modules[m.__name__]=m
        with patch('builtins.open',forbidden),patch('io.open',forbidden),patch('os.open',forbidden),patch('os.readlink',forbidden),patch('socket.socket',forbidden),patch('subprocess.Popen',forbidden),patch('threading.Thread.start',forbidden):exec(body,m.__dict__)
        src,payload=fresh();payload['contrasts'][0]['numerator_group']='PRIVATE_SENTINEL'
        with self.assertRaises(model.ContrastError) as error:model.review_draft(payload,model.bind_source(src))
        self.assertNotIn('PRIVATE_SENTINEL',str(error.exception))


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    print(json.dumps({'contract':model.CONTRACT,'tests':result.testsRun,'guard_hits':len(HITS),'HTTP_DB_Auth_R_resources_jobs':0}))
    raise SystemExit(0 if result.wasSuccessful() and not HITS else 1)
