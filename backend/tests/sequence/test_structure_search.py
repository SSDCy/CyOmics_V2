"""All sequences/responses below are explicitly SYNTHETIC fixtures."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import unittest
from cyomics_projects.sequence.structure_search import Adapter, Query, Parameters, Response, Invalid, ENDPOINT, build_request, sequence_hash

SEQ = "ACDEFGHIKLMNPQRSTVWYBJOUXZ"
Q = Query("Pt_Chr_SYNTHETIC.1", SEQ, sequence_hash(SEQ), "synthetic-project", "synthetic-dataset-v1")
CONTEXT = {"sequence_identity": 0.8, "evalue": 0.00001, "bitscore": 40,
           "query_beg": 1, "query_end": 20, "subject_beg": 2, "subject_end": 21,
           "query_length": 20, "subject_length": 20, "alignment_length": 20}
HIT = {"identifier": "1ABC_1", "score": 0.95, "services": [{"service_type": "sequence", "nodes": [
    {"node_id": 0, "original_score": 40, "norm_score": 0.95, "match_context": [CONTEXT]}]}]}
BODY = {"query_id": "synthetic-remote-query", "result_type": "polymer_entity", "total_count": 1, "result_set": [HIT]}


class FixtureTransport:
    def __init__(self, response=None, error=None):
        self.response = response or Response(200, deepcopy(BODY), ENDPOINT)
        self.error = error
        self.calls = []

    def __call__(self, method, url, payload, timeout):
        self.calls.append((method, url, payload, timeout))
        if self.error:
            raise self.error
        return self.response


def run(body=BODY, status=200, params=Parameters()):
    return Adapter(FixtureTransport(Response(status, deepcopy(body), ENDPOINT)), data_origin="synthetic_fixture").search(Q, params)


class Tests(unittest.TestCase):
    def test_native_id_build_request_never_forges_accession(self):
        request = build_request(Q)
        self.assertEqual(request["query"]["parameters"]["value"], SEQ)
        self.assertEqual(request["query"]["service"], "sequence")
        self.assertEqual(request["return_type"], "polymer_entity")
        self.assertNotIn(Q.original_id, repr(request))
        self.assertNotIn(Q.project_id, repr(request))
        self.assertNotIn("accession", repr(request))
        self.assertEqual(request["request_options"]["results_content_type"], ["experimental"])

    def test_request_subset_matches_official_downloaded_schema(self):
        schema = json.loads(Path(__file__).with_name("json-schema-rcsb_search_query_parameters_sequence.json").read_text())
        params = build_request(Q)["query"]["parameters"]
        self.assertTrue(set(params) <= set(schema["properties"]))
        self.assertTrue(set(schema["required"]) <= set(params))
        self.assertIn(params["sequence_type"], schema["properties"]["sequence_type"]["enum"])
        self.assertNotIn("target", params)

    def test_default_disabled_after_local_validation(self):
        result = Adapter().search(Q)
        self.assertEqual(result["status"], "disabled")
        self.assertFalse(result["transport_invoked"])
        self.assertNotIn(SEQ, repr(result))

    def test_synthetic_hit_keeps_candidate_boundary(self):
        result = run()
        self.assertEqual(result["status"], "hit")
        self.assertEqual(result["data_origin"], "synthetic_fixture")
        e = result["candidates"][0]
        self.assertEqual(e["identity"], 0.8)
        self.assertEqual(e["query_coverage"], "unknown")
        self.assertFalse(e["experimental_provenance_verified"])
        self.assertEqual(e["target_identity_status"], "unverified_candidate")
        self.assertEqual(e["chain_label_asym_ids"], "unknown")
        self.assertEqual(result["original_id"], Q.original_id)
        self.assertFalse(result["authorization_verified"])

    def test_no_hit_204(self):
        self.assertEqual(run(None, 204)["status"], "no_hit")

    def test_timeout_exception_and_http(self):
        t = FixtureTransport(error=TimeoutError())
        self.assertEqual(Adapter(t).search(Q)["status"], "timeout")
        for status in (408, 504):
            self.assertEqual(run(None, status)["status"], "timeout")

    def test_unsupported_type_no_io(self):
        t = FixtureTransport()
        r = Adapter(t).search(Q, replace(Parameters(), sequence_type="rna"))
        self.assertEqual(r["status"], "unsupported")
        self.assertEqual(t.calls, [])

    def test_wrong_origin_redirect_rejected(self):
        t = FixtureTransport(Response(200, BODY, "https://example.org/forged"))
        self.assertEqual(Adapter(t).search(Q)["reason"], "wrong_response_source")

    def test_hash_mismatch_no_io(self):
        t = FixtureTransport()
        self.assertEqual(Adapter(t).search(replace(Q, sequence_sha256="0" * 64))["reason"], "input_hash_mismatch")
        self.assertEqual(t.calls, [])

    def test_identity_contract_ascii_ambiguity_case_whitespace(self):
        self.assertEqual(sequence_hash(SEQ.lower()), sha256(SEQ.encode()).hexdigest())
        self.assertNotEqual(sequence_hash("BJOUXZ"), sequence_hash("XXXXXX"))
        for sequence in ("", "AC DE", "AC\nDE", "AC-DE", "AC*DE", "AC\u212aDE"):
            with self.assertRaises(Invalid):
                sequence_hash(sequence)

    def test_invalid_numeric_or_pagination_parameters_no_io(self):
        for kwargs in ({"identity_cutoff": 80}, {"identity_cutoff": True}, {"evalue_cutoff": -1},
            {"evalue_cutoff": float("nan")}, {"evalue_cutoff": 10**400}, {"identity_cutoff": float("inf")}, {"rows": 0}, {"rows": 101},
            {"rows": True}, {"start": -1}, {"start": 0.5}, {"timeout_seconds": 0}):
            t = FixtureTransport()
            self.assertEqual(Adapter(t).search(Q, replace(Parameters(), **kwargs))["status"], "error")
            self.assertEqual(t.calls, [])

    def test_no_identity_or_coverage_inferred_from_ranking(self):
        body = deepcopy(BODY)
        body["result_set"][0].pop("services")
        e = run(body)["candidates"][0]
        self.assertEqual(e["identity"], "unknown")
        self.assertEqual(e["query_coverage"], "unknown")

    def test_multiple_alignment_contexts_not_merged(self):
        body = deepcopy(BODY)
        body["result_set"][0]["services"][0]["nodes"][0]["match_context"].append(deepcopy(CONTEXT))
        e = run(body)["candidates"][0]
        self.assertEqual(e["identity"], "unknown")
        self.assertEqual(len(e["match_contexts"]), 2)

    def test_invalid_alignment_metric_and_bounds_rejected(self):
        for key, value in (("sequence_identity", 95), ("sequence_identity", True), ("query_end", 500),
                           ("query_beg", 40), ("query_beg", 0), ("subject_end", -1), ("bitscore", 40.5), ("alignment_length", True)):
            body = deepcopy(BODY)
            body["result_set"][0]["services"][0]["nodes"][0]["match_context"][0][key] = value
            self.assertEqual(run(body)["status"], "error")

    def test_service_or_node_mismatch_rejected(self):
        for service, node in (("text", 0), ("sequence", 1), ("sequence", False)):
            body = deepcopy(BODY)
            body["result_set"][0]["services"][0]["service_type"] = service
            body["result_set"][0]["services"][0]["nodes"][0]["node_id"] = node
            self.assertEqual(run(body)["status"], "error")

    def test_pagination_is_explicit_partial_result(self):
        body = deepcopy(BODY)
        body["total_count"] = 2
        r = run(body, params=replace(Parameters(), rows=1))
        self.assertTrue(r["more_results"])
        self.assertEqual(len(r["candidates"]), 1)

    def test_http_failure_and_malformed_payload_are_errors(self):
        for status in (400, 404, 429, 500):
            self.assertEqual(run({}, status)["status"], "error")
        for payload in ([], {}, {"result_type": "entry"}, {**BODY, "total_count": 0}):
            self.assertEqual(run(payload)["status"], "error")

    def test_unsupported_result_id_is_not_experimental_evidence(self):
        body = deepcopy(BODY)
        body["result_set"][0]["identifier"] = "AF_SYNTHETIC"
        r = run(body)
        self.assertEqual(r["status"], "unsupported")
        self.assertEqual(r["candidates"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
