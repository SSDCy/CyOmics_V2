"""Pure input binding for the existing limma_core_v0.1; never Auth or execution.

ServerInput must be assembled AFTER the original repository authorized the
project, decoded the saved version/design and verified the complete11 artifacts.
Neither this Python type nor its digest checks prove that authorization occurred.
No browser Owner, path, connection, runner, model formula or service is accepted.
"""
from dataclasses import dataclass,field
import hashlib
import math
import re

MASKS=('original-blank-mask.tsv','original-zero-mask.tsv','working-NA-mask.tsv','imputed-mask.tsv')
NUMBER=re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z')


@dataclass(frozen=True,slots=True)
class ServerInput:
    project_id:str
    processed_dataset_version_id:str
    processed_version:dict
    artifacts:dict # Original artifact names -> immutable verified body bytes.
    sample_columns:tuple # Independent original import/report descriptors.
    sample_design:dict # Existing sample_persistence response envelope.
    contrast:dict # Existing contrast_design payload, exactly one explicit pair.
    replicate_model:str # Explicit choice: independent_biological; never inferred.


@dataclass(frozen=True,slots=True)
class SampleBinding:
    sample_key:str
    group_key:str
    replicate_key:str
    source_column:object # Original frozen sample_design.SourceColumn.
    design_row:object # Original frozen DesignRow, including all four fields.


@dataclass(frozen=True,slots=True)
class RowBinding:
    source_row_ordinal:int
    protein_ids_raw:str


@dataclass(frozen=True,slots=True)
class MaskBinding:
    name:str
    source_sha256:str
    original_bytes:bytes=field(repr=False)
    selected_bytes:bytes=field(repr=False)


@dataclass(frozen=True,slots=True)
class BoundInput:
    source_version_json:bytes=field(repr=False)
    source_analysis_ready:bool
    sample_source_binding:object # Existing contrast_design.SourceBinding.
    contrast:object # Existing contrast_design.Contrast, exact manual group names.
    family_columns:tuple
    excluded_column_ordinals:tuple
    samples:tuple[SampleBinding,...]
    rows:tuple[RowBinding,...]
    numeric_design:tuple
    design_rank:int
    contrast_weights:tuple
    matrix_tsv:bytes=field(repr=False)
    design_tsv:bytes=field(repr=False)
    preset_tsv:bytes=field(repr=False)
    upstream_row_map_tsv:bytes=field(repr=False)
    masks:tuple[MaskBinding,...]=field(repr=False)


@dataclass(frozen=True,slots=True)
class Result:
    model_input_satisfied:bool
    reasons:tuple[str,...]
    source_analysis_ready:bool|None
    bound:BoundInput|None=None


class _Rejected(ValueError):
    pass


def need(ok,code):
    if not ok:raise _Rejected(code)


def _tsv(rows):
    # Core quote="" requires exact lexical cells; never quote/normalize IDs.
    need(all(type(cell) is str and not any(c in cell for c in '\t\r\n\0')
        for row in rows for cell in row),'unsupported_tsv_cell')
    return ''.join('\t'.join(row)+'\n' for row in rows).encode('utf8')


def _table(body,width,maximum_rows):
    import csv,io
    need(type(body) is bytes and body.endswith(b'\n') and not body.startswith(b'\xef\xbb\xbf')
        and b'\r' not in body and b'\0' not in body,'invalid_artifact_table')
    try:text=body.decode('utf8')
    except UnicodeError:raise _Rejected('invalid_artifact_table') from None
    need(1<=text.count('\n')<=maximum_rows+1,'invalid_artifact_table')
    # Same dialect as the original log2 validator: R quote=TRUE/qmethod=double.
    # Decode quotes only; retain every cell as exact Unicode text.
    try:rows=[tuple(row) for row in csv.reader(io.StringIO(text,newline=''),delimiter='\t',strict=True)]
    except csv.Error:raise _Rejected('artifact_shape_mismatch') from None
    need(1<=len(rows)<=maximum_rows+1,'invalid_artifact_table')
    need(all(len(row)==width for row in rows),'artifact_shape_mismatch')
    return rows


def bind(value=None):
    if value is None:return Result(False,('waiting_server_input',),None)
    source_ready=None
    try:
        need(type(value) is ServerInput,'server_input_required')
        from cyomics_projects import preprocess_contract as pc,sample_design as sd
        from cyomics_projects import sample_persistence as sp,contrast_design as cd
        need(type(value.processed_version) is dict,'invalid_processed_version')
        need(value.processed_version.get('processed_dataset_version_id')==value.processed_dataset_version_id
            and value.processed_version.get('project_id')==value.project_id,'processed_version_mismatch')
        try:version=pc.version_value(value.processed_version,project=value.project_id,version=value.processed_dataset_version_id)
        except (ValueError,TypeError,KeyError):raise _Rejected('invalid_processed_version') from None
        need(version['parameters']['normalization']=='none' and version['parameters']['imputation']=='none',
            'invalid_processed_version')
        source_ready=version['analysis_ready'] # Retained false; never a readiness gate.
        version_json=pc.canonical(version)
        if value.replicate_model=='paired':raise _Rejected('paired_design_not_supported')
        need(value.replicate_model=='independent_biological','independent_biological_model_required')
        need(type(value.artifacts) is dict and set(value.artifacts)==set(pc.ARTIFACT_NAMES),'complete_artifact_set_required')
        artifacts=dict(value.artifacts)
        for pin in version['artifacts']:
            body=artifacts[pin['name']]
            need(type(body) is bytes and len(body)==pin['bytes'] and hashlib.sha256(body).hexdigest()==pin['sha256'],
                'artifact_pin_mismatch')
        need(artifacts['parameters.json']==pc.canonical(version['parameters']),'parameter_binding_mismatch')
        need(type(value.sample_columns) is tuple,'independent_sample_columns_required')
        try:descriptors=sd.bind_trusted_source(version['parent_dataset_version_id'],value.sample_columns)
        except sd.DesignError:raise _Rejected('invalid_import_sample_columns') from None
        family=version['quant_family']
        family_columns=tuple(c for c in descriptors.sample_columns if c.quant_family==family)
        selected=[{'ordinal':c.ordinal,'name_raw':c.name_raw,'quant_family':c.quant_family,
            'sample_label_raw':c.sample_label_raw} for c in family_columns]
        need(selected and pc.digest(selected)==version['source']['selected_columns_sha256'],
            'selected_columns_order_or_binding_mismatch')
        envelope=value.sample_design
        need(type(envelope) is dict,'invalid_saved_sample_design')
        need(envelope.get('dataset_version_id')==version['parent_dataset_version_id'],'sample_parent_version_mismatch')
        need(envelope.get('quant_family')==family,'quant_family_mismatch')
        try:
            sp.response_value(envelope,value.project_id,version['parent_dataset_version_id'],family)
            source=cd.bind_source({'project_id':value.project_id,'dataset_version_id':version['parent_dataset_version_id'],
                'quant_family':family,'sample_columns':list(value.sample_columns),'sample_design_record':envelope['record']},
                expected_project_id=value.project_id)
        except Exception:raise _Rejected('invalid_saved_sample_design') from None
        need(type(value.contrast) is dict,'invalid_contrast')
        need(value.contrast.get('quant_family')==family,'quant_family_mismatch')
        need(type(value.contrast.get('contrasts')) is list and len(value.contrast['contrasts'])==1,'single_explicit_contrast_required')
        try:review=cd.review_draft(value.contrast,source)
        except cd.ContrastError as error:
            # Original module supplies fixed codes, never names or input text.
            raise _Rejected(error.code) from None
        if review.issues:return Result(False,tuple(dict.fromkeys(i.code for i in review.issues)),source_ready)
        need(review.state=='ready_for_method_selection' and source.sample_review.globally_confirmed,
            'confirmed_sample_design_required')
        contrast=review.draft.contrasts[0]
        rows=source.sample_review.design.rows
        chosen=[(i,row) for i,row in enumerate(rows) if row.group in (contrast.numerator_group,contrast.denominator_group)]
        num=sum(row.group==contrast.numerator_group for i,row in chosen)
        den=sum(row.group==contrast.denominator_group for i,row in chosen)
        insufficient=tuple(code for count,code in ((num,'insufficient_numerator_biological_replicates'),
            (den,'insufficient_denominator_biological_replicates')) if count<2)
        if insufficient:return Result(False,insufficient,source_ready)
        need(len(chosen)<=64,'limma_core_sample_limit_exceeded')
        need(all(row.replicate_type=='biological' and row.confirmed for i,row in chosen),'independent_biological_model_required')
        # Disjoint no-intercept one-hot columns with >=2 members each have rank2.
        numeric=tuple((1,0) if row.group==contrast.numerator_group else (0,1) for i,row in chosen)
        need(num>0 and den>0 and all(sum(row)==1 for row in numeric),'design_not_full_rank')
        samples=tuple(SampleBinding('s'+str(j),'g1' if row.group==contrast.numerator_group else 'g2',
            'r'+str(j),family_columns[i],row) for j,(i,row) in enumerate(chosen,1))
        header=('record_number','protein_ids_raw',*(c.name_raw for c in family_columns))
        working=_table(artifacts['working-matrix.tsv'],len(header),version['source']['row_count'])
        need(working[0]==header,'working_column_order_mismatch')
        need(len(working)>1,'empty_working_matrix')
        lineage=[];previous=0
        for row in working[1:]:
            need(re.fullmatch('[1-9][0-9]*',row[0]) is not None,'invalid_source_row_ordinal')
            ordinal=int(row[0]);need(previous<ordinal<=version['source']['row_count'],'invalid_source_row_ordinal')
            previous=ordinal;lineage.append(RowBinding(ordinal,row[1]))
            for token in row[2:]:
                need(token=='NA' or (NUMBER.fullmatch(token) is not None and math.isfinite(float(token))),
                    'invalid_working_numeric_token')
        row_map=_table(artifacts['row-map.tsv'],4,version['source']['row_count'])
        need(row_map[0]==('source_record_number','protein_ids_raw','kept','work_row_ordinal')
            and len(row_map)==version['source']['row_count']+1,'row_map_binding_mismatch')
        kept=[]
        for ordinal,row in enumerate(row_map[1:],1):
            need(row[0]==str(ordinal) and row[2] in ('TRUE','FALSE'),'row_map_binding_mismatch')
            if row[2]=='TRUE':
                kept.append((row[0],row[1]));need(row[3]==str(len(kept)),'row_map_binding_mismatch')
            else:need(row[3]=='NA','row_map_binding_mismatch')
        need(kept==[row[:2] for row in working[1:]],'row_map_binding_mismatch')
        indices=tuple(i+2 for i,row in chosen)
        core_matrix=[('source_row_ordinal','protein_ids_raw',*(s.sample_key for s in samples))]
        core_matrix.extend((row[0],row[1],*(row[i] for i in indices)) for row in working[1:])
        masks=[]
        for name in MASKS:
            table=_table(artifacts[name],len(header),version['source']['row_count'])
            need(table[0]==header and len(table)==len(working),'mask_alignment_mismatch')
            for row,work in zip(table[1:],working[1:]):
                need(row[:2]==work[:2] and all(token in ('TRUE','FALSE') for token in row[2:]),'mask_alignment_mismatch')
                if name=='working-NA-mask.tsv':
                    need(all((token=='TRUE')==(cell=='NA') for token,cell in zip(row[2:],work[2:])),
                        'mask_alignment_mismatch')
            projected=[('source_row_ordinal','protein_ids_raw',*(s.sample_key for s in samples))]
            projected.extend((row[0],row[1],*(row[i] for i in indices)) for row in table[1:])
            masks.append(MaskBinding(name,hashlib.sha256(artifacts[name]).hexdigest(),artifacts[name],_tsv(projected)))
        design=[('sample_key','group_key','replicate_key','replicate_type','confirmed')]
        design.extend((s.sample_key,s.group_key,s.replicate_key,'biological','true') for s in samples)
        preset=[('key','value'),('schema_version','limma_core_v0.1'),('input_scale','log2'),
            ('upstream_transform','positive_log2'),('upstream_zero_blank','NA'),('pseudocount','0'),
            ('normalization','none'),('imputation','none'),('replicate_type','biological'),
            ('design_confirmed','true'),('contrast_id',contrast.contrast_id),('numerator','g1'),('denominator','g2')]
        bound=BoundInput(version_json,source_ready,source.binding,contrast,family_columns,
            tuple(row.column_ordinal for row in rows if row.group not in (contrast.numerator_group,contrast.denominator_group)),
            samples,tuple(lineage),numeric,2,(1,-1),_tsv(core_matrix),_tsv(design),_tsv(preset),
            artifacts['row-map.tsv'],tuple(masks))
        return Result(True,(),source_ready,bound)
    except _Rejected as error:return Result(False,(str(error),),source_ready)
    except (ValueError,TypeError,KeyError,AttributeError,OverflowError,UnicodeError):
        return Result(False,('invalid_server_input',),source_ready)
