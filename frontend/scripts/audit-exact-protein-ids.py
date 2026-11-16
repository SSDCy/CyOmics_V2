"""Read-only exact ID audit; explicit inputs, JSON statistics on stdout only."""

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path


EXPECTED = {
    "table": (6186652, "301bc95ff440a4ea70912fb5ede26aa54cb15f2cb06a9efeed6e55fd749f95fb"),
    "fasta": (17195782, "8cb4a7994a80b6fecef0310e288c4763261026989b73a4e467d3310aad5daba5"),
}
COLUMNS = ("Protein IDs", "Majority protein IDs")


def identity(path):
    metadata = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return {"size": metadata.st_size, "mtime_ns": metadata.st_mtime_ns,
            "sha256": digest.hexdigest()}


def prefix(token):
    if token.startswith("REV__"):
        return "REV__"
    if token.startswith("CON__"):
        return "CON__"
    return "other"


def analyze_column(rows, fasta_ids):
    counts = Counter()
    occurrences = Counter()
    all_tokens = set()
    for tokens in rows:
        counts["total_rows"] += 1
        occurrences.update(tokens)
        unique = set(tokens)
        all_tokens.update(unique)
        if not tokens:
            counts["empty_id_rows"] += 1
            counts["zero_match_rows"] += 1
        else:
            counts["single_id_rows" if len(tokens) == 1 else "multi_id_rows"] += 1
            hits = unique & fasta_ids
            if len(hits) == len(unique):
                counts["all_match_rows"] += 1
            elif hits:
                counts["partial_match_rows"] += 1
            else:
                counts["zero_match_rows"] += 1
    matched = all_tokens & fasta_ids
    unmatched = all_tokens - fasta_ids
    result = {key: counts[key] for key in (
        "total_rows", "empty_id_rows", "single_id_rows", "multi_id_rows",
        "all_match_rows", "partial_match_rows", "zero_match_rows")}
    result.update({"token_occurrences": sum(occurrences.values()),
                   "distinct_tokens": len(all_tokens),
                   "matched_distinct_tokens": len(matched),
                   "unmatched_distinct_tokens": len(unmatched)})
    result["prefix_statistics"] = {}
    for category in ("REV__", "CON__", "other"):
        category_tokens = {token for token in all_tokens if prefix(token) == category}
        result["prefix_statistics"][category] = {
            "token_occurrences": sum(occurrences[token] for token in category_tokens),
            "distinct_tokens": len(category_tokens),
            "matched_distinct_tokens": len(category_tokens & fasta_ids),
            "unmatched_distinct_tokens": len(category_tokens - fasta_ids),
        }
    result["within_row_repeated_occurrences"] = sum(len(tokens) - len(set(tokens)) for tokens in rows)
    appearances = Counter(token for tokens in rows for token in set(tokens))
    result["tokens_reused_across_rows"] = sum(count > 1 for count in appearances.values())
    result["extra_row_appearances"] = sum(count - 1 for count in appearances.values())
    assert result["empty_id_rows"] + result["single_id_rows"] + result["multi_id_rows"] == len(rows)
    assert result["all_match_rows"] + result["partial_match_rows"] + result["zero_match_rows"] == len(rows)
    assert len(matched) + len(unmatched) == len(all_tokens)
    return result, all_tokens


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table", required=True, type=Path)
    parser.add_argument("--fasta", required=True, type=Path)
    args = parser.parse_args()
    paths = {"table": args.table, "fasta": args.fasta}
    before = {role: identity(path) for role, path in paths.items()}
    for role, (size, digest) in EXPECTED.items():
        if before[role]["size"] != size or before[role]["sha256"] != digest:
            raise ValueError("INPUT_IDENTITY_MISMATCH: " + role)

    fasta_ids = set()
    fasta_records = 0
    duplicate_headers = 0
    with args.fasta.open("r", encoding="utf-8-sig", newline="") as handle:
        for line in handle:
            if not line.startswith(">"):
                continue
            tokens = line[1:].split()
            if not tokens:
                raise ValueError("EMPTY_FASTA_ID")
            token = tokens[0]
            fasta_records += 1
            duplicate_headers += token in fasta_ids
            fasta_ids.add(token)

    rows = {name: [] for name in COLUMNS}
    blank_segments = Counter()
    with args.table.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t", strict=True)
        headers = next(reader)
        if len(headers) != 182 or any(headers.count(name) != 1 for name in COLUMNS):
            raise ValueError("INVALID_TABLE_HEADERS")
        positions = {name: headers.index(name) for name in COLUMNS}
        for number, row in enumerate(reader, start=1):
            if len(row) != len(headers):
                raise ValueError("INVALID_COLUMN_COUNT at record " + str(number))
            for name, position in positions.items():
                parts = [token.strip() for token in row[position].split(";")]
                blank_segments[name] += sum(not token for token in parts)
                rows[name].append([token for token in parts if token])
    if len(rows[COLUMNS[0]]) != 2252 or fasta_records != 38387:
        raise ValueError("UNEXPECTED_RECORD_COUNT")

    statistics = {}
    sets = {}
    for name in COLUMNS:
        statistics[name], sets[name] = analyze_column(rows[name], fasta_ids)
        statistics[name]["empty_split_segments"] = blank_segments[name]
    left, right = COLUMNS
    differences = {
        "common_distinct_tokens": len(sets[left] & sets[right]),
        "protein_ids_only_distinct_tokens": len(sets[left] - sets[right]),
        "majority_ids_only_distinct_tokens": len(sets[right] - sets[left]),
        "identical_token_list_rows": sum(a == b for a, b in zip(rows[left], rows[right])),
        "identical_token_set_rows": sum(set(a) == set(b) for a, b in zip(rows[left], rows[right])),
        "majority_subset_of_protein_ids_rows": sum(set(b) <= set(a) for a, b in zip(rows[left], rows[right])),
    }
    after = {role: identity(path) for role, path in paths.items()}
    if before != after:
        raise ValueError("INPUT_CHANGED_DURING_AUDIT")
    print(json.dumps({
        "inputs": {role: {"path": str(path), "before": before[role], "after": after[role],
                          "unchanged": before[role] == after[role]}
                   for role, path in paths.items()},
        "fasta": {"records": fasta_records, "distinct_ids": len(fasta_ids),
                  "duplicate_header_ids": duplicate_headers},
        "columns": statistics,
        "column_differences": differences,
        "unmatched_examples": [
            {"id": token, "columns": [name for name in COLUMNS if token in sets[name]]}
            for token in sorted((sets[left] | sets[right]) - fasta_ids)[:5]
        ],
        "rules": {
            "table": "Split each specified column on semicolon; strip token ends only; ignore empty split segments as absent IDs, never drop rows.",
            "fasta": "First whitespace-delimited token after > at column 1; UTF-8 BOM handled by decoding.",
            "comparison": "Exact, case-sensitive; no prefix/suffix/version edits, fuzzy matching, homology, filtering or sample grouping.",
            "row_categories": "Empty-ID rows count as zero-match; nonempty rows partition into all/partial/zero using distinct row tokens; single/multi counts use nonempty token occurrences.",
            "prefix": "Only original token startswith REV__, CON__, otherwise other; no deletion or biological inference.",
        },
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
