"""Structural raw_qc.R protocol validation only; no scientific estimator in Python."""
import csv
from decimal import Decimal, InvalidOperation
import hashlib
import math
from pathlib import Path
import re

OUTPUTS = frozenset(('raw-matrix.tsv', 'numeric-matrix.tsv', 'blank-mask.tsv', 'zero-mask.tsv',
                     'sample-qc.tsv', 'summary.tsv', 'R-session-info.txt'))
QC_HEADER = ('column_ordinal','name_raw','sample_label_raw','records','positive','zero','blank',
             'exact_zero_literal','numeric_sum','positive_median')
SUMMARY_HEADER = ('input_rows','input_columns','quant_family','selected_columns','selected_cells',
                  'positive','zero','blank','exact_zero_literal','original_zero_transformed',
                  'imputed_values','filtered_rows','removed_samples','numeric_representation')
NUMBER = re.compile(r'[+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?')


class ProtocolError(ValueError):
    pass


def require(value, code):
    if not value: raise ProtocolError(code)


def _reader(path, *, raw=False):
    f = open(path, 'r', encoding='utf-8-sig' if raw else 'utf-8', newline='')
    return f, csv.reader(f, delimiter='\t', quoting=csv.QUOTE_NONE if raw else csv.QUOTE_MINIMAL,
                         quotechar='"', doublequote=True, strict=True)


def _count(text, cap):
    require(type(text) is str and NUMBER.fullmatch(text) is not None and len(text) <= 64, 'invalid_count')
    try: value = Decimal(text)
    except InvalidOperation: raise ProtocolError('invalid_count') from None
    require(value.is_finite() and 0 <= value <= cap and value == value.to_integral_value(), 'invalid_count')
    return int(value)


def _numeric(text):
    require(NUMBER.fullmatch(text) is not None and len(text) <= 64, 'invalid_numeric_schema')
    value = float(text)
    require(math.isfinite(value) and value >= 0, 'invalid_numeric_schema')
    return value


def input_header(path, metadata, family, check):
    f, rows = _reader(path, raw=True)
    try:
        header = next(rows)
        require(tuple(header) == metadata.columns and len(set(header)) == len(header)
                and header.count('Protein IDs') == 1, 'input_header_mismatch')
        indices = tuple(i for i, name in enumerate(header) if name.startswith(family + ' '))
        require(bool(indices) and all(header[i][len(family)+1:] != '' for i in indices), 'missing_sample_columns')
        count = 0
        for row in rows:
            check(); count += 1
            require(len(row) == len(header), 'input_row_width')
            require(count <= metadata.row_count, 'input_rows_mismatch')
        require(count == metadata.row_count, 'input_rows_mismatch')
        return tuple(header), indices
    except StopIteration: raise ProtocolError('empty_input') from None
    finally: f.close()


def validate_outputs(output, input_path, metadata, family, expected_r_version, check):
    """Bind seven output schemas to the same staged original input and QC totals.

    Median/sum scientific correctness is provided by pinned R, not recomputed here.
    Numeric/mask partition counts below only cross-check the published protocol.
    """
    header, indices = input_header(input_path, metadata, family, check)
    names = tuple(header[i] for i in indices)
    matrix_header = ('record_number', 'protein_ids_raw') + names
    streams = []
    readers = []
    for name in ('raw-matrix.tsv','numeric-matrix.tsv','blank-mask.tsv','zero-mask.tsv'):
        f, reader = _reader(Path(output)/name); streams.append(f); readers.append(reader)
    original, source_rows = _reader(input_path, raw=True)
    totals = [{'positive':0,'zero':0,'blank':0,'exact_zero_literal':0} for _ in indices]
    try:
        next(source_rows)
        for reader in readers: require(tuple(next(reader)) == matrix_header, 'matrix_header_mismatch')
        protein_index = header.index('Protein IDs')
        for number, source_row in enumerate(source_rows, 1):
            check()
            rows = [next(reader) for reader in readers]
            for row in rows:
                require(len(row) == len(matrix_header) and _count(row[0],metadata.row_count) == number
                        and row[1] == source_row[protein_index], 'matrix_source_mismatch')
            expected = [source_row[i] for i in indices]
            require(rows[0][2:] == expected, 'raw_value_changed')
            for j, raw in enumerate(expected):
                blank, zero = rows[2][j+2], rows[3][j+2]
                require(blank in ('TRUE','FALSE') and zero in ('TRUE','FALSE'), 'invalid_mask_schema')
                require((blank == 'TRUE') == (raw == ''), 'blank_mask_mismatch')
                numeric = rows[1][j+2]
                if raw == '': require(numeric == 'NA' and zero == 'FALSE', 'numeric_blank_mismatch')
                else:
                    value = _numeric(numeric)
                    require((zero == 'TRUE') == (value == 0), 'zero_mask_mismatch')
                totals[j]['blank'] += blank == 'TRUE'
                totals[j]['zero'] += zero == 'TRUE'
                totals[j]['positive'] += blank == 'FALSE' and zero == 'FALSE'
                totals[j]['exact_zero_literal'] += raw == '0'
        for reader in readers: require(next(reader, None) is None, 'extra_matrix_row')
    except StopIteration: raise ProtocolError('missing_matrix_row') from None
    finally:
        original.close()
        for f in streams: f.close()
    f, reader = _reader(Path(output)/'sample-qc.tsv')
    try:
        require(tuple(next(reader)) == QC_HEADER, 'qc_header_mismatch')
        for j, ordinal in enumerate(indices):
            check(); row = next(reader)
            require(len(row) == len(QC_HEADER), 'qc_schema_mismatch')
            q = dict(zip(QC_HEADER,row))
            require(_count(q['column_ordinal'], len(header)-1) == ordinal
                    and q['name_raw'] == header[ordinal]
                    and q['sample_label_raw'] == header[ordinal][len(family)+1:]
                    and _count(q['records'],metadata.row_count) == metadata.row_count, 'qc_source_mismatch')
            for name in totals[j]: require(_count(q[name],metadata.row_count) == totals[j][name], 'qc_count_mismatch')
            total = _numeric(q['numeric_sum'])
            if totals[j]['positive'] == 0:
                require(q['positive_median'] == 'NA' and total == 0, 'qc_numeric_schema')
            else: require(_numeric(q['positive_median']) > 0 and total > 0, 'qc_numeric_schema')
        require(next(reader, None) is None, 'extra_qc_row')
    except StopIteration: raise ProtocolError('missing_qc_row') from None
    finally: f.close()
    f, reader = _reader(Path(output)/'summary.tsv')
    try:
        require(tuple(next(reader)) == SUMMARY_HEADER, 'summary_header_mismatch')
        row = next(reader); require(len(row) == len(SUMMARY_HEADER), 'summary_schema_mismatch')
        summary = dict(zip(SUMMARY_HEADER,row)); cells = metadata.row_count * len(indices)
        require(_count(summary['input_rows'],metadata.row_count) == metadata.row_count
                and _count(summary['input_columns'],len(header)) == len(header)
                and summary['quant_family'] == family
                and _count(summary['selected_columns'],len(indices)) == len(indices)
                and _count(summary['selected_cells'],cells) == cells, 'summary_source_mismatch')
        for name in ('positive','zero','blank','exact_zero_literal'):
            require(_count(summary[name],cells) == sum(t[name] for t in totals), 'summary_count_mismatch')
        for name in ('original_zero_transformed','imputed_values','filtered_rows','removed_samples'):
            require(_count(summary[name],0) == 0, 'unexpected_processing')
        require(summary['numeric_representation'] == 'R_double_17_significant_digits', 'numeric_representation_mismatch')
        require(next(reader, None) is None, 'extra_summary_row')
    except StopIteration: raise ProtocolError('missing_summary') from None
    finally: f.close()
    check()
    session = (Path(output)/'R-session-info.txt').read_text(encoding='utf-8')
    require(session.startswith(expected_r_version + ' ') or session.startswith(expected_r_version + '\n'), 'R_version_mismatch')
    return {'input_rows':metadata.row_count, 'input_columns':len(header),
            'selected_columns':len(indices), 'selected_cells':cells, 'quant_family':family,
            'scientific_estimates_recomputed_in_Python':False}
