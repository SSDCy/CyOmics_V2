"""Four independent synthetic boundary probes, never structure retrieval."""
from copy import deepcopy
from dataclasses import replace
import unittest
from cyomics_projects.sequence.structure_search import Adapter, Parameters, Response, ENDPOINT, build_request
from sequence.test_structure_search import Q, BODY, FixtureTransport, run

class Independent(unittest.TestCase):
    def test_native_id_can_also_look_like_uniprot_without_accession_conversion(self):
        for original in ('Q9ZV06', 'Pt_native|isoform.2', 'native:with/slash'):
            query=replace(Q,original_id=original)
            request=build_request(query)
            self.assertEqual(request,build_request(Q))
            result=Adapter().search(query)
            self.assertEqual(result['original_id'],original)
            self.assertEqual(result['project_id'],Q.project_id)
            self.assertEqual(result['dataset_version_id'],Q.dataset_version_id)
            self.assertFalse(result['transport_invoked'])
            self.assertEqual(result['status'],'disabled')

    def test_duplicate_or_malformed_response_is_never_accepted_as_evidence(self):
        body=deepcopy(BODY);body['total_count']=2;body['result_set']*=2
        self.assertEqual(run(body)['status'],'error')
        for change in ({'score':float('nan')},{'services':None},{'services':[{'service_type':'sequence','nodes':[False]}]}):
            body=deepcopy(BODY);body['result_set'][0].update(change)
            result=run(body)
            self.assertEqual(result['status'],'error')
            self.assertEqual(result['candidates'],[])

    def test_sparse_candidates_keep_missing_scientific_facts_unknown(self):
        body=deepcopy(BODY);body['result_set'][0].pop('services')
        result=run(body)
        hit=result['candidates'][0]
        for name in ('identity','query_coverage','target_sequence_sha256','taxonomy_match_status','chain_label_asym_ids','chain_auth_asym_ids','database_version','engine_version'):
            self.assertEqual(hit[name],'unknown')
        self.assertFalse(hit['experimental_provenance_verified'])
        self.assertEqual(hit['target_identity_status'],'unverified_candidate')
        self.assertFalse(result['authorization_verified'])
        self.assertEqual(result['data_origin'],'synthetic_fixture')

    def test_no_hit_and_out_of_range_page_are_distinct_from_server_failure(self):
        nohit=run(None,204)
        self.assertEqual((nohit['status'],nohit['total_count'],nohit['more_results']),('no_hit',0,False))
        empty=deepcopy(BODY);empty['result_set']=[]
        paged=run(empty,params=replace(Parameters(),start=1))
        self.assertEqual((paged['status'],paged['total_count'],paged['more_results']),('no_hit',1,False))
        for code in (403,429,500,503):
            failed=run(None,code)
            self.assertEqual(failed['status'],'error')
            self.assertEqual(failed['candidates'],[])

if __name__=='__main__':unittest.main(verbosity=2)
