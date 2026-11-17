"""Synthetic packaging, disabled configuration and existing selector boundaries."""
import hashlib
import importlib
import sys
import unittest
from unittest.mock import patch

from cyomics_projects import resource_selector as selector
from cyomics_projects.sequence import domain_runner as domain
from cyomics_projects.sequence import structure_search as structure

UUID = 'b8656fc5-e7e7-4df2-bbd0-c956571b42d8'
SHA = hashlib.sha256(b'AA').hexdigest()


class PackageBoundary(unittest.TestCase):
    def test_import_does_not_call_runtime_io(self):
        def forbidden(*a, **kw):
            raise AssertionError('runtime I/O during import')
        with patch('builtins.open', forbidden), patch('pathlib.Path.open', forbidden), patch('socket.socket', forbidden), patch('subprocess.Popen', forbidden):
            for module in (domain, structure):
                name = 'cyomics_projects.sequence._probe_' + module.__name__.rsplit('.', 1)[-1]
                spec = importlib.util.spec_from_file_location(name, module.__file__)
                fresh = importlib.util.module_from_spec(spec)
                sys.modules[name] = fresh
                try:
                    spec.loader.exec_module(fresh)
                finally:
                    del sys.modules[name]

    def test_domain_default_is_explicitly_unconfigured(self):
        self.assertIsNone(domain.ROOT)
        sequence = domain.bind_sequence('SYNTHETIC', 'AA', SHA)
        for resource, tool in [('swissprot-reviewed', 'blastp'), ('pfam-raw-reviewed', 'hmmsearch')]:
            self.assertIsNone(domain.CONFIG[resource]['path'])
            request = {'database_key': resource, 'tool_key': tool, 'limits': {
                'threads': 1, 'wall_seconds': 60, 'memory_bytes': 1024, 'output_bytes': 1024}}
            context = {'run_id': UUID, 'project_id': None, 'dataset_version_id': None, 'taxonomy_id': None}
            with self.assertRaisesRegex(ValueError, 'unconfigured'):
                domain.make_plan(request, sequence, context)

    def make_selection(self, capability='blast.protein_homology', local=()):
        return selector.select_job({'job_id': UUID, 'capabilities': (capability,)},
            selector.InputIdentity(UUID, ('SYNTHETIC',), (('SYNTHETIC', SHA),)),
            selector.ServerProjectContext(UUID, False, '2026-11-18T00:00:00Z'), local)

    def test_uses_existing_reviewed_domain_keys_only(self):
        for capability, resource in [('blast.protein_homology', 'swissprot-reviewed'), ('pfam.domains', 'pfam-raw-reviewed')]:
            record = selector.LocalResource(capability, resource, 'usable', 'synthetic.evidence',
                checks=(True,) * 5, species_scope='any')
            plan = self.make_selection(capability, (record,))
            self.assertEqual(selector.domain_runner_resource_key(plan, capability), resource)
            self.assertFalse(plan.execution_enabled)
            self.assertEqual(plan.external_calls, 0)

    def test_unknown_resource_is_not_missing_or_fallback(self):
        for capability in ['blast.protein_homology', 'pfam.domains', 'pdb.sequence_search']:
            selection = self.make_selection(capability).selections[0]
            self.assertEqual(selection.provider, 'needs_verification')
            self.assertEqual(selection.reason, 'local_unverified')
            self.assertIsNone(selection.resource_key)

    def test_no_hit_preserves_locked_local_source(self):
        capability = 'blast.protein_homology'
        local = selector.LocalResource(capability, 'swissprot-reviewed', 'usable', 'synthetic.evidence',
            checks=(True,) * 5, species_scope='any')
        plan = self.make_selection(capability, (local,))
        result = selector.observe_result(plan, job_id=UUID, capability_key=capability, provider='local', result_state='no_hit')
        self.assertEqual(result.result_state, 'no_hit')
        self.assertFalse(result.provider_changed)
        self.assertEqual(result.external_calls, 0)

    def test_structure_stays_disabled_and_is_not_prediction(self):
        query = structure.Query('SYNTHETIC', 'AA', SHA, UUID, UUID)
        result = structure.Adapter().search(query)
        self.assertEqual(result['status'], 'disabled')
        self.assertFalse(result['transport_invoked'])
        self.assertFalse(result['authorization_verified'])
        self.assertEqual(result['candidates'], [])
        with self.assertRaises(ValueError):
            selector.domain_runner_resource_key(self.make_selection('pdb.sequence_search'), 'pdb.sequence_search')
