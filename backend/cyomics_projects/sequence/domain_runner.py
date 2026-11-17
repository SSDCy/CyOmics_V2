"""Pure offline candidate. No process/network/filesystem execution interface."""
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from uuid import UUID

from .vendor_domain_r1 import normalize_domtblout, normalize_sequence

CONTRACT = "sequence_identity_review_v0.1"
FIELDS = "qseqid sseqid pident length qstart qend sstart send evalue bitscore qlen slen qcovhsp staxids stitle".split()
ROOT = None  # UNCONFIGURED: no worker workspace or filesystem path by default
# Server-owned reviewed candidates. These do not assert current availability.
CONFIG = {
    "swissprot-reviewed": {"tool": "blastp", "binary": "/usr/local/bin/blastp", "version": "2.17.0+",
        "path": None, "database": "Swiss-Prot",
        "release": None, "release_status": "unverified", "format_version": 5, "raw_sha256": None,
        "component_sha256": {"pin": "659ae2fe5fc35f8d469811b2477d3a9f86b4de2803e5005eff9e24cb2abf1b36",
                             "pjs": "a43ff788fd449c6648b471397e17320e0054a77cf07a369f0cfd04f36c7fba7c"},
        "evidence": "BLAST-GOLDEN-001"},
    "pfam-raw-reviewed": {"tool": "hmmsearch", "binary": "/usr/bin/hmmsearch", "version": "3.3.2",
        "path": None, "database": "Pfam",
        "release": None, "release_status": "unverified",
        "raw_sha256": "40b742a1bebca10f6b86c1364624dbdc34f79890954777eb95f8a48428de39d3",
        "component_sha256": {}, "evidence": "PFAM-GOLDEN-001"},
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def exact_keys(value, expected, name):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError(name + " fields mismatch")


def sha_field(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("invalid SHA256")
    return value


def decimal(value):
    if not isinstance(value, str):
        raise ValueError("decimal must be explicit string")
    try:
        n = Decimal(value)
    except InvalidOperation:
        raise ValueError("invalid decimal") from None
    if not n.is_finite():
        raise ValueError("nonfinite decimal")
    return n


@dataclass(frozen=True)
class SequenceIdentity:
    id: str
    length: int
    sequence_sha256: str
    raw_sequence_sha256: str
    query_fasta_sha256: str


def bind_sequence(sequence_id, residues, expected_sequence_sha256):
    if not isinstance(sequence_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:|+-]{0,199}", sequence_id):
        raise ValueError("invalid exact FASTA token")
    canonical = normalize_sequence(residues)
    digest = sha(canonical.encode("ascii"))
    if digest != sha_field(expected_sequence_sha256):
        raise ValueError("sequence SHA binding mismatch")
    return SequenceIdentity(sequence_id, len(canonical), digest, sha(residues.encode("ascii")),
                            sha((">" + sequence_id + "\n" + canonical + "\n").encode("ascii")))


@dataclass(frozen=True)
class Plan:
    tool_key: str
    database_key: str
    run_id: str
    project_id: str | None
    dataset_version_id: str | None
    taxonomy_id: int | None
    sequence: SequenceIdentity
    threads: int
    wall_seconds: int
    memory_bytes: int
    output_bytes: int
    argv: tuple
    cwd: str
    execution_enabled: bool = False


def make_plan(request, sequence, server_context):
    """Context/config come from a future trusted worker; this supplies no auth."""
    exact_keys(request, ("tool_key", "database_key", "limits"), "request")
    exact_keys(server_context, ("run_id", "project_id", "dataset_version_id", "taxonomy_id"), "context")
    exact_keys(request["limits"], ("threads", "wall_seconds", "memory_bytes", "output_bytes"), "limits")
    config = CONFIG.get(request["database_key"]) if isinstance(request["database_key"], str) else None
    if not config or request["tool_key"] != config["tool"]:
        raise ValueError("unsupported fixed tool/database pairing")
    if ROOT is None or config["path"] is None:
        raise ValueError("reviewed resource paths unconfigured; no availability conclusion")
    run_id = server_context["run_id"]
    if not isinstance(run_id, str) or str(UUID(run_id)) != run_id:
        raise ValueError("run ID must be canonical UUID")
    for key in ("project_id", "dataset_version_id"):
        value = server_context[key]
        if value is not None and (not isinstance(value, str) or str(UUID(value)) != value):
            raise ValueError("context identity must be UUID or explicit unknown")
    taxonomy = server_context["taxonomy_id"]
    if taxonomy is not None and (type(taxonomy) is not int or taxonomy <= 0):
        raise ValueError("taxonomy must be positive integer or unknown")
    if not isinstance(sequence, SequenceIdentity) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:|+-]{0,199}", sequence.id):
        raise ValueError("bound sequence identity required")
    for value in (sequence.sequence_sha256, sequence.raw_sequence_sha256, sequence.query_fasta_sha256):
        sha_field(value)
    if type(sequence.length) is not int or not 1 <= sequence.length <= 100000:
        raise ValueError("invalid sequence length")
    limits = request["limits"]
    for key, maximum in (("threads", 1), ("wall_seconds", 60), ("memory_bytes", 2 * 1024 ** 3), ("output_bytes", 1024 ** 2)):
        if type(limits[key]) is not int or not 1 <= limits[key] <= maximum:
            raise ValueError("resource limit out of range: " + key)
    cwd = ROOT + "/" + run_id
    query = cwd + "/query.fasta"
    if config["tool"] == "blastp":
        argv = (config["binary"], "-query", query, "-db", config["path"], "-num_threads", "1",
                "-max_target_seqs", "5", "-evalue", "1e-5", "-outfmt", "6 " + " ".join(FIELDS))
    else:
        argv = (config["binary"], "--cpu", "1", "--cut_ga", "--noali", "--domtblout", cwd + "/domains.domtblout",
                "-o", "/dev/null", config["path"], query)
    return Plan(config["tool"], request["database_key"], run_id, server_context["project_id"],
                server_context["dataset_version_id"], taxonomy, sequence, **limits, argv=argv, cwd=cwd)


def validate_plan(plan):
    if not isinstance(plan, Plan):
        raise ValueError("Plan required")
    request = {"tool_key": plan.tool_key, "database_key": plan.database_key,
               "limits": {k: getattr(plan, k) for k in ("threads", "wall_seconds", "memory_bytes", "output_bytes")}}
    context = {k: getattr(plan, k) for k in ("run_id", "project_id", "dataset_version_id", "taxonomy_id")}
    if make_plan(request, plan.sequence, context) != plan:
        raise ValueError("plan mutated or execution enabled")


def plan_sha256(plan):
    validate_plan(plan)
    return sha(json.dumps({"plan": asdict(plan), "database_snapshot": CONFIG[plan.database_key]}, sort_keys=True, separators=(",", ":")).encode())


def database_snapshot_sha256(plan):
    validate_plan(plan)
    return sha(json.dumps(CONFIG[plan.database_key], sort_keys=True, separators=(",", ":")).encode())


def check_result(raw, plan, sidecar):
    validate_plan(plan)
    exact_keys(sidecar, ("plan_sha256", "database_snapshot_sha256", "output_sha256", "sequence_sha256", "query_fasta_sha256", "engine_version",
                        "exit_code", "completed", "truncated", "elapsed_seconds", "output_bytes", "synthetic_fixture"), "sidecar")
    if not isinstance(raw, bytes) or len(raw) > plan.output_bytes:
        raise ValueError("output type/size limit")
    if (sidecar["plan_sha256"] != plan_sha256(plan) or sidecar["database_snapshot_sha256"] != database_snapshot_sha256(plan) or sidecar["output_sha256"] != sha(raw) or
            sidecar["sequence_sha256"] != plan.sequence.sequence_sha256 or
            sidecar["query_fasta_sha256"] != plan.sequence.query_fasta_sha256):
        raise ValueError("result/source binding mismatch")
    if sidecar["engine_version"] != CONFIG[plan.database_key]["version"]:
        raise ValueError("engine version mismatch")
    if type(sidecar["exit_code"]) is not int or sidecar["exit_code"] != 0 or sidecar["completed"] is not True or sidecar["truncated"] is not False:
        raise ValueError("incomplete/failed/truncated result")
    elapsed = decimal(sidecar["elapsed_seconds"])
    if not 0 <= elapsed <= plan.wall_seconds:
        raise ValueError("elapsed time out of range")
    size = sidecar["output_bytes"]
    if type(size) is not int or not len(raw) <= size <= plan.output_bytes or type(sidecar["synthetic_fixture"]) is not bool:
        raise ValueError("output accounting/fixture flag invalid")


def parse_blast(raw, plan):
    if raw and not raw.endswith(b"\n"):
        raise ValueError("TSV lacks complete line terminator")
    hits = []
    for ordinal, line in enumerate(raw.decode("utf-8").splitlines(), 1):
        values = line.split("\t")
        if len(values) != len(FIELDS):
            raise ValueError("TSV requires exact 15 fields")
        row = dict(zip(FIELDS, values))
        try:
            start, end, qlen, ss, se, slen, length = [int(row[k]) for k in ("qstart", "qend", "qlen", "sstart", "send", "slen", "length")]
        except ValueError:
            raise ValueError("invalid TSV integer") from None
        if row["qseqid"] != plan.sequence.id or qlen != plan.sequence.length or not row["sseqid"]:
            raise ValueError("TSV sequence identity mismatch")
        if not 1 <= start <= end <= qlen or not 1 <= ss <= se <= slen or length < max(end - start + 1, se - ss + 1):
            raise ValueError("invalid TSV coordinates/length")
        if not 0 <= decimal(row["pident"]) <= 100 or not 0 <= decimal(row["qcovhsp"]) <= 100 or not 0 <= decimal(row["evalue"]) <= Decimal("1e-5"):
            raise ValueError("invalid TSV score/E-value")
        decimal(row["bitscore"])
        if not re.fullmatch(r"(?:0|[1-9][0-9]*)(?:;[1-9][0-9]*)*", row["staxids"]):
            raise ValueError("invalid subject taxonomy field")
        hits.append({**row, "source_row": ordinal, "sequence_sha256": plan.sequence.sequence_sha256,
                     "coordinate_system": "1-based-inclusive", "taxonomy_status": "unknown" if row["staxids"] == "0" else "reported_subject_only"})
    if len({h["sseqid"] for h in hits}) > 5:
        raise ValueError("distinct subjects exceed recorded limit")
    return hits


def adapt_result(raw, plan, sidecar):
    """A future trusted worker must attest sidecar; consistency is not authenticity."""
    check_result(raw, plan, sidecar)
    config = CONFIG[plan.database_key]
    seq = plan.sequence
    context = {"project_id": plan.project_id, "dataset_version_id": plan.dataset_version_id,
               "taxonomy_id": plan.taxonomy_id, "run_id": plan.run_id,
               "sequence_identity_contract": CONTRACT, "synthetic_fixture": sidecar["synthetic_fixture"],
               "input_fasta_sha256": seq.query_fasta_sha256, "input_sequence_sha256": {seq.id: seq.sequence_sha256},
               "domtblout_sha256": sha(raw), "source": {"tool": "HMMER", "program": "hmmsearch",
                   "tool_version": config["version"], "database": "Pfam", "database_version": "unverified",
                   "database_version_status": "unverified", "database_sha256": config["raw_sha256"]},
               "threshold_policy": {"strategy": "model_gathering", "description": "--cut_ga planned; per-hit numeric GA unverified", "models": {}}}
    if plan.tool_key == "blastp":
        hits = parse_blast(raw, plan)
    else:
        for field, expected in (("Query file", config["path"]), ("Target file", plan.cwd + "/query.fasta")):
            observed = re.findall(r"^#\s*" + field + r":\s*(.*?)\s*$", raw.decode("utf-8"), re.M)
            if observed and any(value != expected for value in observed):
                raise ValueError("domtblout source path contradicts fixed plan")
        manifest = {"sequence_identity_contract": CONTRACT, "source": {"sha256": seq.query_fasta_sha256},
                    "identity": {k: getattr(plan, k) for k in ("project_id", "dataset_version_id", "taxonomy_id")},
                    "sequences": [{"id": seq.id, "length": seq.length, "sequence_sha256": seq.sequence_sha256}]}
        hits = normalize_domtblout(raw, context, manifest)["hits"]
    return {"schema_version": 1, "schema": "domain_runner_result_review_v0.1",
            "kind": "synthetic_fixture" if sidecar["synthetic_fixture"] else "reported_candidates_pending_worker_attestation",
            "result_state": "reported_hits" if hits else "no_reported_hits", "hits": hits,
            "execution_enabled": False, "platform_analysis_ready": False, "biological_validity": "not_established",
            "provenance": {"plan_sha256": plan_sha256(plan), "run_id": plan.run_id,
                "project_id": plan.project_id, "dataset_version_id": plan.dataset_version_id, "taxonomy_id": plan.taxonomy_id,
                "sequence": asdict(seq), "sequence_identity_contract": CONTRACT,
                "tool": plan.tool_key, "tool_version": config["version"], "tool_version_status": "prior_cli_snapshot_not_reprobed",
                "database_key": plan.database_key, "database_snapshot_sha256": database_snapshot_sha256(plan), "database_release": config["release"], "database_release_status": config["release_status"],
                "database_raw_sha256": config["raw_sha256"], "database_component_sha256": dict(config["component_sha256"]),
                "prior_evidence": config["evidence"], "argv": list(plan.argv), "output_sha256": sha(raw),
                "sidecar": dict(sidecar), "threshold_policy": "--cut_ga; numeric model GA unverified" if plan.tool_key == "hmmsearch" else "evalue 1e-5; max_target_seqs 5"},
            "limitations": ["No execution API; resource limits declared only, future worker must enforce",
                            "Sidecar and identity context require trusted worker ownership and authorization",
                            "TSV completion cannot be proved from rows alone; worker sidecar remains mandatory",
                            "Unknown database release is preserved; homology/domain candidates do not validate function"]}
