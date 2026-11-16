"""Synthetic-only parser tests and real POSIX files under this candidate's audit.

The filesystem suite requires POSIX and a safe, owned candidate parent chain.
It is not silently skipped or treated as equivalent on another platform.
"""
import base64
import importlib
import inspect
import io
import json
import os
from pathlib import Path
import socket
import stat
import tempfile
import traceback
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cyomics_projects import anon_config as anon
from cyomics_projects import settings
from cyomics_projects.app import create_app
from cyomics_projects.settings import Fault, RuntimeNotApproved


def encoded(raw):
    if not isinstance(raw, bytes):
        raw = json.dumps(raw, separators=(',', ':')).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def jwt(header=None, payload=None, signature=b'offline-not-a-verified-signature'):
    return '.'.join((encoded({'alg': 'HS256'} if header is None else header),
                     encoded({'role': 'anon', 'fixture': 'synthetic-only'} if payload is None else payload),
                     encoded(signature)))


KEY = jwt()
SENTINEL = 'SYNTHETIC_OTHER_SECRET_MUST_NOT_ESCAPE'


def configuration(value=KEY):
    return ('SMTP_PASSWORD=' + SENTINEL + '\nANON_KEY=' + value + '\n').encode()


class SafeFailureAssertions:
    def rejected(self, operation):
        with self.assertRaises(Fault) as caught:
            operation()
        error = caught.exception
        self.assertEqual((error.status, error.code), (503, 'projects_unavailable'))
        self.assertIsNone(error.__context__)
        rendered = ''.join(traceback.format_exception(error))
        for forbidden in (KEY, SENTINEL, '/auth-dev/.env.keys'):
            self.assertNotIn(forbidden, rendered)


class ParserTests(SafeFailureAssertions, unittest.TestCase):
    def test_anon_acceptance_is_not_signature_verification(self):
        self.assertEqual(anon._parse_anon_config(configuration()), KEY)
        self.assertEqual(anon._parse_anon_config(configuration(jwt(signature=b'not-a-real-signature'))),
                         jwt(signature=b'not-a-real-signature'))

    def test_native_node_probed_safe_subset_single_line_formats(self):
        forms = [
            'ANON_KEY=' + KEY, '  ANON_KEY = ' + KEY + '  ',
            'export ANON_KEY=' + KEY, '  export ANON_KEY = "' + KEY + '" # comment',
            "ANON_KEY='" + KEY + "'", 'ANON_KEY="' + KEY + '"',
            'ANON_KEY=' + KEY + '# adjacent comment',
            '\ufeffANON_KEY=' + KEY,
        ]
        for line in forms:
            for ending in ('\n', '\r\n', ''):
                with self.subTest(format=forms.index(line), ending=repr(ending)):
                    self.assertEqual(anon._parse_anon_config((line + ending).encode()), KEY)

    def test_native_rejected_target_whitespace_cannot_be_skipped(self):
        # A Node20 complete provider probe, not JS regex alone, rejects these.
        # Each rejection must remain a failure even with a later valid target.
        forms = [
            '\tANON_KEY\t=\t' + KEY + '\t', '\texport ANON_KEY=' + KEY,
            '\tANON_KEY=' + KEY, 'ANON_KEY\t=' + KEY,
            'ANON_KEY=\t' + KEY, 'ANON_KEY=' + KEY + '\t',
            'ANON_KEY=' + KEY + '\t# comment',
            'export \tANON_KEY=' + KEY, '\t export\tANON_KEY=' + KEY,
            'export  ANON_KEY=' + KEY,
            '\u00a0export ANON_KEY=' + KEY, '\u00a0ANON_KEY=' + KEY,
            'export\ufeffANON_KEY=' + KEY,
            'ANON_KEY\ufeff=\ufeff' + KEY + '\ufeff# comment',
            'export\tANON_KEY=' + KEY, 'export\t ANON_KEY=' + KEY,
            'export\u00a0ANON_KEY=' + KEY, '\u2028ANON_KEY=' + KEY,
            '\vANON_KEY=' + KEY, '\fANON_KEY=' + KEY,
            'OTHER=synthetic\n\ufeffANON_KEY=' + KEY,
            'ANON_KEY=' + KEY + '# comment\rcontinued',
            'ANON_KEY=' + KEY + '# comment\u2028continued',
            'ANON_KEY=' + KEY + '# comment\u2029continued',
        ]
        for index, line in enumerate(forms):
            for suffix in ('', '\nANON_KEY=' + KEY):
                with self.subTest(case=index, laterValidTarget=bool(suffix)):
                    self.rejected(lambda: anon._parse_anon_config((line + suffix).encode()))

    def test_other_assignments_are_ignored_without_evaluation(self):
        raw = ('SERVICE_ROLE_KEY=' + SENTINEL + '\nOTHER=$(untrusted_command)\n'
               'ANON_KEY_BACKUP=' + SENTINEL + '\n# ANON_KEY=ignored\nANON_KEY=' + KEY).encode()
        with patch('os.system', side_effect=AssertionError('No shell')) as command:
            self.assertEqual(anon._parse_anon_config(raw), KEY)
            # Actual provider splits LF into separate physical lines before its
            # regex: a later non-target line is not a CR/Unicode dot-crossing case.
            self.assertEqual(anon._parse_anon_config(('ANON_KEY=' + KEY + '# comment\ncontinued').encode()), KEY)
        command.assert_not_called()

    def test_missing_duplicate_and_malformed_target_assignments(self):
        bad = [b'', b'ANON_KEY_BACKUP=not-selected', b'ANON_KEY', b'ANON_KEY=',
               b'ANON_KEY:bad', b'ANON_KEY+=bad', b'ANON_KEY="unterminated',
               ('\u0085ANON_KEY=' + KEY).encode(),
               ('ANON_KEY=' + KEY + '# comment\rcontinued').encode(),
               ('ANON_KEY=' + KEY + '# comment\u2028continued').encode(),
               ('ANON_KEY=' + KEY + '# comment\u2029continued').encode(),
               b'ANON_KEY=two words', b'export ANON_KEY=\'mixed"',
               configuration() + configuration(), configuration() + b'export ANON_KEY=bad',
               b'ANON_KEY=""\n' + configuration()]
        for index, raw in enumerate(bad):
            with self.subTest(case=index):
                self.rejected(lambda: anon._parse_anon_config(raw))

    def test_only_anon_role_is_accepted(self):
        for role in ('service_role', 'authenticated', 'ANON', '', None, ['anon']):
            with self.subTest(roleType=type(role).__name__):
                self.rejected(lambda: anon._parse_anon_config(configuration(jwt(payload={'role': role}))))
        self.rejected(lambda: anon._parse_anon_config(configuration(jwt(payload={}))))

    def test_rejects_none_missing_empty_and_malformed_algorithm(self):
        for header in ({}, [], {'alg': None}, {'alg': ''}, {'alg': 'none'},
                       {'alg': 'NoNe'}, {'alg': ' none '}, {'alg': 'HS256\n'}):
            with self.subTest(case=repr(header)):
                self.rejected(lambda: anon._parse_anon_config(configuration(jwt(header=header))))

    def test_strict_json_duplicate_keys_types_and_surrogates(self):
        bad = [jwt(header=b'{"alg":"HS256","alg":"RS256"}'),
               jwt(payload=b'{"role":"anon","role":"service_role"}'),
               jwt(payload=b'{"role":"anon","nested":{"x":1,"x":2}}'),
               jwt(payload=b'{"role":"anon","x":"\\ud800"}'),
               jwt(payload=[]), jwt(payload=b'{"role":"anon",}')]
        for index, value in enumerate(bad):
            with self.subTest(case=index):
                self.rejected(lambda: anon._parse_anon_config(configuration(value)))

    def test_rejects_nonfinite_json_even_numeric_overflow(self):
        for value in (b'NaN', b'Infinity', b'-Infinity', b'1e999'):
            raw = b'{"role":"anon","nested":[' + value + b']}'
            self.rejected(lambda: anon._parse_anon_config(configuration(jwt(payload=raw))))

    def test_jwt_segments_canonical_encoding_and_signature_presence(self):
        h, p, s = KEY.split('.')
        bad = [h + '.' + p, KEY + '.extra', '.' + p + '.' + s,
               h + '..' + s, h + '.' + p + '.', h + '=.' + p + '.' + s,
               h + '.' + p + '=.' + s, h + '.' + p + '.' + s + '=',
               h + '.' + p + '.AB', h + '.' + p + '.a+', KEY + ' ']
        for index, value in enumerate(bad):
            with self.subTest(case=index):
                self.rejected(lambda: anon._parse_anon_config(('ANON_KEY="' + value + '"').encode()))

    def test_utf8_and_bounded_config_and_key_size(self):
        base = configuration()
        self.assertEqual(anon._parse_anon_config(base + b'#' * (16384 - len(base))), KEY)
        for raw in (base + b'#' * (16385 - len(base)), b'OTHER=\xff\n' + base,
                    configuration(jwt(payload={'role': 'anon', 'long': 'x' * 4096})), 'ANON_KEY=' + KEY):
            self.rejected(lambda: anon._parse_anon_config(raw))

    def test_parse_is_io_free_and_failure_output_is_constant(self):
        with patch('os.open', side_effect=AssertionError('No file')) as opened, \
             patch.object(socket.socket, 'connect', side_effect=AssertionError('No network')) as connected:
            self.assertEqual(anon._parse_anon_config(configuration()), KEY)
            output, errors = io.StringIO(), io.StringIO()
            with patch('sys.stdout', output), patch('sys.stderr', errors):
                self.rejected(lambda: anon._parse_anon_config(configuration(SENTINEL)))
            self.assertEqual(output.getvalue() + errors.getvalue(), '')
        opened.assert_not_called()
        connected.assert_not_called()


class PosixFilesystemTests(SafeFailureAssertions, unittest.TestCase):
    def setUp(self):
        if os.name != 'posix':
            self.fail('POSIX filesystem evidence requires a separate supported run; no equivalent skip')
        # A real private test directory, not /tmp and not the live auth-dev tree.
        scratch = Path(__file__).resolve().parents[3] / 'audit/AUTH-ANON-LOADER-001/synthetic'
        scratch.mkdir(mode=0o700, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix='anon-fixture-', dir=scratch)
        self.addCleanup(self.temporary.cleanup)
        self.parent = Path(self.temporary.name) / 'auth-dev'
        self.parent.mkdir(mode=0o700)
        self.path = self.parent / '.env.keys'
        self.path.write_bytes(configuration())
        self.path.chmod(0o600)

    def test_real_owned_posix_file_and_no_cache(self):
        self.assertEqual(anon._read_anon_key(self.path), KEY)
        second = jwt(payload={'role': 'anon', 'fixture': 'second-synthetic-only'})
        self.path.write_bytes(configuration(second))
        self.assertEqual(anon._read_anon_key(self.path), second)

    def test_missing_file(self):
        self.path.unlink()
        self.rejected(lambda: anon._read_anon_key(self.path))

    def test_leaf_permissions(self):
        for mode in (0o644, 0o640, 0o660, 0o400):
            with self.subTest(mode=oct(mode)):
                self.path.chmod(mode)
                self.rejected(lambda: anon._read_anon_key(self.path))
        self.path.chmod(0o600)

    def test_private_directory_and_writable_ancestor(self):
        for mode in (0o750, 0o770, 0o777):
            with self.subTest(mode=oct(mode)):
                self.parent.chmod(mode)
                self.rejected(lambda: anon._read_anon_key(self.path))
        self.parent.chmod(0o700)
        Path(self.temporary.name).chmod(0o770)
        self.rejected(lambda: anon._read_anon_key(self.path))
        Path(self.temporary.name).chmod(0o700)

    def test_leaf_and_directory_symlink(self):
        alternate = self.parent / 'synthetic-only'
        self.path.rename(alternate)
        self.path.symlink_to(alternate)
        self.rejected(lambda: anon._read_anon_key(self.path))
        linked = Path(self.temporary.name) / 'linked-parent'
        linked.symlink_to(self.parent, target_is_directory=True)
        self.rejected(lambda: anon._read_anon_key(linked / 'synthetic-only'))

    def test_hardlink_fifo_and_directory_are_rejected(self):
        linked = self.parent / 'hardlink'
        os.link(self.path, linked)
        self.rejected(lambda: anon._read_anon_key(self.path))
        linked.unlink()
        self.path.unlink()
        os.mkfifo(self.path, 0o600)
        self.rejected(lambda: anon._read_anon_key(self.path))
        self.path.unlink()
        self.path.mkdir(mode=0o700)
        self.rejected(lambda: anon._read_anon_key(self.path))

    def test_empty_and_oversized_rejected_before_read(self):
        for raw in (b'', b'x' * 16385):
            self.path.write_bytes(raw)
            with patch('os.read', side_effect=AssertionError('Must reject before reading')) as read:
                self.rejected(lambda: anon._read_anon_key(self.path))
            read.assert_not_called()

    def test_file_owner_and_group_checked_before_read(self):
        real = os.fstat
        for field in ('st_uid', 'st_gid'):
            def changed(fd):
                info = real(fd)
                if stat.S_ISREG(info.st_mode):
                    values = {key: getattr(info, key) for key in anon._STABLE}
                    values[field] += 1
                    return SimpleNamespace(**values)
                return info
            with self.subTest(field=field), patch('os.fstat', side_effect=changed), \
                 patch('os.read', side_effect=AssertionError('Bad owner cannot read')) as read:
                self.rejected(lambda: anon._read_anon_key(self.path))
                read.assert_not_called()

    def test_uncontrolled_parent_owner_is_rejected(self):
        real = os.fstat
        changed_once = False
        def changed(fd):
            nonlocal changed_once
            info = real(fd)
            if stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and not changed_once:
                changed_once = True
                values = {key: getattr(info, key) for key in anon._STABLE}
                values['st_uid'] = max(os.getuid(), real(root_fd).st_uid) + 17
                return SimpleNamespace(**values)
            return info
        root_fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        try:
            with patch('os.fstat', side_effect=changed):
                self.rejected(lambda: anon._read_anon_key(self.path))
        finally:
            os.close(root_fd)
        self.assertTrue(changed_once)

    def test_all_stability_fields_checked_after_read(self):
        real = os.fstat
        for field in anon._STABLE:
            calls = 0
            def changed(fd):
                nonlocal calls
                info = real(fd)
                if stat.S_ISREG(info.st_mode):
                    calls += 1
                    if calls == 2:
                        values = {key: getattr(info, key) for key in anon._STABLE}
                        values[field] += 1
                        return SimpleNamespace(**values)
                return info
            with self.subTest(field=field), patch('os.fstat', side_effect=changed):
                self.rejected(lambda: anon._read_anon_key(self.path))
                self.assertEqual(calls, 2)

    def test_real_metadata_change_during_read_is_rejected(self):
        real = os.read
        changed = False
        def reading(fd, count):
            nonlocal changed
            value = real(fd, count)
            if not changed:
                changed = True
                info = self.path.stat()
                os.utime(self.path, ns=(info.st_atime_ns, info.st_mtime_ns + 1000000000))
            return value
        with patch('os.read', side_effect=reading):
            self.rejected(lambda: anon._read_anon_key(self.path))
        self.assertTrue(changed)

    def test_growth_and_short_read_are_bounded_and_rejected(self):
        for grow in (False, True):
            real = os.read
            total, calls = 0, 0
            def reading(fd, count):
                nonlocal total, calls
                calls += 1
                value = b'x' * count if grow else real(fd, 4) if calls == 1 else b''
                total += len(value)
                return value
            with self.subTest(growing=grow), patch('os.read', side_effect=reading):
                self.rejected(lambda: anon._read_anon_key(self.path))
            self.assertLessEqual(total, 16385)

    def test_only_own_descriptors_closed_on_success_and_error(self):
        real_open, real_close = os.open, os.close
        unrelated = real_open(self.path, os.O_RDONLY)
        try:
            for invalid in (False, True):
                opened, closed = [], []
                def opening(*args, **kwargs):
                    fd = real_open(*args, **kwargs)
                    opened.append(fd)
                    return fd
                def closing(fd):
                    closed.append(fd)
                    return real_close(fd)
                self.path.write_bytes(configuration(SENTINEL) if invalid else configuration())
                with patch('os.open', side_effect=opening), patch('os.close', side_effect=closing):
                    if invalid:
                        self.rejected(lambda: anon._read_anon_key(self.path))
                    else:
                        self.assertEqual(anon._read_anon_key(self.path), KEY)
                self.assertCountEqual(closed, opened)
                self.assertEqual(len(closed), len(set(closed)))
                self.assertNotIn(unrelated, closed)
                self.assertTrue(stat.S_ISREG(os.fstat(unrelated).st_mode))
        finally:
            real_close(unrelated)

    def test_read_error_is_sanitized_and_descriptors_closed(self):
        real_open, real_close = os.open, os.close
        opened, closed = [], []
        def opening(*args, **kwargs):
            fd = real_open(*args, **kwargs)
            opened.append(fd)
            return fd
        def closing(fd):
            closed.append(fd)
            return real_close(fd)
        with patch('os.open', side_effect=opening), patch('os.close', side_effect=closing), \
             patch('os.read', side_effect=OSError(SENTINEL + KEY)):
            self.rejected(lambda: anon._read_anon_key(self.path))
        self.assertCountEqual(opened, closed)

    def test_unsupported_platform_and_missing_features_fail_closed(self):
        with patch('os.name', 'nt'), patch('os.open') as opened:
            self.rejected(lambda: anon._read_anon_key(self.path))
            opened.assert_not_called()
        with patch('os.O_NOFOLLOW', None), patch('os.open') as opened:
            self.rejected(lambda: anon._read_anon_key(self.path))
            opened.assert_not_called()
        with patch('os.open', side_effect=NotImplementedError(SENTINEL)):
            self.rejected(lambda: anon._read_anon_key(self.path))


class ProductionEntryTests(unittest.TestCase):
    def test_closed_gate_preserves_original_failure_before_any_io(self):
        self.assertFalse(settings.AUTH_POLICY_VERIFIED)
        self.assertFalse(settings.RUNTIME_START_APPROVED)
        with patch('os.open', side_effect=AssertionError('No configuration')) as opened, \
             patch.object(socket.socket, 'connect', side_effect=AssertionError('No network')) as connected:
            with self.assertRaises(RuntimeNotApproved):
                settings.load_approved_anon_key()
            with self.assertRaises(RuntimeNotApproved):
                create_app()
        opened.assert_not_called()
        connected.assert_not_called()

    def test_module_import_does_not_open_configuration_or_network(self):
        with patch('os.open', side_effect=AssertionError('No configuration')) as opened, \
             patch.object(socket.socket, 'connect', side_effect=AssertionError('No network')) as connected:
            importlib.reload(anon)
        opened.assert_not_called()
        connected.assert_not_called()

    def test_no_argument_fixed_source_lazy_entry_and_no_cache(self):
        self.assertEqual(list(inspect.signature(settings.load_approved_anon_key).parameters), [])
        second = jwt(payload={'role': 'anon', 'fixture': 'second-synthetic-only'})
        with patch.object(settings, 'production_gate') as gate, \
             patch.object(anon, '_read_anon_key', side_effect=[KEY, second]) as reader, \
             patch.dict(os.environ, {'ANON_KEY': SENTINEL, 'AUTH_CONFIG_PATH': '/untrusted'}):
            self.assertEqual(settings.load_approved_anon_key(), KEY)
            self.assertEqual(settings.load_approved_anon_key(), second)
        self.assertEqual(gate.call_count, 2)
        self.assertEqual(reader.call_args_list, [unittest.mock.call(anon._CONFIG)] * 2)
        self.assertEqual(str(anon._CONFIG), '/home/user01/Cy_Omics_v2.0/auth-dev/.env.keys')
        self.assertFalse(settings.AUTH_POLICY_VERIFIED)
        self.assertFalse(settings.RUNTIME_START_APPROVED)
