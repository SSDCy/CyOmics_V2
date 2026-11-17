"""Synthetic parser/identity tests; no filesystem, server or scientific API."""
import hashlib,sys,unittest
from dataclasses import replace
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cyomics_projects.fasta_index import parse_fasta,FastaIndexError,Limits

class FastaTests(unittest.TestCase):
    def error(self,data,code,**kw):
        with self.assertRaises(FastaIndexError) as cm:parse_fasta(data,**kw)
        self.assertEqual(cm.exception.code,code);return cm.exception
    def test_raw_header_sequence_hash_case_and_wrapping_preserved(self):
        raw=b'>  id desc\r\nacD\r\nBJOUXZ\n'
        result=parse_fasta(raw);r=result.records[0]
        self.assertEqual((result.source_bytes,result.source_sha256),(len(raw),hashlib.sha256(raw).hexdigest()))
        self.assertEqual((r.id,r.header_raw,r.sequence_lines,r.sequence_raw),('id','  id desc',('acD','BJOUXZ'),'acDBJOUXZ'))
        self.assertEqual(r.canonical_sequence,'ACDBJOUXZ');self.assertIsNone(r.canonical_issue)
        self.assertEqual(r.sequence_sha256,hashlib.sha256(b'ACDBJOUXZ').hexdigest())
    def test_duplicate_and_case_sensitive_ids_preserve_all_ordinals(self):
        v=parse_fasta(b'>x\nAA\n>x other\nCC\n>X\nGG\n')
        self.assertEqual(v.id_ordinals['x'],(1,2));self.assertEqual([r.ordinal for r in v.records_for_id('x')],[1,2])
        self.assertEqual(v.id_ordinals['X'],(3,));self.assertEqual(v.records_for_id('absent'),())
        with self.assertRaises(TypeError):v.id_ordinals['x']=(1,)
    def test_invalid_or_empty_sequences_retained_not_repaired(self):
        v=parse_fasta(b'>a\nA C\n>b\nAA*\n>c\nA-\n>d\n12?\n>e\n\n')
        self.assertEqual([r.canonical_issue for r in v.records],['sequence_whitespace','stop_or_gap','stop_or_gap','illegal_residue','empty_sequence'])
        for r in v.records:
            self.assertIsNone(r.canonical_sequence);self.assertIsNone(r.sequence_sha256)
            self.assertEqual(r.raw_sequence_sha256,hashlib.sha256(r.sequence_raw.encode()).hexdigest())
    def test_blank_and_no_final_newline(self):
        v=parse_fasta(b'\n>a\nAA\n\nCC');self.assertEqual(v.blank_lines,2)
        self.assertEqual(v.records[0].sequence_lines,('AA','','CC'));self.assertEqual(v.records[0].sequence_raw,'AACC')
    def test_every_byte_boundary_and_empty_chunks_equal(self):
        raw=b'>a\r\nAc\r\n>x\nBU\n';one=parse_fasta(raw)
        for i in range(len(raw)+1):
            v=parse_fasta((raw[:i],b'',raw[i:]));self.assertEqual(v.records,one.records);self.assertEqual(v.source_sha256,one.source_sha256)
    def test_bad_utf8_distinct_from_unsupported_ASCII_contract(self):
        self.error(b'>x\n\xff','INVALID_UTF8')
        for raw,code in [(b'\xef\xbb\xbf>x\nAA','UTF8_BOM'),('>中文\nAA'.encode(),'NON_ASCII_FASTA'),(b'>x\rAA','LONE_CR'),(b'>x\nAA\r','LONE_CR')]:
            self.assertEqual(self.error(raw,code).category,'unsupported')
    def test_structure_errors(self):
        for raw,code in [(b'', 'NO_RECORDS'),(b'\n\n','NO_RECORDS'),(b'AA','SEQUENCE_BEFORE_HEADER'),(b'>  \nAA','EMPTY_ID')]:self.error(raw,code)
    def test_wrong_input_types_not_paths_or_URLs(self):
        for value in ('/private/file','https://x',bytearray(b'>x\nAA'),memoryview(b'x'),None):self.error(value,'INVALID_INPUT')
        self.error((b'>x\nAA','text'),'INVALID_CHUNK')
    def test_limits_only_lower_known_positive_integers(self):
        for value in ({},replace(Limits(),max_bytes=True),replace(Limits(),max_records=50001),replace(Limits(),max_lines=0)):
            self.error(b'>x\nAA','INVALID_LIMITS',limits=value)
    def test_caps_have_no_partial_result(self):
        for raw,limits,code in [(b'>x\nAA',replace(Limits(),max_bytes=4),'BYTE_LIMIT'),
            (b'>x\nAA\n>y\nCC',replace(Limits(),max_records=1),'RECORD_LIMIT'),
            (b'>xyz\nAA',replace(Limits(),max_id_length=2),'ID_LENGTH_LIMIT'),
            (b'>xy\nAA\n>z\nCC',replace(Limits(),max_id_characters=2),'ID_CHARACTER_LIMIT'),
            (b'>x\nAAA',replace(Limits(),max_line_length=2),'LINE_LENGTH_LIMIT'),
            (b'>x\nAA\nCC',replace(Limits(),max_sequence_length=3),'SEQUENCE_LENGTH_LIMIT'),
            (b'>x\nAA\n',replace(Limits(),max_lines=1),'LINE_LIMIT')]:self.error(raw,code,limits=limits)
        self.error((b'',b'',b''),'CHUNK_LIMIT',limits=replace(Limits(),max_chunks=2))
    def test_repr_never_contains_user_sequences_or_headers(self):
        v=parse_fasta(b'>PRIVATE_HEADER\nPRIVATESEQ')
        self.assertNotIn('PRIVATE',repr(v));self.assertNotIn('PRIVATE',repr(v.records[0]))

if __name__=='__main__':unittest.main(verbosity=2)
