"""Native-ID RCSB sequence-search seam; stdlib only, default no external I/O."""
from dataclasses import dataclass
from hashlib import sha256
import json
import math
import re

ENDPOINT = "https://search.rcsb.org/rcsbsearch/v2/query"
IDENTITY_CONTRACT = "sequence_identity_review_v0.1"
UNKNOWN = "unknown"
VERSION = "structure-fasta-bootstrap-0.1"


class Invalid(ValueError):
    pass


class Unsupported(ValueError):
    pass


class Disabled(RuntimeError):
    pass


def sequence_hash(sequence):
    # Same assembled sequence contract as STRUCTURE-BOOTSTRAP-001 R2/domain.
    if not isinstance(sequence, str) or not sequence or not sequence.isascii() or not re.fullmatch(r"[ACDEFGHIKLMNPQRSTVWYBXZJUOacdefghiklmnpqrstvwybxzjuo]+", sequence):
        raise Invalid("unsupported_sequence")
    return sha256(sequence.upper().encode("ascii")).hexdigest()


def text(value):
    if not isinstance(value, str) or not value or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value):
        raise Invalid("invalid_context_or_id")
    return value


def number(value, lower, upper=None):
    try:
        valid = type(value) in (float, int) and math.isfinite(value) and value >= lower and (upper is None or value <= upper)
    except OverflowError:
        valid = False
    if not valid:
        raise Invalid("invalid_numeric_parameter")
    return value


@dataclass(frozen=True)
class Query:
    original_id: str
    sequence: str
    sequence_sha256: str
    project_id: str
    dataset_version_id: str
    taxonomy_id: object = UNKNOWN


@dataclass(frozen=True)
class Parameters:
    identity_cutoff: float = 0.3
    evalue_cutoff: float = 0.001
    start: int = 0
    rows: int = 10
    timeout_seconds: float = 10
    sequence_type: str = "protein"


@dataclass(frozen=True)
class Response:
    status: int
    payload: object
    source_url: str


def validate_query(query):
    for value in (query.original_id, query.project_id, query.dataset_version_id):
        text(value)
    if sequence_hash(query.sequence) != query.sequence_sha256:
        raise Invalid("input_hash_mismatch")


def validate_parameters(params):
    if params.sequence_type != "protein":
        raise Unsupported("only_protein_sequence_supported")
    number(params.identity_cutoff, 0, 1)
    number(params.evalue_cutoff, 0)
    number(params.timeout_seconds, 0.001, 60)
    if type(params.start) is not int or params.start < 0 or type(params.rows) is not int or not 1 <= params.rows <= 100:
        raise Invalid("invalid_pagination")  # deliberately bounded below API max


def build_request(query, params=Parameters()):
    validate_query(query)
    validate_parameters(params)
    return {"query": {"type": "terminal", "node_id": 0, "service": "sequence", "parameters": {
        "value": query.sequence.upper(), "sequence_type": "protein", "identity_cutoff": params.identity_cutoff,
        "evalue_cutoff": params.evalue_cutoff}}, "return_type": "polymer_entity", "request_options": {
        "results_content_type": ["experimental"], "results_verbosity": "verbose",
        "paginate": {"start": params.start, "rows": params.rows}}}


def disabled_transport(method, url, payload, timeout_seconds):
    raise Disabled("external_transport_disabled")


def parse_contexts(hit, query):
    contexts = []
    for service in hit.get("services", []):
        if service["service_type"] != "sequence":
            raise Invalid("wrong_result_service")
        for node in service["nodes"]:
            if type(node.get("node_id")) is not int or node["node_id"] != 0:
                raise Invalid("wrong_result_node")
            for context in node.get("match_context", []):
                # Never derive identity from score or cutoffs. Coverage is not a
                # documented response field; leave it unknown, even with bounds.
                identity = context.get("sequence_identity", UNKNOWN)
                if identity != UNKNOWN:
                    number(identity, 0, 1)
                normalized = {"identity": identity, "query_coverage": UNKNOWN,
                              "metric_source": "rcsb_sequence_match_context"}
                for prefix in ("query", "subject"):
                    begin, end = context.get(prefix + "_beg", UNKNOWN), context.get(prefix + "_end", UNKNOWN)
                    for v in (begin, end):
                        if v != UNKNOWN and (type(v) is not int or v < 1):
                            raise Invalid("invalid_alignment_bounds")
                    if begin != UNKNOWN and end != UNKNOWN and begin > end:
                        raise Invalid("invalid_alignment_bounds")
                    if prefix == "query" and any(v != UNKNOWN and v > len(query.sequence) for v in (begin, end)):
                        raise Invalid("query_bounds_exceed_input")
                    normalized[prefix + "_beg"], normalized[prefix + "_end"] = begin, end
                for key in ("evalue", "bitscore", "alignment_length"):
                    value = context.get(key, UNKNOWN)
                    if value != UNKNOWN:
                        number(value, 0)
                        if key != "evalue" and type(value) is not int:
                            raise Invalid("invalid_integer_metric")
                    normalized[key] = value
                # Aligned sequences are deliberately not copied into reports.
                contexts.append(normalized)
    return contexts


def normalize(response, query, params):
    if not isinstance(response, Response) or response.source_url != ENDPOINT:
        raise Invalid("wrong_response_source")
    if response.status in (408, 504):
        raise TimeoutError()
    if response.status == 204:
        return {"status": "no_hit", "candidates": [], "total_count": 0, "more_results": False}
    if response.status != 200:
        raise Invalid("http_" + str(response.status))
    body = response.payload
    if not isinstance(body, dict) or body.get("result_type") != "polymer_entity" or not isinstance(body.get("query_id"), str):
        raise Invalid("invalid_response_envelope")
    total = body.get("total_count")
    if type(total) is not int or total < 1 or not isinstance(body.get("result_set"), list):
        raise Invalid("invalid_response_count")
    hits = body["result_set"]
    if len(hits) > params.rows or len(hits) > max(0, total - params.start) or (not hits and total > params.start):
        raise Invalid("inconsistent_response_count")
    candidates, seen = [], set()
    for hit in hits:
        identifier = hit["identifier"]
        # Native query IDs are unrestricted by PDB identifier syntax. Only
        # returned entity identifiers use this deliberately limited first seam.
        if not isinstance(identifier, str) or not re.fullmatch(r"[0-9][A-Za-z0-9]{3}_[1-9][0-9]*", identifier):
            raise Unsupported("unsupported_result_identifier")
        if identifier in seen:
            raise Invalid("duplicate_result_identifier")
        seen.add(identifier)
        number(hit["score"], 0, 1)
        contexts = parse_contexts(hit, query)
        candidates.append({"identifier": identifier, "evidence_kind": "sequence_search_candidate",
            "requested_content_type": "experimental", "experimental_provenance_verified": False,
            "target_identity_status": "unverified_candidate", "taxonomy_match_status": UNKNOWN,
            "query_sequence_sha256": query.sequence_sha256, "target_sequence_sha256": UNKNOWN,
            "chain_label_asym_ids": UNKNOWN, "chain_auth_asym_ids": UNKNOWN,
            "identity": contexts[0]["identity"] if len(contexts) == 1 else UNKNOWN,
            "query_coverage": UNKNOWN, "match_contexts": contexts, "ranking_score": hit["score"],
            "database_version": UNKNOWN, "engine": "MMseqs2", "engine_version": UNKNOWN})
    return {"status": "hit" if candidates else "no_hit", "candidates": candidates,
            "total_count": total, "more_results": params.start + len(hits) < total,
            "remote_query_id": body["query_id"]}


class Adapter:
    def __init__(self, transport=None, *, data_origin="unknown"):
        if data_origin not in {"unknown", "synthetic_fixture", "real_external"}:
            raise Invalid("invalid_data_origin")
        self.transport = transport or disabled_transport
        self.data_origin = data_origin

    def search(self, query, params=Parameters()):
        result = {"adapter_version": VERSION, "sequence_identity_contract": IDENTITY_CONTRACT,
            "status": "error", "reason": UNKNOWN, "data_origin": self.data_origin, "candidates": [],
            "original_id": query.original_id, "project_id": query.project_id,
            "dataset_version_id": query.dataset_version_id, "query_taxonomy_id": query.taxonomy_id,
            "query_sequence_sha256": query.sequence_sha256, "source_url": ENDPOINT,
            "api_contract_version": "2.6.0", "authorization_verified": False,
            "parameters": {k: v for k, v in vars(params).items()}, "transport_invoked": False}
        try:
            payload = build_request(query, params)
            result["request_payload_sha256"] = sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            # Default capability rejects I/O without invoking any external client.
            if self.transport is disabled_transport:
                raise Disabled()
            result["transport_invoked"] = True
            response = self.transport("POST", ENDPOINT, payload, params.timeout_seconds)
            result.update(normalize(response, query, params))
            result["response_payload_sha256"] = sha256(json.dumps(response.payload, sort_keys=True).encode()).hexdigest()
        except Disabled:
            result.update(status="disabled", reason="external_transport_disabled")
        except TimeoutError:
            result.update(status="timeout", reason="transport_timeout")
        except Unsupported as exc:
            result.update(status="unsupported", reason=str(exc), candidates=[])
        except (Invalid, KeyError, ValueError, TypeError, AttributeError) as exc:
            result.update(status="error", reason=str(exc) if isinstance(exc, Invalid) else "invalid_payload", candidates=[])
        except OSError:
            result.update(status="error", reason="transport_error", candidates=[])
        return result
