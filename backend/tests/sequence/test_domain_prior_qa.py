"""Four bounded independent probes using synthetic metadata and engine output."""
from copy import deepcopy
from dataclasses import replace
import unittest
from cyomics_projects.sequence.domain_runner import bind_sequence,make_plan,adapt_result,sha
from sequence.test_domain_runner import fixture,BLAST,HMM_ROW,hmm

from sequence.test_domain_runner import configure_synthetic_paths

class Independent(unittest.TestCase):
    def setUp(self):
        configure_synthetic_paths(self)

    def test_client_injection_cannot_reach_fixed_argv_or_paths(self):
        request,ctx,plan,_,_=fixture()
        for injected in ('swissprot-reviewed;echo BAD','$(id)','../swissprot-reviewed','--help','/tmp/client-db'):
            with self.subTest(injected=injected),self.assertRaises(ValueError):
                make_plan({**request,'database_key':injected},plan.sequence,ctx)
        for field in ('binary','cwd','output_path','extra_args'):
            with self.subTest(field=field),self.assertRaises(ValueError):
                make_plan({**request,field:'injected'},plan.sequence,ctx)
        self.assertEqual(plan.argv[0],'/usr/local/bin/blastp')
        self.assertEqual(plan.argv[plan.argv.index('-query')+1],plan.cwd+'/query.fasta')
        self.assertFalse(plan.execution_enabled)

    def test_all_resource_lower_upper_and_type_bounds_reject(self):
        request,ctx,plan,_,_=fixture()
        maximum={'threads':1,'wall_seconds':60,'memory_bytes':2*1024**3,'output_bytes':1024**2}
        for key,cap in maximum.items():
            for bad in (-1,0,cap+1,True,'1',1.5):
                req=deepcopy(request);req['limits'][key]=bad
                with self.subTest(key=key,bad=bad),self.assertRaises(ValueError):make_plan(req,plan.sequence,ctx)

    def test_output_identity_and_each_hash_binding_are_required(self):
        for sid in ('q\n>forged','../q','--flag','q;echo BAD'):
            with self.assertRaises(ValueError):bind_sequence(sid,'AA',sha(b'AA'))
        _,_,plan,sidecar,raw=fixture()
        for field in ('plan_sha256','database_snapshot_sha256','output_sha256','sequence_sha256','query_fasta_sha256'):
            altered={**sidecar,field:'0'*64}
            with self.subTest(field=field),self.assertRaises(ValueError):adapt_result(raw,plan,altered)
        mismatch=raw.replace(b'SYNTHETIC_Q',b'OTHER_TOKEN')
        _,_,p,s,_=fixture(raw=mismatch)
        with self.assertRaisesRegex(ValueError,'identity'):adapt_result(mismatch,p,s)

    def test_hmmer_orientation_footer_version_and_complete_blast_row_attestation(self):
        for raw in (hmm().replace(b'# Program: hmmsearch',b'# Program: hmmscan'),hmm().replace(b'# Version: 3.3.2',b'# Version: 3.4'),hmm(complete=False),hmm((HMM_ROW.replace('SYNTHETIC_Q - 100 Test_model PF00001.9 50','Test_model PF00001.9 50 SYNTHETIC_Q - 100'),))):
            _,_,plan,sidecar,_=fixture('hmmsearch',raw)
            with self.subTest(raw=raw),self.assertRaises(ValueError):adapt_result(raw,plan,sidecar)
        _,_,plan,sidecar,raw=fixture(raw=BLAST)
        for change in ({'truncated':True},{'completed':False},{'exit_code':1}):
            with self.subTest(change=change),self.assertRaises(ValueError):adapt_result(raw,plan,{**sidecar,**change})
        accepted=adapt_result(raw,plan,sidecar)
        self.assertIsNone(accepted['provenance']['database_release'])
        self.assertEqual(accepted['biological_validity'],'not_established')
        self.assertFalse(accepted['platform_analysis_ready'])

if __name__=='__main__':unittest.main(verbosity=2)
