"""Offline synthetic DTO/state-transition checks, never auth or scientific proof."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import importlib.util
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

HERE = Path(__file__).resolve().parent
MODEL_PATH = HERE.parent / 'cyomics_projects' / 'sample_design.py'
sys.path.insert(0, str(MODEL_PATH.parent))
import sample_design as sd

VERSION = '11111111-1111-4111-8111-111111111111'
OTHER_VERSION = '22222222-2222-4222-8222-222222222222'
GUARD_HITS = []


def forbidden(*args, **kwargs):
    GUARD_HITS.append('side_effect')
    raise AssertionError('No runtime action in pure model test')


def audit(event, args):
    if event.startswith(('socket.', 'subprocess.')) or event in ('os.system', 'os.kill', 'os.killpg'):
        GUARD_HITS.append(event)
        raise AssertionError('No network/process operation')
    if event == 'open' and isinstance(args[0], (str, bytes)):
        value = str(args[0])
        if '/proc/' in value or '/candidate/' in value or '/.env' in value or '/cyomics/data/' in value:
            GUARD_HITS.append('private_read')
            raise AssertionError('No candidate/data/secret read')


sys.addaudithook(audit)


def columns(intensity=('alpha', 'beta'), lfq=('alpha', 'beta')):
    return [
        {'ordinal': 1, 'name_raw': 'Intensity ' + intensity[0], 'quant_family': 'Intensity', 'sample_label_raw': intensity[0]},
        {'ordinal': 2, 'name_raw': 'LFQ intensity ' + lfq[0], 'quant_family': 'LFQ intensity', 'sample_label_raw': lfq[0]},
        {'ordinal': 7, 'name_raw': 'Intensity ' + intensity[1], 'quant_family': 'Intensity', 'sample_label_raw': intensity[1]},
        {'ordinal': 11, 'name_raw': 'LFQ intensity ' + lfq[1], 'quant_family': 'LFQ intensity', 'sample_label_raw': lfq[1]},
    ]


def source(**options):
    return sd.bind_trusted_source(VERSION, columns(**options))


def payload(src=None, family='Intensity', confirmed=False):
    src = source() if src is None else src
    data = sd.new_draft(src, family).design.to_payload()
    if confirmed:
        for row in data['rows']:
            row.update(group='arbitrary group', condition='any condition', control='yes',
                       replicate='replicate 27', replicate_type='technical', confirmed=True)
    return data


class BindingTests(unittest.TestCase):
    def test_real_report_descriptor_shape_zero_based_gaps_and_alternative_family(self):
        src = source()
        intensity = sd.validate_design(payload(src), src)
        lfq = sd.validate_design(payload(src, 'LFQ intensity'), src)
        self.assertEqual([r.column_ordinal for r in intensity.design.rows], [1, 7])
        self.assertEqual([r.column_ordinal for r in lfq.design.rows], [2, 11])
        self.assertEqual(len(intensity.design.rows), 2)  # Do not double biological samples.
        zero = sd.bind_trusted_source(VERSION, [dict(columns()[0], ordinal=0)])
        self.assertEqual(sd.new_draft(zero, 'Intensity').design.rows[0].column_ordinal, 0)

    def test_extra_client_keys_and_time_fields_rejected(self):
        for key in ('owner_id', 'project_id', 'source', 'time_factor', 'time_value', 'time_unit'):
            data = payload(); data[key] = 'untrusted'
            with self.subTest(key=key), self.assertRaises(sd.DesignError):
                sd.validate_design(data, source())
        for key in ('quant_family', 'time_factor', 'time_value', 'time_unit', 'owner_id'):
            data = payload(); data['rows'][0][key] = 'untrusted'
            with self.subTest(key=key), self.assertRaises(sd.DesignError):
                sd.validate_design(data, source())

    def test_wrong_version_schema_and_missing_required_flags_rejected(self):
        for version in (OTHER_VERSION, 'ABCDEFAB-CDEF-4ABC-8DEF-ABCDEFABCDEF', '{' + VERSION + '}', 'not-a-uuid'):
            data = payload(); data['dataset_version_id'] = version
            with self.assertRaises(sd.DesignError): sd.validate_design(data, source())
        for version in (True, 0, 2, '1'):
            data = payload(); data['schemaVersion'] = version
            with self.assertRaises(sd.DesignError): sd.validate_design(data, source())
        data = payload(); del data['rows'][0]['confirmed']
        with self.assertRaises(sd.DesignError): sd.validate_design(data, source())

    def test_wrong_source_mixed_family_duplicate_and_missing_ordinal_rejected(self):
        cases = []
        for field, value in [('name_raw', 'Intensity other'), ('sample_label_raw', 'other'), ('column_ordinal', 2), ('column_ordinal', 0), ('column_ordinal', True)]:
            data = payload(); data['rows'][0][field] = value; cases.append(data)
        duplicate = payload(); duplicate['rows'][1] = deepcopy(duplicate['rows'][0]); cases.append(duplicate)
        missing = payload(); missing['rows'].pop(); cases.append(missing)
        extra = payload(); extra['rows'].append(deepcopy(extra['rows'][0])); cases.append(extra)
        for data in cases:
            with self.assertRaises(sd.DesignError): sd.validate_design(data, source())

    def test_v02_reordered_rows_are_rejected_not_sorted(self):
        src = source(); data = payload(src); data['rows'].reverse(); before = deepcopy(data)
        with self.assertRaises(sd.DesignError) as caught: sd.validate_design(data, src)
        self.assertEqual(caught.exception.code, 'row_order_mismatch')
        self.assertEqual(data, before)
        # Follow the trusted sequence itself; do not invent a family-position index.
        native = columns(); native[0], native[2] = native[2], native[0]
        native_source = sd.bind_trusted_source(VERSION, native)
        self.assertEqual([r.column_ordinal for r in sd.new_draft(native_source, 'Intensity').design.rows], [7, 1])

    def test_source_shape_version_duplicates_and_cross_field_integrity_checked(self):
        for bad in (None, {}, [dict(columns()[0], owner_id='untrusted')], [dict(columns()[0], name_raw='LFQ intensity alpha')], [dict(columns()[0], ordinal=True)], [columns()[0], columns()[0]]):
            with self.assertRaises(sd.DesignError): sd.bind_trusted_source(VERSION, bad)
        with self.assertRaises(sd.DesignError): sd.bind_trusted_source('invalid', columns())
        fake = sd.TrustedSource(VERSION, (sd.SourceColumn(1, 'wrong', 'Intensity', 'alpha'),))
        with self.assertRaises(sd.DesignError) as caught: sd.validate_design(payload(), fake)
        self.assertEqual(caught.exception.code, 'invalid_trusted_source')


class DraftAndConfirmationTests(unittest.TestCase):
    def test_draft_preserves_text_unknowns_and_cannot_mean_analysis_ready(self):
        src = source(); data = payload(src)
        data['rows'][0].update(group='  arbitrary group  ', condition=' ', replicate=' 01 ')
        before = deepcopy(data); result = sd.validate_design(data, src)
        self.assertEqual(result.design.to_payload(), before); self.assertEqual(data, before)
        self.assertFalse(result.all_rows_explicitly_confirmed); self.assertFalse(result.globally_confirmed)
        self.assertFalse(result.ready_for_differential_analysis)

    def test_explicit_all_confirmed_remains_separate_from_statistical_readiness(self):
        src = source(); result = sd.validate_design(payload(src, confirmed=True), src)
        self.assertTrue(result.all_rows_explicitly_confirmed); self.assertTrue(result.globally_confirmed)
        self.assertEqual(result.confirmation_blockers, ()); self.assertFalse(result.ready_for_differential_analysis)
        data = result.design.to_payload(); data['rows'][0]['confirmed'] = 1
        with self.assertRaises(sd.DesignError): sd.validate_design(data, src)

    def test_confirmation_requires_nonblank_fields_known_enums_and_explicit_group_trim(self):
        src = source()
        for field, value in [('group', ''), ('condition', ' '), ('replicate', '\u2003'), ('control', 'unknown'), ('replicate_type', 'unknown'), ('group', ' A'), ('group', 'A\ufeff')]:
            data = payload(src, confirmed=True); data['rows'][0][field] = value; before = deepcopy(data)
            with self.subTest(field=field, value=value), self.assertRaises(sd.DesignError): sd.validate_design(data, src)
            self.assertEqual(data, before)
        data = payload(src, confirmed=True); data['rows'][0]['condition'] = '  retained condition  '; data['rows'][0]['replicate'] = ' 01 '
        self.assertEqual(sd.validate_design(data, src).design.rows[0].replicate, ' 01 ')

    def test_unicode_codepoint_limits_and_C0_C1_all_rejected(self):
        src = source()
        for field, limit in [('group', 80), ('condition', 160), ('replicate', 40)]:
            data = payload(src); data['rows'][0][field] = '\U0001f600' * limit
            self.assertEqual(len(sd.validate_design(data, src).design.rows[0].to_payload()[field]), limit)
            data['rows'][0][field] += '\U0001f600'
            with self.assertRaises(sd.DesignError): sd.validate_design(data, src)
            for code in list(range(32)) + list(range(127, 160)):
                data = payload(src); data['rows'][0][field] = 'x' + chr(code)
                with self.subTest(field=field, code=code), self.assertRaises(sd.DesignError): sd.validate_design(data, src)

    def test_ambiguous_labels_keep_distinct_drafts_and_cannot_be_confirmed(self):
        for labels in (('same', 'same'), ('', 'beta'), (' \u00a0\ufeff', 'beta')):
            src = source(intensity=labels); result = sd.new_draft(src, 'Intensity')
            self.assertEqual([r.column_ordinal for r in result.design.rows], [1, 7])
            self.assertFalse(result.globally_confirmed)
            self.assertIn('ambiguous_sample_labels', [b.code for b in result.confirmation_blockers])
            data = payload(src, confirmed=True)
            with self.assertRaises(sd.DesignError): sd.validate_design(data, src)
        # Duplicate labels in the unselected alternative do not duplicate/block this family.
        src = source(lfq=('same', 'same'))
        self.assertTrue(sd.validate_design(payload(src, confirmed=True), src).globally_confirmed)

    def test_group_control_conflict_blocks_global_confirmation_no_group_inference(self):
        src = source(); data = payload(src, confirmed=True); data['rows'][1]['control'] = 'no'
        result = sd.validate_design(data, src)
        self.assertTrue(result.all_rows_explicitly_confirmed); self.assertFalse(result.globally_confirmed)
        self.assertIn('group_control_inconsistent', [b.code for b in result.confirmation_blockers])
        data['rows'][1]['group'] = 'another arbitrary group'; data['rows'][1]['replicate'] = 'same arbitrary identifier'
        self.assertTrue(sd.validate_design(data, src).globally_confirmed)
        self.assertFalse(sd.validate_design(data, src).ready_for_differential_analysis)

    def test_group_equality_is_exact_without_unicode_case_or_label_normalization(self):
        src = source(intensity=('alpha', 'alpha ')); data = payload(src, confirmed=True)
        data['rows'][0]['group'] = '\u00c0'; data['rows'][1].update(group='A\u0300', control='no')
        self.assertTrue(sd.validate_design(data, src).globally_confirmed)
        self.assertEqual(sd.validate_design(data, src).design.rows[1].sample_label_raw, 'alpha ')

    def test_empty_selected_family_is_only_a_blocked_draft_never_vacuously_confirmed(self):
        src = sd.bind_trusted_source(VERSION, [])
        result = sd.new_draft(src, 'Intensity')
        self.assertEqual(result.design.rows, ()); self.assertFalse(result.all_rows_explicitly_confirmed)
        self.assertFalse(result.globally_confirmed); self.assertFalse(result.ready_for_differential_analysis)


class TransitionTests(unittest.TestCase):
    def test_each_edit_clears_only_that_row_confirmation_and_source_is_immutable(self):
        src = source(); design = sd.validate_design(payload(src, confirmed=True), src).design
        for field, value in [('group', 'new group'), ('condition', 'new condition'), ('control', 'no'), ('replicate', '9'), ('replicate_type', 'biological')]:
            result = sd.edit_row(design, src, 1, {field: value})
            self.assertFalse(result.design.rows[0].confirmed); self.assertTrue(result.design.rows[1].confirmed)
            self.assertTrue(design.rows[0].confirmed)
        self.assertTrue(sd.edit_row(design, src, 1, {'group': design.rows[0].group}).design.rows[0].confirmed)
        for changes in ({'confirmed': True}, {'name_raw': 'changed'}, {'column_ordinal': 0}):
            with self.assertRaises(sd.DesignError): sd.edit_row(design, src, 1, changes)

    def test_confirmation_is_an_explicit_transition_not_save_or_validation(self):
        src = source(); data = payload(src, confirmed=True)
        for row in data['rows']: row['confirmed'] = False
        design = sd.validate_design(data, src).design
        first = sd.set_row_confirmed(design, src, 1, True)
        self.assertFalse(first.globally_confirmed)
        final = sd.set_row_confirmed(first.design, src, 7, True)
        self.assertTrue(final.globally_confirmed); self.assertFalse(final.ready_for_differential_analysis)
        with self.assertRaises(sd.DesignError): sd.set_row_confirmed(design, src, 1, 'true')

    def test_switch_family_resets_flags_with_only_explicit_target_ordinal_suggestions(self):
        src = source(); design = sd.validate_design(payload(src, confirmed=True), src).design
        suggestions = {2: {'group': 'suggested', 'condition': 'copied', 'control': 'yes', 'replicate': '1', 'replicate_type': 'biological'}}
        result = sd.switch_quantification(design, src, 'LFQ intensity', suggestions)
        self.assertEqual([r.column_ordinal for r in result.design.rows], [2, 11])
        self.assertEqual(result.design.rows[0].group, 'suggested'); self.assertEqual(result.design.rows[1].group, '')
        self.assertTrue(all(not r.confirmed for r in result.design.rows)); self.assertFalse(result.globally_confirmed)
        self.assertTrue(all(not r.confirmed for r in sd.switch_quantification(design, src, 'Intensity').design.rows))
        for bad in ({1: {'group': 'cross-family'}}, {2: {'confirmed': True}}, {'2': {'group': 'string ordinal'}}):
            with self.assertRaises(sd.DesignError): sd.switch_quantification(design, src, 'LFQ intensity', bad)

    def test_source_and_result_are_defensive_copies_and_version_changes_reject_reuse(self):
        raw = columns(); src = sd.bind_trusted_source(VERSION, raw); raw[0]['name_raw'] = 'mutated'
        data = payload(src); result = sd.validate_design(data, src); data['rows'][0]['group'] = 'mutated'
        self.assertEqual(src.sample_columns[0].name_raw, 'Intensity alpha'); self.assertEqual(result.design.rows[0].group, '')
        with self.assertRaises(FrozenInstanceError): result.design.rows[0].confirmed = True
        other = sd.bind_trusted_source(OTHER_VERSION, columns())
        with self.assertRaises(sd.DesignError): sd.edit_row(result.design, other, 1, {'group': 'new'})

    def test_import_is_inert_stdlib_only_and_errors_do_not_echo_source_values(self):
        source_text = MODEL_PATH.read_text(encoding='utf-8'); body = compile(source_text, 'sample_design.py', 'exec')
        m = ModuleType('sample_design_inert'); sys.modules[m.__name__] = m
        with patch('builtins.open', forbidden), patch('io.open', forbidden), patch('os.open', forbidden), patch('os.readlink', forbidden), patch('socket.socket', forbidden), patch('subprocess.Popen', forbidden), patch('threading.Thread.start', forbidden):
            exec(body, m.__dict__)
        data = payload(); data['rows'][0]['group'] = 'PRIVATE_SENTINEL\n'
        with self.assertRaises(sd.DesignError) as caught: sd.validate_design(data, source())
        self.assertNotIn('PRIVATE_SENTINEL', str(caught.exception))


class Clarification01Tests(unittest.TestCase):
    def test_lone_surrogates_rejected_in_all_manual_fields_draft_and_confirmed(self):
        src = source()
        for field in ('group', 'condition', 'replicate'):
            for text in ('\ud800', '\udfff', 'prefix\ud83d', '\ude00suffix', '\U0001f600\ud800'):
                for confirmed in (False, True):
                    data = payload(src, confirmed=confirmed); data['rows'][0][field] = text
                    with self.subTest(field=field, confirmed=confirmed), self.assertRaises(sd.DesignError) as caught:
                        sd.validate_design(data, src)
                    self.assertEqual(caught.exception.code, 'manual_text_invalid_unicode_scalar')
                    self.assertEqual(caught.exception.field, field)

    def test_valid_astral_and_decoded_surrogate_pair_keep_codepoint_limits(self):
        src = source(); astral = json.loads('"\\ud83d\\ude00"')
        self.assertEqual(astral, '\U0001f600'); self.assertEqual(len(astral), 1)
        for field, limit in [('group', 80), ('condition', 160), ('replicate', 40)]:
            data = payload(src, confirmed=True); data['rows'][0][field] = astral * limit
            result = sd.validate_design(data, src)
            self.assertEqual(result.design.rows[0].to_payload()[field], astral * limit)
            self.assertTrue(result.design.rows[0].confirmed)
            self.assertEqual(len(result.design.rows[0].to_payload()[field].encode('utf-8')), 4 * limit)
            data['rows'][0][field] += astral
            with self.assertRaises(sd.DesignError) as caught: sd.validate_design(data, src)
            self.assertEqual(caught.exception.code, 'manual_text_too_long')

    def test_noop_and_all_same_manual_facts_preserve_true_and_false_flags(self):
        src = source()
        for confirmed in (True, False):
            data = payload(src, confirmed=True)
            for row in data['rows']: row['confirmed'] = confirmed
            design = sd.validate_design(data, src).design; before = design.to_payload()
            same = {key: getattr(design.rows[0], key) for key in sd.MANUAL_KEYS}
            for changes in ({}, same, *({key: value} for key, value in same.items())):
                result = sd.edit_row(design, src, 1, changes)
                self.assertEqual(result.design.to_payload(), before)
                self.assertEqual(result.design.rows[0].confirmed, confirmed)
                self.assertEqual(design.to_payload(), before)

    def test_effective_mixed_patch_clears_only_edited_flag_and_invalid_edit_is_atomic(self):
        src = source(); design = sd.validate_design(payload(src, confirmed=True), src).design
        before = design.to_payload()
        changed = sd.edit_row(design, src, 1, {'group': design.rows[0].group, 'condition': 'new condition'})
        self.assertFalse(changed.design.rows[0].confirmed); self.assertTrue(changed.design.rows[1].confirmed)
        self.assertEqual(changed.design.rows[0].condition, 'new condition')
        with self.assertRaises(sd.DesignError): sd.edit_row(design, src, 1, {'condition': '\ud800'})
        self.assertEqual(design.to_payload(), before)
        switched = sd.switch_quantification(design, src, 'LFQ intensity', {2: {'group': design.rows[0].group}})
        self.assertTrue(all(not row.confirmed for row in switched.design.rows))


class JSONIntegerClarificationTests(unittest.TestCase):
    def test_integral_float_request_and_source_values_normalize_without_mutation(self):
        raw = columns()
        for column in raw: column['ordinal'] = float(column['ordinal'])
        src = sd.bind_trusted_source(VERSION, raw)
        self.assertTrue(all(type(c.ordinal) is int for c in src.sample_columns))
        self.assertTrue(all(type(c['ordinal']) is float for c in raw))
        data = payload(src, confirmed=True); data['schemaVersion'] = 1.0
        for row in data['rows']: row['column_ordinal'] = float(row['column_ordinal'])
        result = sd.validate_design(data, src); output = result.design.to_payload()
        self.assertEqual(output['schemaVersion'], 1); self.assertIs(type(output['schemaVersion']), int)
        self.assertTrue(all(type(r['column_ordinal']) is int for r in output['rows']))
        self.assertTrue(all(type(r['column_ordinal']) is float for r in data['rows']))
        self.assertTrue(sd.edit_row(result.design, src, 1.0, {}).design.rows[0].confirmed)
        self.assertFalse(sd.set_row_confirmed(result.design, src, 1.0, False).design.rows[0].confirmed)

    def test_bool_string_fraction_nonfinite_and_unsafe_numbers_are_not_protocol_integers(self):
        bad = [True, False, '1', None, 1.5, float('nan'), float('inf'), float('-inf'), 2**53, float(2**53), -(2**53)]
        for value in bad:
            data = payload(); data['schemaVersion'] = value
            with self.subTest(field='schemaVersion', value=repr(value)), self.assertRaises(sd.DesignError): sd.validate_design(data, source())
            data = payload(); data['rows'][0]['column_ordinal'] = value
            with self.subTest(field='column_ordinal', value=repr(value)), self.assertRaises(sd.DesignError): sd.validate_design(data, source())
            raw = columns(); raw[0]['ordinal'] = value
            with self.subTest(field='source_ordinal', value=repr(value)), self.assertRaises(sd.DesignError): sd.bind_trusted_source(VERSION, raw)

    def test_safe_integer_boundary_and_float_order_or_source_mismatch_still_strict(self):
        raw = [dict(columns()[0], ordinal=9007199254740991.0)]
        src = sd.bind_trusted_source(VERSION, raw); data = payload(src)
        data['rows'][0]['column_ordinal'] = 9007199254740991.0
        self.assertIs(type(sd.validate_design(data, src).design.rows[0].column_ordinal), int)
        src = source(); data = payload(src)
        data['rows'][0]['column_ordinal'] = 2.0
        with self.assertRaises(sd.DesignError) as caught: sd.validate_design(data, src)
        self.assertEqual(caught.exception.code, 'source_column_mismatch')
        data = payload(src); data['rows'].reverse()
        for row in data['rows']: row['column_ordinal'] = float(row['column_ordinal'])
        with self.assertRaises(sd.DesignError) as caught: sd.validate_design(data, src)
        self.assertEqual(caught.exception.code, 'row_order_mismatch')


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    print(json.dumps({'contract': sd.CONTRACT, 'tests': result.testsRun, 'guard_hits': len(GUARD_HITS), 'HTTP_DB_Auth_scientific_actions': 0}))
    raise SystemExit(0 if result.wasSuccessful() and not GUARD_HITS else 1)
