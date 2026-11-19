"""One finite synthetic assembly target; original repo methods are explicit doubles.

No Auth/session/PG/native source acceptance. Uses original pure small FASTA
parser/rebuild, never real Leaf/old mapping targets. Normal checkout portable.
"""
import asyncio, copy, csv, hashlib, importlib.util, io, json, sys, types, unittest
from pathlib import Path
from unittest.mock import patch
BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

def resolve(coro):
    """Finite doubles contain no external await; no event-loop socket required."""
    try:
        try:
            coro.send(None)
        except StopIteration as done:
            return done.value
        raise AssertionError('synthetic fixture unexpectedly suspended for I/O')
    finally:
        coro.close()
from cyomics_projects import dep_fasta_handoff as h
from cyomics_projects import fasta_intake_contract as f
from cyomics_projects.settings import Fault
from cyomics_projects.preprocess_repository import StoredArtifact
U = lambda n: f'00000000-0000-4000-8000-{n:012d}'
ACTOR, OTHER, PROJECT, DERIVED, MAPPING, SOURCE, RAW, FILE, RESULT, MEMBER, JOB, ATTEMPT, REQUEST, CONTRAST = [U(n) for n in range(1, 15)]
H = lambda b: hashlib.sha256(b).hexdigest()
C = h.canonical
PIN = h.pin

def tsv(header, rows):
    b = io.StringIO(newline='')
    w = csv.writer(b, delimiter='\t', lineterminator='\n')
    w.writerow(header)
    w.writerows(rows)
    return b.getvalue().encode()

class Budget:

    def __init__(self):
        self.calls = 0
        self.cancel = False
        self.timeout = False

    def check(self):
        self.calls += 1
        if self.cancel:
            raise asyncio.CancelledError()
        if self.timeout:
            raise TimeoutError()

class Fixture:

    def __init__(self):
        self.log = []
        self.budget = Budget()
        self.foreign = False
        raw = b'>dup original;$(not_a_command)\nacD\n>dup second same-sequence\nACD\n>bad stop-kept-invalid\nAC*\n>empty preserved\n>safe "quoted";header\nMNX\n'
        dto = dict(source_id=SOURCE, project_id=PROJECT, request_id=U(21), filename='synthetic.fasta', format='protein_fasta', bytes=len(raw), sha256=H(raw), status='stored', createdAt='2026-11-20T00:00:00.000000Z')
        self.source = f.source_from_json(C(dto), raw)
        candidate = f.build_candidate(self.source, expected_project_id=PROJECT, expected_source_id=SOURCE, control=f.ParseControl(checkpoint=self.budget.check), config_json=f.CONFIG_JSON)
        verified = f.rebuild_and_verify(self.source, candidate, expected_project_id=PROJECT, expected_source_id=SOURCE, expected_index_version_id=candidate.index_version_id, control=f.ParseControl(checkpoint=self.budget.check))
        self.stored = types.SimpleNamespace(source=self.source, candidate=candidate, verified=verified, dto=lambda: {'source': dto, 'index': f.index_dto(candidate, '2026-11-20T00:00:00.000000Z')})
        self.ids = {2: 'dup; missing;dup', 5: 'bad;empty;safe'}
        self.rowmap = tsv(h.ROWMAP, [(i, self.ids.get(i, 'other'), 'TRUE' if i in self.ids else 'FALSE', 1 if i == 2 else 2 if i == 5 else 'NA') for i in range(1, 6)])
        original_source = dict(row_count=5, column_count=8, config_sha256='a' * 64, report_sha256='b' * 64, source_bytes=1000, source_sha256='c' * 64, parser_contract='protein_groups_txt_v1')
        self.processed = dict(processed_dataset_version_id=U(22), created_by=ACTOR, project_id=PROJECT, parent_dataset_version_id=RAW, file_id=FILE, quant_family='LFQ intensity', source=original_source, parameters={'normalization': 'none', 'imputation': 'none'}, manifest_sha256='d' * 64, artifacts=[dict(name='row-map.tsv', media_type='text/tab-separated-values; charset=utf-8', **PIN(self.rowmap))])
        contrast = dict(contrast_id=CONTRAST, numerator_group='treatment', denominator_group='WT')
        self.parent = dict(created_by=ACTOR, project_id=PROJECT, result_id=RESULT, job_id=JOB, attempt_id=ATTEMPT, request_id=REQUEST, receipt_sha256='e' * 64, method={'name': 'original_limited_synthetic'}, manifest_sha256='f' * 64, artifacts=[], admission_binding_sha256='1' * 64, selection={'processed_dataset_version_id': U(22), 'contrast': {'dataset_version_id': RAW, 'quant_family': 'LFQ intensity', 'contrasts': [contrast]}})
        self.binding = dict(owner_id=ACTOR, project_id=PROJECT, source_result_id=RESULT, source_metadata_sha256=H(C(self.parent)), source_manifest_sha256='f' * 64, source_artifacts=[], source_receipt_sha256='e' * 64, source_job_id=JOB, source_attempt_id=ATTEMPT, source_request_id=REQUEST, source_method_sha256=H(C(self.parent['method'])), admission_binding_sha256='1' * 64, source_contrast_id=CONTRAST, contrast_direction='numerator_minus_denominator')
        rows = []
        for n, label in [(2, 'Up'), (5, 'Down')]:
            rows.append((n, self.ids[n], CONTRAST, 'testable', 'ok', '1' if n == 2 else '-1', '2' if n == 2 else '.5', 'ok', '1', '1', '2', '2', '.01', '.02', '3', '3', '3', '3', label, 'TRUE'))
        self.selected = tsv(h.HEADER, rows)
        self.member = dict(derived_version_id=DERIVED, member_set_id=MEMBER, member_count=2, selected_artifact_sha256=H(self.selected))
        self.meta = dict(derived_version_id=DERIVED, created_by=ACTOR, project_id=PROJECT, status='CLASSIFIED', DE_members_evaluated=True, member_set=self.member, counts={'selected_DE_rows': 2, 'Up': 1, 'Down': 1}, source_binding=self.binding, source_result_id=RESULT, source_manifest_sha256='f' * 64, manifest_sha256='2' * 64, parameters={'absolute_log2fc_min': '1', 'adjusted_p_max': '.05'}, parameters_sha256='3' * 64, artifacts=[dict(name='selected-DE.tsv', **PIN(self.selected))])
        q = dict(dataset_version_id=RAW, project_id=PROJECT, raw_file_id=FILE, raw_bytes=1000, raw_sha256='c' * 64, config_sha256='a' * 64, report_sha256='b' * 64, row_count=5, column_count=8, parser_contract='protein_groups_txt_v1')
        fb = dict(project_id=PROJECT, source_id=SOURCE, index_version_id=candidate.index_version_id, source_bytes=len(raw), source_sha256=H(raw), index_contract=candidate.index_contract, config_sha256=candidate.config_sha256, index_manifest_sha256=candidate.index_manifest_sha256)
        cat = {str(r.ordinal): dict(record_ordinal=r.ordinal, fasta_id=r.id, raw_sequence_sha256=r.raw_sequence_sha256, canonical_sequence_sha256=r.sequence_sha256, normalization_status=r.normalization_status, canonical_issue=r.canonical_issue) for r in verified.index.records}
        member = lambda seg, token, hits: dict(segment_ordinal=seg, token=token, fasta_record_ordinals=hits)
        self.mapping = dict(mapping_version_id=MAPPING, binding_sha256='4' * 64, inputs={'quantification': q, 'fasta': fb}, rows=[dict(record_number=i, protein_ids_raw=self.ids.get(i, 'other'), members=[member(0, 'dup', [1, 2]), member(1, 'missing', []), member(2, 'dup', [1, 2])] if i == 2 else [member(0, 'bad', [3]), member(1, 'empty', [4]), member(2, 'safe', [5])] if i == 5 else []) for i in range(1, 6)], fasta_records=cat)
        self.refresh_mapping()

        async def classread(actor, project, derived, budget):
            self.log.append('classification_owner')
            if actor != ACTOR or project != PROJECT:
                raise Fault(404, 'de_classification_version_not_found')
            assert derived == DERIVED
            return copy.deepcopy(self.meta)

        async def memberread(actor, project, member_id, budget):
            self.log.append('member')
            assert member_id == MEMBER
            return {'member_set': copy.deepcopy(self.member), 'bytes': self.selected}

        async def parentread(actor, project, budget, *, result_id):
            self.log.append('parent')
            assert result_id == RESULT
            return copy.deepcopy(self.parent)

        async def processedread(actor, project, version, budget):
            self.log.append('processed')
            assert version == U(22)
            return copy.deepcopy(self.processed)

        async def rowread(actor, project, version, name, budget):
            self.log.append('rowmap')
            assert name == 'row-map.tsv'
            return StoredArtifact('row-map.tsv', 'text/tab-separated-values; charset=utf-8', self.rowmap, H(self.rowmap))

        async def mapread(actor, project, identifier, budget):
            self.log.append('mapping')
            assert identifier == MAPPING
            return self.saved

        async def fastaread(actor, project, source, budget, *, config_json):
            self.log.append('fasta_owner_verified')
            assert (actor, project, source, config_json) == (ACTOR, PROJECT, SOURCE, f.CONFIG_JSON)
            return self.stored
        self.readers = h._Readers(types.SimpleNamespace(read_version=classread, read_member_set=memberread), types.SimpleNamespace(read=parentread), types.SimpleNamespace(read_version=processedread, read_artifact=rowread), types.SimpleNamespace(read=mapread), types.SimpleNamespace(read_verified_source=fastaread))

    def refresh_mapping(self):
        raw = C(self.mapping)
        self.saved = types.SimpleNamespace(content=raw, metadata=dict(mapping_version_id=MAPPING, created_by=ACTOR, project_id=PROJECT, inputs=self.mapping['inputs'], binding_sha256='4' * 64, result=PIN(raw)))

    async def run(self, subject=ACTOR):
        with patch.object(h.OwnerFastaHandoff, '_readers', return_value=self.readers):
            return await h.OwnerFastaHandoff(object(), h.Config(enabled=True)).assemble(subject, PROJECT, DERIVED, MAPPING, self.budget)

class HandoffFinite(unittest.TestCase):

    def test_owner_bindings_no_dedup_and_safe_queries(self):
        fixture = Fixture()
        with self.subTest('defaultoff_no_read'):
            with self.assertRaises(Fault):
                resolve(h.OwnerFastaHandoff().assemble(ACTOR, PROJECT, DERIVED, MAPPING, fixture.budget))
            self.assertEqual(fixture.log, [])
        with self.subTest('genuine_new_synthetic_parser_and_full_relationships'):
            result = resolve(fixture.run())
            descriptor = json.loads(result.descriptor_json)
            members = list(csv.DictReader(io.StringIO(result.membership_tsv.decode(), newline=''), delimiter='\t'))
            self.assertEqual(fixture.log, ['classification_owner', 'member', 'parent', 'processed', 'rowmap', 'mapping', 'fasta_owner_verified'])
            self.assertEqual(descriptor['dedup_policy'], h.POLICY)
            self.assertFalse(descriptor['annotation_ready'])
            self.assertIsNone(descriptor['taxonomy_id'])
            self.assertEqual(descriptor['counts'], dict(selected_groups=2, member_occurrences=6, relation_rows=8, query_records=5, unmatched_members=1, empty_groups=0, invalid_record_occurrences=2, duplicate_id_members=2, distinct_valid_fasta_records=3, distinct_valid_sequence_sha256=2, Up=1, Down=1))
            self.assertEqual(descriptor['status'], 'ready_with_unresolved')
            self.assertEqual([x['query_id'] for x in members if x['query_id']], ['q_r00002_m00000_f00001', 'q_r00002_m00000_f00002', 'q_r00002_m00002_f00001', 'q_r00002_m00002_f00002', 'q_r00005_m00002_f00005'])
            self.assertEqual([x['sequence_status'] for x in members], ['valid_canonical_residues', 'valid_canonical_residues', 'unmatched', 'valid_canonical_residues', 'valid_canonical_residues', 'stop_or_gap', 'empty_sequence', 'valid_canonical_residues'])
            self.assertEqual([x['raw_record_number'] for x in members], ['2'] * 5 + ['5'] * 3)
            self.assertIn('original;$(not_a_command)', result.membership_tsv.decode())
            self.assertNotIn(b'not_a_command', result.queries_fasta)
            self.assertEqual(result.queries_fasta.count(b'\nACD\n'), 4)
            self.assertNotIn(b'*', result.queries_fasta)
            self.assertTrue(result.queries_fasta.endswith(b'\n'))
            self.assertEqual(PIN(result.membership_tsv), {k: descriptor['outputs'][0][k] for k in ('bytes', 'sha256')})
        with self.subTest('deterministic_repeat_read_no_new_identity'):
            again = resolve(fixture.run())
            self.assertEqual(result, again)
        with self.subTest('foreign_owner_denied_before_source_IO'):
            before = len(fixture.log)
            with self.assertRaises(Fault) as got:
                resolve(fixture.run(OTHER))
            self.assertEqual(got.exception.status, 404)
            self.assertEqual(fixture.log[before:], ['classification_owner'])
        with self.subTest('wrong_mapping_source_refuses_before_fasta'):
            old = fixture.mapping['inputs']['quantification']['raw_sha256']
            fixture.mapping['inputs']['quantification']['raw_sha256'] = '9' * 64
            fixture.refresh_mapping()
            fixture.log = []
            with self.assertRaises(Fault):
                resolve(fixture.run())
            self.assertNotIn('fasta_owner_verified', fixture.log)
            fixture.mapping['inputs']['quantification']['raw_sha256'] = old
            fixture.refresh_mapping()
        with self.subTest('filtered_work_ordinal_is_not_raw_identity'):
            old = fixture.rowmap
            fixture.rowmap = old.replace(b'5\tbad;empty;safe', b'5\tother-id')
            fixture.processed['artifacts'][0].update(PIN(fixture.rowmap))
            fixture.log = []
            with self.assertRaises(Fault):
                resolve(fixture.run())
            self.assertNotIn('fasta_owner_verified', fixture.log)
            fixture.rowmap = old
            fixture.processed['artifacts'][0].update(PIN(old))
        with self.subTest('changed_fasta_source_hash_refuses'):
            old = fixture.mapping['inputs']['fasta']['source_sha256']
            fixture.mapping['inputs']['fasta']['source_sha256'] = '8' * 64
            fixture.refresh_mapping()
            with self.assertRaises(Fault):
                resolve(fixture.run())
            fixture.mapping['inputs']['fasta']['source_sha256'] = old
            fixture.refresh_mapping()
        with self.subTest('sequence_catalogue_mismatch_refuses_no_partial_return'):
            old = fixture.mapping['fasta_records']['1']['canonical_sequence_sha256']
            fixture.mapping['fasta_records']['1']['canonical_sequence_sha256'] = '7' * 64
            fixture.refresh_mapping()
            with self.assertRaises(Fault):
                resolve(fixture.run())
            fixture.mapping['fasta_records']['1']['canonical_sequence_sha256'] = old
            fixture.refresh_mapping()
        with self.subTest('member_set_bytes_changed_refuses'):
            old = fixture.selected
            fixture.selected = old + b'bad\n'
            with self.assertRaises(Fault):
                resolve(fixture.run())
            fixture.selected = old
        with self.subTest('timeout_and_cancellation_keep_original_types'):
            fixture.budget.timeout = True
            with self.assertRaises(TimeoutError):
                resolve(fixture.run())
            fixture.budget.timeout = False
            fixture.budget.cancel = True
            with self.assertRaises(asyncio.CancelledError):
                resolve(fixture.run())

    def test_output_cap_preserves_whole_bundle(self):
        fixture = Fixture()
        with self.subTest('output_cap_refuses_whole_bundle_no_truncation'):
            with patch.object(h, 'MAX_OUTPUT', len(fixture.saved.content) + 1):
                with self.assertRaises(Fault) as got:
                    resolve(fixture.run())
            self.assertEqual(got.exception.status, 413)
if __name__ == '__main__':
    unittest.main(verbosity=2)
