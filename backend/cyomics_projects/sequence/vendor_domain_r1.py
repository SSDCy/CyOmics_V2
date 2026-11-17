"""Offline candidate only: FASTA audit and HMMER3 Pfam domtblout normalization.

No engine, database, network, application persistence, or scientific hit validation.
"""
import argparse
from collections import Counter
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import statistics


VERSION = "domain-bootstrap-0.1-r1"
SEQUENCE_IDENTITY_CONTRACT = "sequence_identity_review_v0.1"
STANDARD_AA = set("ACDEFGHIKLMNPQRSTVWY")
EXTENDED_AA = set("BJOUXZ")
SHA_RE = re.compile(r"[0-9a-f]{64}\Z")


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def normalize_sequence(sequence):
    """Canonical residue identity after explicit input-format decoding.

    ASCII only; uppercase; no whitespace, gaps, stops or silent replacements.
    Ambiguity/extended B/J/O/U/X/Z is retained, never converted to X.
    """
    if not isinstance(sequence, str) or not sequence or not sequence.isascii():
        raise ValueError("sequence must be nonempty ASCII residues")
    normalized = sequence.upper()
    if any(c not in STANDARD_AA | EXTENDED_AA for c in normalized):
        raise ValueError("assembled sequence contains whitespace/gap/stop/illegal residue")
    return normalized


def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def audit_fasta(path):
    """ASCII, first header token; explicitly decode FASTA wrapping, not whitespace.

    Raw residue SHA joins sequence lines without trimming. Canonical sequence SHA
    is uppercase validated ASCII; invalid sequences retain raw SHA and get no
    canonical hash. The audit remains informative even for invalid residues.
    """
    path = Path(path)
    raw = path.read_bytes()
    text = raw.decode("ascii")
    records, sequence, header = [], [], None
    blank_lines = whitespace_lines = 0

    def finish():
        if header is not None:
            tokens = header.split()
            if not tokens:
                raise ValueError("FASTA header missing first-token ID")
            seq = "".join(sequence)
            try:
                canonical_sha = sha_bytes(normalize_sequence(seq).encode("ascii"))
                status = "valid_canonical_residues"
            except ValueError:
                canonical_sha, status = None, "invalid_no_canonical_sequence_identity"
            records.append({"id": tokens[0], "record_ordinal_1based": len(records) + 1,
                            "length": len(seq), "sequence_sha256": canonical_sha,
                            "raw_sequence_sha256": sha_bytes(seq.encode("ascii")),
                            "normalization_status": status, "characters": Counter(seq)})

    lines = text.split("\n")  # Only FASTA LF/CRLF wrapping; do not treat VT/FF as line breaks.
    if lines[-1] == "":
        lines.pop()
    for line_no, raw_line in enumerate(lines, 1):
        line = raw_line.removesuffix("\r")
        if line == "":
            blank_lines += 1
            continue
        if line.startswith(">"):
            finish()
            header, sequence = line[1:], []
        else:
            if header is None:
                raise ValueError(f"sequence before header at line {line_no}")
            if any(c.isspace() for c in line):
                whitespace_lines += 1
            sequence.append(line)
    finish()
    if not records:
        raise ValueError("empty FASTA")
    ids = Counter(r["id"] for r in records)
    chars = sum((r["characters"] for r in records), Counter())
    lengths = [r["length"] for r in records]
    duplicates = {k: n for k, n in ids.items() if n > 1}
    file_info = {"path": str(path.resolve()), "bytes": len(raw), "sha256": sha_bytes(raw)}
    stats = {
        "schema_version": 1, "kind": "real_original_fasta_audit", "parser_version": VERSION,
        "source": file_info, "records": len(records), "unique_first_token_ids": len(ids),
        "duplicate_ids": duplicates, "duplicate_record_excess": sum(n - 1 for n in duplicates.values()),
        "length": {"min": min(lengths), "max": max(lengths), "median": statistics.median(lengths),
                   "total_residues": sum(lengths)}, "empty_sequences": lengths.count(0),
        "character_counts": dict(sorted(chars.items())),
        "non_standard_amino_acids": {k: n for k, n in sorted(chars.items()) if k not in STANDARD_AA},
        "extended_ambiguity_amino_acids": {k: n for k, n in sorted(chars.items()) if k in EXTENDED_AA},
        "invalid_under_extended_alphabet": {k: n for k, n in sorted(chars.items())
                                            if k.upper() not in STANDARD_AA | EXTENDED_AA},
        "lowercase_characters": sum(n for k, n in chars.items() if k.islower()),
        "stop_characters": chars.get("*", 0), "blank_lines": blank_lines,
        "sequence_lines_with_internal_whitespace": whitespace_lines,
        "sequence_identity_contract": SEQUENCE_IDENTITY_CONTRACT,
        "sequence_hash_policy": "explicit FASTA line-wrap decoding; uppercase validated ASCII residues; reject assembled whitespace/gap/stop; retain BJOUXZ",
        "raw_sequence_hash_policy": "ASCII concat of original sequence lines without trimming, case changes or residue removal; source file SHA separately includes headers/newlines",
        "invalid_sequence_count": sum(r["sequence_sha256"] is None for r in records),
        "id_policy": "first whitespace-delimited token after >; exact case-sensitive; no alias inference",
    }
    # Small, deterministic boundary sample. Stable tie break is ID then file ordinal.
    ordered = sorted(records, key=lambda r: (r["length"], r["id"], r["record_ordinal_1based"]))
    picks = [("first_record", records[0]), ("last_record", records[-1]),
             ("shortest_by_length_id_ordinal", ordered[0]),
             ("median_rank_floor_by_length_id_ordinal", ordered[(len(ordered) - 1) // 2]),
             ("longest_by_length_id_ordinal", ordered[-1])]
    sample = {}
    for rule, record in picks:
        ordinal = record["record_ordinal_1based"]
        entry = sample.setdefault(ordinal, {k: v for k, v in record.items() if k != "characters"})
        entry.setdefault("selection_rules", []).append(rule)
    manifest = {
        "schema_version": 1, "kind": "real_fasta_sequence_identity_manifest_no_domain_results",
        "source": file_info, "source_record_count": len(records),
        "sequence_identity_contract": SEQUENCE_IDENTITY_CONTRACT,
        "sequence_hash_policy": stats["sequence_hash_policy"],
        "raw_sequence_hash_policy": stats["raw_sequence_hash_policy"],
        "selection_rule": "union of first,last,shortest,median-floor,longest; length ties by exact ID then ordinal",
        "identity": {"project_id": None, "dataset_version_id": None, "taxonomy_id": None},
        "identity_status": "unfrozen; filename is not authoritative taxonomy or accession mapping",
        "sequences": sorted(sample.values(), key=lambda r: r["record_ordinal_1based"]),
    }
    return stats, manifest


def require_text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"missing {field}")
    return value


def require_sha(value, field):
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise ValueError(f"invalid {field}")
    return value


def number(raw, field, minimum=None, maximum=None):
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        raise ValueError(f"invalid numeric {field}") from None
    if not value.is_finite() or (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
        raise ValueError(f"out of range {field}")
    return value


def interval(start, end, length, field):
    if not 1 <= start <= end <= length:
        raise ValueError(f"invalid {field} coordinates")
    return {"start": start, "end": end, "coordinate_system": "1-based-inclusive"}


def normalize_domtblout(raw, context, manifest):
    """Parse hmmscan or hmmsearch; explicit orientation, no implicit alias mapping.

    context is a trusted run sidecar candidate, not client-authoritative metadata.
    domtblout does NOT carry sequence hashes or Pfam release/GA values. Supplied
    metadata must be independently attested by a future worker before real use.
    This function verifies consistency/bindings, not engine authenticity.
    """
    require_sha(context.get("domtblout_sha256"), "domtblout_sha256")
    if sha_bytes(raw) != context["domtblout_sha256"]:
        raise ValueError("source domtblout SHA mismatch")
    source = context.get("source", {})
    if source.get("tool") != "HMMER" or source.get("database") != "Pfam":
        raise ValueError("source must be HMMER/Pfam")
    program = source.get("program")
    if program not in ("hmmscan", "hmmsearch"):
        raise ValueError("unsupported source program")
    for key in ("tool_version", "database_version"):
        require_text(source.get(key), key)
    require_sha(source.get("database_sha256"), "database_sha256")
    require_text(context.get("run_id"), "run_id")
    for key in ("project_id", "dataset_version_id", "taxonomy_id"):
        if key not in context:
            raise ValueError(f"missing identity field {key}; use explicit null when unfrozen")
    input_sha = require_sha(context.get("input_fasta_sha256"), "input_fasta_sha256")
    if manifest.get("source", {}).get("sha256") != input_sha:
        raise ValueError("source FASTA mismatch")
    if (context.get("sequence_identity_contract") != SEQUENCE_IDENTITY_CONTRACT or
            manifest.get("sequence_identity_contract") != SEQUENCE_IDENTITY_CONTRACT):
        raise ValueError("sequence identity contract missing or mismatch")
    if manifest.get("identity") != {k: context[k] for k in ("project_id", "dataset_version_id", "taxonomy_id")}:
        raise ValueError("project/dataset/taxonomy mismatch")
    records = manifest.get("sequences", [])
    sequences = {}
    for item in records:
        sid = require_text(item.get("id"), "sequence ID")
        require_sha(item.get("sequence_sha256"), "sequence_sha256")
        if type(item.get("length")) is not int or item["length"] <= 0 or sid in sequences:
            raise ValueError("invalid/duplicate sequence manifest entry")
        sequences[sid] = item
    if not sequences:
        raise ValueError("empty sequence manifest")
    expected_sequences = context.get("input_sequence_sha256")
    if expected_sequences != {sid: entry["sequence_sha256"] for sid, entry in sequences.items()}:
        raise ValueError("sequence SHA binding mismatch")
    policy = context.get("threshold_policy", {})
    if policy.get("strategy") not in ("record_only", "model_gathering"):
        raise ValueError("explicit threshold strategy required")
    require_text(policy.get("description"), "threshold policy description")
    if type(context.get("synthetic_fixture")) is not bool:
        raise ValueError("explicit synthetic_fixture boolean required")
    text = raw.decode("utf-8")
    headers = {}
    for line in text.splitlines():
        m = re.match(r"^#\s*(Program|Version):\s*(\S+)", line)
        if m:
            if m[1] in headers and headers[m[1]] != m[2]:
                raise ValueError("conflicting source headers")
            headers[m[1]] = m[2]
    if headers.get("Program") != program or headers.get("Version") != source["tool_version"]:
        raise ValueError("source program/version header missing or mismatch")
    nonblank = [line.strip() for line in text.splitlines() if line.strip()]
    if not nonblank or nonblank[-1] != "# [ok]":
        raise ValueError("missing completion footer; cannot interpret truncated file as no hits")
    hits, seen = [], set()
    for line_no, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = line.split(maxsplit=22)
        if len(fields) < 22:
            raise ValueError(f"line {line_no}: expected 22 fixed fields")
        tname, tacc, qname, qacc = fields[0], fields[1], fields[3], fields[4]
        try:
            tlen, qlen = int(fields[2]), int(fields[5])
            ordinal, total = int(fields[9]), int(fields[10])
            hf, ht, af, at, ef, et = map(int, fields[15:21])
        except ValueError:
            raise ValueError(f"line {line_no}: invalid integer") from None
        if program == "hmmscan":
            model, accession, mlen, sid, slen = tname, tacc, tlen, qname, qlen
        else:
            model, accession, mlen, sid, slen = qname, qacc, qlen, tname, tlen
        if not re.fullmatch(r"PF[0-9]{5}\.[0-9]+", accession):
            raise ValueError("missing/invalid versioned Pfam model accession")
        if sid not in sequences or sequences[sid]["length"] != slen:
            raise ValueError("sequence ID/length mismatch")
        if not 1 <= ordinal <= total:
            raise ValueError("invalid domain ordinal/total")
        key = (sid, accession, ordinal)
        if key in seen:
            raise ValueError("duplicate domain ordinal for same sequence/model")
        seen.add(key)
        hmm_range = interval(hf, ht, mlen, "HMM")
        alignment = interval(af, at, slen, "alignment")
        envelope = interval(ef, et, slen, "envelope")
        if not ef <= af <= at <= et:
            raise ValueError("alignment outside envelope")
        for index in (6, 11, 12):
            number(fields[index], "E-value", 0)
        for index in (7, 13):
            number(fields[index], "bit score")
        for index in (8, 14):
            number(fields[index], "bias", 0)
        number(fields[21], "posterior accuracy", 0, 1)
        evaluation = {"status": "not_evaluated", "passes_recorded_cutoffs": None,
                      "biological_validity": "not_established"}
        if policy["strategy"] == "model_gathering":
            ga = policy.get("models", {}).get(accession)
            if ga is not None:
                seq_ga = number(ga.get("sequence_bits"), "sequence GA")
                dom_ga = number(ga.get("domain_bits"), "domain GA")
                evaluation.update(status="compared_with_supplied_model_GA",
                                  passes_recorded_cutoffs=number(fields[7], "score") >= seq_ga and
                                  number(fields[13], "score") >= dom_ga,
                                  sequence_ga_bits=str(seq_ga), domain_ga_bits=str(dom_ga))
            else:
                evaluation["status"] = "GA_unavailable_no_inference"
        hits.append({"source_line": line_no, "sequence_id": sid,
                     "sequence_sha256": sequences[sid]["sequence_sha256"], "sequence_length": slen,
                     "model_name": model, "model_accession": accession,
                     "model_version": accession.rsplit(".", 1)[1], "model_length": mlen,
                     "domain_ordinal": ordinal, "domains_reported_for_pair": total,
                     "hmm_range": hmm_range, "alignment_range": alignment, "envelope_range": envelope,
                     "model_coverage": str(Decimal(ht - hf + 1) / mlen),
                     "sequence_alignment_coverage": str(Decimal(at - af + 1) / slen),
                     "full_sequence_evalue": fields[6], "full_sequence_bits": fields[7],
                     "full_sequence_bias_bits": fields[8], "conditional_evalue": fields[11],
                     "independent_evalue": fields[12], "domain_bits": fields[13],
                     "domain_bias_bits": fields[14], "posterior_accuracy": fields[21],
                     "description_of_target": fields[22] if len(fields) == 23 else "",
                     "threshold_evaluation": evaluation})
    # Preserve overlap and input order, including hits from different models.
    warnings = ["metadata consistency only; engine provenance not independently attested",
                "domtblout lacks sequence hashes; trusted worker binding remains required",
                "no overlap/clan resolution; passing a score cutoff does not establish biological truth"]
    if any(context[k] is None for k in ("project_id", "dataset_version_id", "taxonomy_id")):
        warnings.append("project/dataset/taxonomy identity contains unfrozen null fields")
    return {"schema_version": 1, "normalizer_version": VERSION,
            "kind": "synthetic_parser_fixture" if context["synthetic_fixture"] else "unverified_reported_domain_candidates",
            "platform_analysis_ready": False, "engine_executed_by_this_module": False,
            "context": deepcopy(context), "result_state": "reported_candidates" if hits else "no_reported_hits",
            "hits": hits, "warnings": warnings}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    audit = sub.add_parser("audit")
    audit.add_argument("fasta")
    audit.add_argument("output_dir")
    norm = sub.add_parser("normalize")
    norm.add_argument("domtblout")
    norm.add_argument("context_json")
    norm.add_argument("sequence_manifest_json")
    norm.add_argument("output_json")
    args = parser.parse_args()
    if args.command == "audit":
        stats, manifest = audit_fasta(args.fasta)
        out = Path(args.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        write_json(out / "fasta-audit.json", stats)
        write_json(out / "real-sequence-manifest.json", manifest)
        print(json.dumps({"source": stats["source"], "records": stats["records"], "length": stats["length"],
                          "duplicate_record_excess": stats["duplicate_record_excess"],
                          "invalid_characters": stats["invalid_under_extended_alphabet"],
                          "selected_sequences": len(manifest["sequences"])}))
    else:
        result = normalize_domtblout(Path(args.domtblout).read_bytes(),
                                    json.loads(Path(args.context_json).read_text(encoding="utf-8")),
                                    json.loads(Path(args.sequence_manifest_json).read_text(encoding="utf-8")))
        write_json(args.output_json, result)
        print(json.dumps({"kind": result["kind"], "result_state": result["result_state"], "hits": len(result["hits"])}))


if __name__ == "__main__":
    main()
