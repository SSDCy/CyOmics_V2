"""Portable synthetic tests only: no engines, network, real sequence files or DB reads."""
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
from pathlib import Path
import unittest

from cyomics_projects.sequence.domain_runner import adapt_result, bind_sequence, database_snapshot_sha256, make_plan, plan_sha256, sha

RUN = "b8656fc5-e7e7-4df2-bbd0-c956571b42d8"
BLAST = b"SYNTHETIC_Q\tSYNTHETIC_SUB\t50.000\t21\t10\t30\t20\t40\t1e-8\t40.0\t100\t200\t21\t0\tSYNTHETIC description\n"
HMM_ROW = "SYNTHETIC_Q - 100 Test_model PF00001.9 50 1e-30 80.0 0.0 1 1 1e-35 1e-32 70.0 0.0 1 50 10 59 8 61 0.99 SYNTHETIC description"


def fixture(tool="blastp", raw=None):
    sequence = bind_sequence("SYNTHETIC_Q", "A" * 100, sha(b"A" * 100))
    request = {"tool_key": tool, "database_key": "swissprot-reviewed" if tool == "blastp" else "pfam-raw-reviewed",
               "limits": {"threads": 1, "wall_seconds": 60, "memory_bytes": 2 * 1024 ** 3, "output_bytes": 1024 ** 2}}
    context = {"run_id": RUN, "project_id": None, "dataset_version_id": None, "taxonomy_id": None}
    plan = make_plan(request, sequence, context)
    if raw is None:
        raw = BLAST if tool == "blastp" else hmm()
    sidecar = {"plan_sha256": plan_sha256(plan), "output_sha256": sha(raw),
               "database_snapshot_sha256": database_snapshot_sha256(plan),
               "sequence_sha256": sequence.sequence_sha256, "query_fasta_sha256": sequence.query_fasta_sha256,
               "engine_version": "2.17.0+" if tool == "blastp" else "3.3.2", "exit_code": 0,
               "completed": True, "truncated": False, "elapsed_seconds": "0.5", "output_bytes": len(raw), "synthetic_fixture": True}
    return request, context, plan, sidecar, raw


def hmm(rows=(HMM_ROW,), complete=True):
    text = "# SYNTHETIC ONLY\n" + "\n".join(rows) + "\n# Program: hmmsearch\n# Version: 3.3.2 (SYNTHETIC)\n"
    return (text + ("# [ok]\n" if complete else "")).encode()


from unittest.mock import patch
from cyomics_projects.sequence import domain_runner

def configure_synthetic_paths(case):
    # Test-only declared strings; no filesystem is probed and no engine runs.
    case.enterContext(patch.object(domain_runner, "ROOT", "/__synthetic_disabled_workspace__"))
    case.enterContext(patch.dict(domain_runner.CONFIG["swissprot-reviewed"], {"path": "/__synthetic_swissprot__"}))
    case.enterContext(patch.dict(domain_runner.CONFIG["pfam-raw-reviewed"], {"path": "/__synthetic_pfam_hmm__"}))

class AdapterTests(unittest.TestCase):
    def setUp(self):
        configure_synthetic_paths(self)

    def test_vendor_is_unmodified_reviewed_copy(self):
        digest = hashlib.sha256(Path(__import__("cyomics_projects.sequence.domain_runner", fromlist=["x"]).__file__).with_name("vendor_domain_r1.py").read_bytes()).hexdigest()
        self.assertEqual(digest, "09608f43623e2939b38ec2427f842a70a67cb39f810678b15cda2839044dbd87")

    def test_fixed_blast_argv_disabled(self):
        _, _, plan, sidecar, raw = fixture()
        self.assertEqual(plan.argv[0], "/usr/local/bin/blastp")
        self.assertIn("-max_target_seqs", plan.argv)
        self.assertFalse(plan.execution_enabled)
        self.assertEqual(plan.argv[plan.argv.index("-db") + 1], "/__synthetic_swissprot__")
        result = adapt_result(raw, plan, sidecar)
        self.assertEqual(result["hits"][0]["taxonomy_status"], "unknown")
        self.assertEqual(result["kind"], "synthetic_fixture")
        self.assertFalse(result["platform_analysis_ready"])

    def test_fixed_hmm_argv_and_coordinates(self):
        _, _, plan, sidecar, raw = fixture("hmmsearch")
        self.assertIn("--cut_ga", plan.argv)
        result = adapt_result(raw, plan, sidecar)
        hit = result["hits"][0]
        self.assertEqual(hit["alignment_range"]["start"], 10)
        self.assertEqual(hit["envelope_range"]["start"], 8)
        self.assertEqual(hit["model_accession"], "PF00001.9")
        self.assertIsNone(hit["threshold_evaluation"]["passes_recorded_cutoffs"])

    def test_client_paths_commands_and_flags_rejected(self):
        request, context, plan, _, _ = fixture()
        for key in ("argv", "query_path", "db_path", "command", "execution_enabled", "evalue", "max_target_seqs"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                make_plan({**request, key: "/tmp/arbitrary"}, plan.sequence, context)

    def test_database_path_and_wrong_pair_rejected(self):
        request, context, plan, _, _ = fixture()
        for key in ("/etc/passwd", "../../pfam", "pfam-raw-reviewed", "custom-db"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                make_plan({**request, "database_key": key}, plan.sequence, context)
        with self.assertRaises(ValueError):
            make_plan({**request, "tool_key": "bash"}, plan.sequence, context)

    def test_run_paths_and_identity_rejected(self):
        request, context, plan, _, _ = fixture()
        for value in ("../../etc", "run; touch X", "", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                make_plan(request, plan.sequence, {**context, "run_id": value})
        for key, value in (("project_id", "someone"), ("taxonomy_id", True), ("taxonomy_id", 0)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                make_plan(request, plan.sequence, {**context, key: value})

    def test_fasta_token_and_residue_contract(self):
        for sid in ("a b", ">x", "../x", "x\ny", "x;cmd", "-flag", ""):
            with self.subTest(sid=sid), self.assertRaises(ValueError):
                bind_sequence(sid, "AX", sha(b"AX"))
        for residues in ("A X", "AX ", "A-X", "A*X", "A\tX", "A\u00dfX"):
            with self.subTest(residues=residues), self.assertRaises(ValueError):
                bind_sequence("q", residues, sha(b"AX"))
        identity = bind_sequence("q", "bjouxz", sha(b"BJOUXZ"))
        self.assertEqual(identity.sequence_sha256, sha(b"BJOUXZ"))
        self.assertEqual(identity.raw_sequence_sha256, sha(b"bjouxz"))
        with self.assertRaises(ValueError):
            bind_sequence("q", "AX", sha(b"AA"))

    def test_resource_bounds_and_boolean_rejected(self):
        request, context, plan, _, _ = fixture()
        for key, value in (("threads", 2), ("threads", True), ("wall_seconds", 61), ("memory_bytes", 2 * 1024 ** 3 + 1), ("output_bytes", 1024 ** 2 + 1), ("wall_seconds", 0), ("output_bytes", 1.5)):
            altered = {**request, "limits": {**request["limits"], key: value}}
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                make_plan(altered, plan.sequence, context)

    def test_plan_immutable_and_revalidated(self):
        _, _, plan, sidecar, raw = fixture()
        with self.assertRaises(FrozenInstanceError):
            plan.execution_enabled = True
        for changed in (replace(plan, execution_enabled=True), replace(plan, argv=("bash",)), replace(plan, cwd="/tmp")):
            with self.assertRaises(ValueError):
                adapt_result(raw, changed, sidecar)

    def test_result_sidecar_binding(self):
        _, _, plan, sidecar, raw = fixture()
        for key in ("plan_sha256", "database_snapshot_sha256", "output_sha256", "sequence_sha256", "query_fasta_sha256", "engine_version"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                adapt_result(raw, plan, {**sidecar, key: "wrong"})

    def test_failed_incomplete_truncated_or_overrun(self):
        _, _, plan, sidecar, raw = fixture()
        for key, value in (("exit_code", 1), ("exit_code", False), ("completed", False), ("truncated", True), ("elapsed_seconds", "61"), ("elapsed_seconds", "NaN"), ("output_bytes", 1024 ** 2 + 1), ("synthetic_fixture", "yes")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                adapt_result(raw, plan, {**sidecar, key: value})

    def test_blast_truncated_row_and_line_boundary(self):
        for raw in (BLAST[:-1], b"SYNTHETIC_Q\tSUB\t50\n", b"\n", BLAST.replace(b"description\n", b"description\textra\n")):
            _, _, plan, sidecar, _ = fixture(raw=raw)
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                adapt_result(raw, plan, sidecar)

    def test_blast_coordinates_and_identity(self):
        for index, value in ((0, "OTHER"), (4, "0"), (5, "101"), (6, "0"), (7, "201"), (10, "99"), (3, "2"), (2, "NaN"), (8, "-1"), (12, "101"), (13, "unknown")):
            values = BLAST.decode().rstrip("\n").split("\t")
            values[index] = value
            raw = ("\t".join(values) + "\n").encode()
            _, _, plan, sidecar, _ = fixture(raw=raw)
            with self.subTest(index=index), self.assertRaises(ValueError):
                adapt_result(raw, plan, sidecar)

    def test_empty_blast_requires_completed_sidecar(self):
        _, _, plan, sidecar, raw = fixture(raw=b"")
        self.assertEqual(adapt_result(raw, plan, sidecar)["result_state"], "no_reported_hits")
        with self.assertRaises(ValueError):
            adapt_result(raw, plan, {**sidecar, "completed": False})

    def test_hmm_footer_and_coordinate_failure(self):
        for raw in (hmm(complete=False), hmm((HMM_ROW.replace("10 59 8 61", "0 59 8 61"),)), hmm((HMM_ROW.replace("PF00001.9", "PF00001"),)), hmm().replace(b"# Program: hmmsearch", b"# Program: hmmscan")):
            _, _, plan, sidecar, _ = fixture("hmmsearch", raw)
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                adapt_result(raw, plan, sidecar)

    def test_hmm_no_hits_and_overlap_not_dropped(self):
        for rows, count in (((), 0), ((HMM_ROW, HMM_ROW.replace("PF00001.9", "PF00002.1")), 2)):
            raw = hmm(rows)
            _, _, plan, sidecar, _ = fixture("hmmsearch", raw)
            self.assertEqual(len(adapt_result(raw, plan, sidecar)["hits"]), count)

    def test_unknown_release_preserved_both_tools(self):
        for tool in ("blastp", "hmmsearch"):
            _, _, plan, sidecar, raw = fixture(tool)
            result = adapt_result(raw, plan, sidecar)
            self.assertIsNone(result["provenance"]["database_release"])
            self.assertEqual(result["provenance"]["database_release_status"], "unverified")
            self.assertEqual(result["biological_validity"], "not_established")

    def test_small_resource_caps_enforced_on_output(self):
        request, context, plan, _, raw = fixture()
        request["limits"]["output_bytes"] = 1
        small = make_plan(request, plan.sequence, context)
        _, _, _, sidecar, _ = fixture()
        with self.assertRaises(ValueError):
            adapt_result(raw, small, sidecar)

    def test_more_than_five_subjects_rejected(self):
        raw = b"".join(BLAST.replace(b"SYNTHETIC_SUB", ("SUB" + str(i)).encode()) for i in range(6))
        _, _, plan, sidecar, _ = fixture(raw=raw)
        with self.assertRaises(ValueError):
            adapt_result(raw, plan, sidecar)

    def test_hmm_source_path_contradiction_rejected(self):
        raw = hmm().replace(b"# [ok]", b"# Target file: /arbitrary/query.fasta\n# [ok]")
        _, _, plan, sidecar, _ = fixture("hmmsearch", raw)
        with self.assertRaisesRegex(ValueError, "source path"):
            adapt_result(raw, plan, sidecar)

    def test_provenance_documented_field_set(self):
        schema = json.loads(Path(__import__("cyomics_projects.sequence.domain_runner", fromlist=["x"]).__file__).with_name("provenance-schema.json").read_text(encoding="utf-8"))
        for tool in ("blastp", "hmmsearch"):
            _, _, plan, sidecar, raw = fixture(tool)
            provenance = adapt_result(raw, plan, sidecar)["provenance"]
            self.assertEqual(set(provenance), set(schema["required"]))
            self.assertEqual(set(provenance), set(schema["properties"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
