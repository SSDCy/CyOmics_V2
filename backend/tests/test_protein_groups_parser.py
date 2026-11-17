"""Synthetic-only TXT core tests. No files, credentials, database or real data."""
from dataclasses import FrozenInstanceError, replace
import hashlib
import unittest

from cyomics_projects.import_report import ImportFailure, SourceRef
from cyomics_projects.protein_groups_parser import ParserLimits, parse_protein_groups, utf16_units


def source(raw, **changes):
    values = dict(project_id='synthetic-project', raw_file_id='synthetic-file',
                  raw_version_key='synthetic-version', expected_bytes=max(1, len(raw)),
                  expected_raw_sha256=hashlib.sha256(raw).hexdigest())
    values.update(changes)
    return SourceRef(**values)


def parse(raw, chunk_size=None, **options):
    chunks = [raw] if chunk_size is None else (raw[i:i + chunk_size] for i in range(0, len(raw), chunk_size))
    return parse_protein_groups(source(raw), chunks, **options)


class ProteinGroupsParserTests(unittest.TestCase):
    def assert_failure(self, outcome, code, limit_key=None):
        self.assertFalse(outcome.ok)
        self.assertIsNone(outcome.parsed)
        self.assertEqual(outcome.report.status, 'failed')
        self.assertEqual(len(outcome.report.issues), 1)
        issue = outcome.report.issues[0]
        self.assertEqual(issue.code, code)
        self.assertEqual(issue.limit_key, limit_key)
        self.assertIsNone(outcome.report.row_count)
        self.assertIsNone(outcome.report.association_counts)
        self.assertIsNone(outcome.report.dataset_version_id)
        self.assertFalse(outcome.report.persisted)
        self.assertEqual(outcome.report.scientific_analysis_status, 'not_started')

    def test_original_values_order_duplicate_nonkey_headers_and_variable_samples(self):
        raw = (b'Protein IDs\tIntensity a\tLFQ intensity z\tIntensity\tmeta\tmeta\tLFQ intensity '\
               b'\n A ; A ;; B \t0\t\t99\tNA\t 0 \t0.0\n ; \t \tNA\t5\t\tX\t0')
        outcome = parse(raw)
        self.assertTrue(outcome.ok)
        table = outcome.parsed
        self.assertEqual(tuple(c.name_raw for c in table.columns),
                         ('Protein IDs', 'Intensity a', 'LFQ intensity z', 'Intensity', 'meta', 'meta', 'LFQ intensity '))
        self.assertEqual(table.rows[0].cells, (' A ; A ;; B ', '0', '', '99', 'NA', ' 0 ', '0.0'))
        self.assertEqual([(m.segment_ordinal, m.token) for m in table.rows[0].members], [(0, 'A'), (1, 'A'), (3, 'B')])
        self.assertEqual(len(table.rows), 2)  # Groups remain quantitative rows.
        self.assertEqual(table.rows[1].members, ())
        self.assertEqual([c.quant_family for c in table.columns], [None, 'Intensity', 'LFQ intensity', None, None, None, 'LFQ intensity'])
        self.assertEqual([c.sample_label_raw for c in table.columns], [None, 'a', 'z', None, None, None, ''])
        self.assertEqual((outcome.report.intensity_sample_count, outcome.report.lfq_sample_count), (1, 2))
        self.assertEqual((outcome.report.quant_exact_zero_string_cells, outcome.report.quant_empty_string_cells), (2, 1))
        self.assertEqual((outcome.report.member_occurrences, outcome.report.empty_group_count), (3, 1))
        self.assertEqual(outcome.report.association_status, 'not_requested')
        self.assertFalse(hasattr(outcome.report, 'stored'))
        self.assertFalse(hasattr(outcome.report, 'authorized'))
        with self.assertRaises(FrozenInstanceError):
            table.rows[0].record_number = 99

    def test_quoted_tabs_escaped_quotes_multiline_crlf_and_exact_raw_spans(self):
        header = b'\xef\xbb\xbfProtein IDs\tmeta\r\n'
        row = 'A\t"one\t\u03b1\r\ntwo ""quoted"""'.encode('utf8')
        raw = header + row + b'\r\nB\tlast'
        outcome = parse(raw, chunk_size=1)
        self.assertTrue(outcome.ok)
        table = outcome.parsed
        self.assertEqual(table.rows[0].cells, ('A', 'one\t\u03b1\r\ntwo "quoted"'))
        first, last = table.rows
        self.assertEqual((first.record_number, last.record_number), (1, 2))
        self.assertEqual((first.source_span.start_line, first.source_span.end_line), (2, 3))
        self.assertEqual((last.source_span.start_line, last.source_span.end_line), (4, 4))
        self.assertEqual(raw[first.source_span.start_byte:first.source_span.end_byte], row)
        self.assertEqual(raw[table.header_span.start_byte:table.header_span.end_byte], header[:-2])
        self.assertEqual(raw[last.source_span.start_byte:last.source_span.end_byte], b'B\tlast')
        self.assertTrue(outcome.report.leading_bom)
        self.assertEqual((outcome.report.crlf_count, outcome.report.bare_cr_count, outcome.report.bare_lf_count), (3, 0, 0))
        self.assertEqual(table.raw_sha256, hashlib.sha256(raw).hexdigest())

    def test_chunk_invariance_and_all_three_record_separators(self):
        raw = 'Protein IDs\t\U0001f600\rA\tx\nB\ty\r\nC\tz\n'.encode('utf8')
        expected = parse(raw)
        self.assertTrue(expected.ok)
        for size in (1, 2, 3, 7, 31):
            with self.subTest(size=size):
                self.assertEqual(parse(raw, chunk_size=size), expected)
        self.assertEqual((expected.report.crlf_count, expected.report.bare_cr_count, expected.report.bare_lf_count), (1, 1, 2))
        self.assertEqual([r.source_span.start_line for r in expected.parsed.rows], [2, 3, 4])

    def test_bom_policy_one_only_and_interior_is_data(self):
        plain = b'Protein IDs\tx\nA\tB'
        for prefix in (b'', b'\xef\xbb\xbf'):
            outcome = parse(prefix + plain, chunk_size=1)
            self.assertTrue(outcome.ok)
            self.assertEqual(outcome.parsed.columns[0].name_raw, 'Protein IDs')
        # Deliberately one BOM only: a browser TextDecoder + parser combination
        # may consume two. This test does not claim equivalence for that input.
        self.assert_failure(parse(b'\xef\xbb\xbf\xef\xbb\xbf' + plain), 'TXT_PROTEIN_IDS_HEADER_COUNT')
        inside = parse('Protein IDs\tx\nA\t\ufeffB'.encode())
        self.assertTrue(inside.ok)
        self.assertEqual(inside.parsed.rows[0].cells[1], '\ufeffB')

    def test_member_trim_is_explicit_ecmascript_not_python_whitespace(self):
        raw = 'Protein IDs\tx\n\ufeff A \u00a0;\u001cB\u001c; a ;REV__A;sp|X|Y\t0'.encode()
        outcome = parse(raw)
        self.assertTrue(outcome.ok)
        self.assertEqual([m.token for m in outcome.parsed.rows[0].members], ['A', '\u001cB\u001c', 'a', 'REV__A', 'sp|X|Y'])

    def test_utf16_budgets_use_two_units_for_astral_characters(self):
        self.assertEqual(utf16_units('x\U0001f600'), 3)
        raw = 'Protein IDs\tx\n\U0001f600\t0'.encode()
        self.assertTrue(parse(raw, limits=ParserLimits(max_id_units=4)).ok)
        self.assert_failure(parse(raw, limits=ParserLimits(max_id_units=3)), 'RESOURCE_LIMIT', 'max_id_units')
        field = ('Protein IDs\tx\nA\t' + '\U0001f600' * 6).encode()
        self.assertTrue(parse(field, limits=ParserLimits(max_field_units=12)).ok)
        self.assert_failure(parse(field, limits=ParserLimits(max_field_units=11)), 'RESOURCE_LIMIT', 'max_field_units')

    def test_resource_edges_and_exact_limit_keys(self):
        raw = b'Protein IDs\tIntensity s\nA;A\t0\nB\t1'
        limits = ParserLimits(max_bytes=len(raw), max_records=2, max_columns=2,
                              max_cells=4, max_field_units=11, max_member_occurrences=3, max_id_units=7)
        self.assertTrue(parse(raw, limits=limits).ok)
        reductions = {'max_bytes':len(raw)-1, 'max_records':1, 'max_columns':1,
                      'max_cells':3, 'max_field_units':10, 'max_member_occurrences':2, 'max_id_units':6}
        for key, value in reductions.items():
            with self.subTest(key=key):
                self.assert_failure(parse(raw, limits=replace(limits, **{key:value})), 'RESOURCE_LIMIT', key)
        for field in ('max_bytes', 'max_records', 'max_columns', 'max_cells', 'max_field_units', 'max_member_occurrences', 'max_id_units'):
            with self.subTest(hard=field):
                for value in (0, True, getattr(ParserLimits(), field) + 1):
                    with self.assertRaises(ImportFailure) as caught:
                        ParserLimits(**{field:value})
                    self.assertEqual(str(caught.exception), 'INVALID_LIMITS')
                    self.assertEqual(caught.exception.issue.limit_key, field)

    def test_strict_header_width_blank_record_and_quote_failures_publish_no_rows(self):
        cases = [(b'Protein IDs\tx\nA\t0\nB', 'TXT_ROW_WIDTH'),
                 (b'Protein IDs\tx\nA\t0\n\n', 'TXT_ROW_WIDTH'),
                 (b'Protein IDs\tx\nA\t0\nB\t"tail', 'TXT_QUOTE_SYNTAX'),
                 (b'Protein IDs\tx\nA\t0\nB\t"x"z', 'TXT_QUOTE_SYNTAX'),
                 (b'Protein IDs\tProtein IDs\nA\tB', 'TXT_PROTEIN_IDS_HEADER_COUNT'),
                 (b'other\tx\nA\t0', 'TXT_PROTEIN_IDS_HEADER_COUNT'),
                 (b'Protein IDs\tx', 'TXT_HEADER_REQUIRED'),
                 (b'\xef\xbb\xbf', 'EMPTY_FILE')]
        for raw, code in cases:
            with self.subTest(code=code):self.assert_failure(parse(raw, chunk_size=1), code)
        outcome = parse(b'Protein IDs\tx\nA\t0\nB')
        issue = outcome.report.issues[0]
        self.assertEqual((issue.record_number, issue.line), (2, 3))

    def test_unquoted_interior_quote_is_literal_and_quoted_empty_fields_are_preserved(self):
        outcome = parse(b'Protein IDs\tx\nA\tno"rewrite\nB\t""')
        self.assertTrue(outcome.ok)
        self.assertEqual([r.cells[1] for r in outcome.parsed.rows], ['no"rewrite', ''])

    def test_invalid_utf8_nul_and_stream_failure_are_fixed_safe_errors(self):
        for raw, code in [(b'Protein IDs\tx\nA\t\xff', 'INVALID_UTF8'),
                          (b'Protein IDs\tx\nA\t\xc3', 'INVALID_UTF8'),
                          (b'Protein IDs\tx\nA\t\0', 'NUL_BYTE')]:
            with self.subTest(code=code):self.assert_failure(parse(raw, chunk_size=1), code)
        raw = b'Protein IDs\tx\nA\t0'
        def broken():
            yield raw
            raise OSError('synthetic-private-path-or-value-never-echo')
        outcome = parse_protein_groups(source(raw), broken())
        self.assert_failure(outcome, 'INPUT_READ_FAILED')
        self.assertNotIn('synthetic-private', repr(outcome))
        self.assertIsNone(outcome.report.observed_raw_sha256)  # Not a full-file digest.
        self.assert_failure(parse_protein_groups(source(raw), [raw, 'bad chunk']), 'INVALID_CHUNK')

    def test_integrity_checked_on_complete_original_bytes_and_not_decoded_cells(self):
        raw = b'\xef\xbb\xbfProtein IDs\tx\r\nA\t0\r\n'
        for ref in (source(raw, expected_raw_sha256='0'*64), source(raw, expected_bytes=len(raw)+1)):
            outcome = parse_protein_groups(ref, [raw])
            self.assert_failure(outcome, 'SOURCE_INTEGRITY_MISMATCH')
            self.assertTrue(outcome.report.source_complete)
            self.assertEqual(outcome.report.observed_raw_sha256, hashlib.sha256(raw).hexdigest())
        self.assertNotEqual(hashlib.sha256(raw).hexdigest(), hashlib.sha256(raw.decode('utf-8-sig').replace('\r\n','\n').encode()).hexdigest())

    def test_cooperative_cancellation_before_producer_and_after_provisional_row(self):
        raw = b'Protein IDs\tx\nA\t0\nB\t1'
        calls=[]
        def untouched():
            calls.append('producer');yield raw
        self.assert_failure(parse_protein_groups(source(raw), untouched(), cancelled=lambda:True), 'CANCELLED')
        self.assertEqual(calls, [])
        cancelled=[False]
        def changes():
            yield b'Protein IDs\tx\nA\t0\n'
            cancelled[0]=True
            yield b'B\t1'
        self.assert_failure(parse_protein_groups(source(raw), changes(), cancelled=lambda:cancelled[0]), 'CANCELLED')
        def bad_control():raise RuntimeError('synthetic-hidden-control-text')
        self.assert_failure(parse(raw, cancelled=bad_control), 'CONTROL_FAILED')
        self.assert_failure(parse(raw, cancelled=lambda:1), 'CONTROL_FAILED')

    def test_source_is_metadata_only_and_invalid_contract_has_no_input_echo(self):
        raw=b'Protein IDs\tx\nA\t0'
        self.assert_failure(parse_protein_groups({'authorized':True}, [raw]), 'INVALID_SOURCE_REF')
        self.assert_failure(parse(raw, limits={}), 'INVALID_LIMITS')
        self.assert_failure(parse(raw, cancelled=True), 'INVALID_CONTROL')
        for change in ({'raw_version_key':'https://synthetic.invalid/input'}, {'role':'sequence_library'},
                       {'format':'fasta'}, {'expected_bytes':True}, {'expected_raw_sha256':'bad'}):
            with self.subTest(change=next(iter(change))):
                with self.assertRaises(ImportFailure) as caught:source(raw, **change)
                self.assertEqual(str(caught.exception), 'INVALID_SOURCE_REF')


if __name__ == '__main__':
    unittest.main()
