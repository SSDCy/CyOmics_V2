"""One fixed saved-None-parent Total strategy; data contracts, never approval."""
import hashlib
import json

STRATEGY = 'total_intensity_saved_parent_v1'
CORE_CONTRACT = 'total_intensity_saved_raw_sum_v1'
PROPOSAL = 'total_saved_parent_source_proposal_v1'
CORE_PIN = {'bytes': 9324, 'sha256': '41966515c6b2acf76bd6eef8de8605ebf0a3589ad32fc1eabebeb19f5bbc9fa3'}
# Filled from the final isolated source bytes, never from a client/environment.
PINS = {'script': {'bytes': 4481, 'sha256': 'cde5cdea0672afe05749dc06f571017d421e4352bedccc6bfccb9b7ca882cadf'}, 'validator': {'bytes': 6210, 'sha256': '8f5cc50c906de148517d4fc697bc6f0af200774b16ea44275b1054ca5a7d19e7'}, 'runner': {'bytes': 14586, 'sha256': '16a879a5322b18c25b2c208d136c87f4e560812c33198e178a1419c4ab82fa81'}, 'executable': {'bytes': 14488, 'sha256': '9c572a708d444e0ec91c59935bf9b5c3fd426a75fe2b7101b2a65f4783155283'}}
SUMMARY_COLUMNS = ('strategy_contract', 'core_contract', 'normalization', 'imputation', 'analysis_ready', 'parent_processed_dataset_version_id', 'parent_manifest_sha256', 'parent_dataset_version_id', 'parent_file_id', 'parent_source_sha256', 'sample_ordinal', 'sample_name_raw', 'observed_count', 'missing_count', 'imputed_count', 'original_blank_count', 'original_zero_count', 'input_total_raw', 'reference_total_raw', 'multiplicative_factor_raw', 'additive_offset_log2', 'output_total_raw', 'warning_codes')


def parameters(flags,parent):
    from . import preprocess_contract as c
    c.exact(parent,('processed_dataset_version_id','manifest_sha256'))
    c._need(c._canonical_uuid(parent['processed_dataset_version_id']) and c.hashed(parent['manifest_sha256']))
    result=c._parameter_value(flags)
    result.update(normalization='total',preset='positive_log2_total_zero_na_no_impute_v1',
        normalization_parent=dict(parent))
    result['steps'][3]='normalization_total'
    return result


def is_total(value):
    return type(value) is dict and value.get('normalization')=='total'


def validate_parameters(value):
    from . import preprocess_contract as c
    c._need(type(value) is dict and set(value)==c.PARAMETER_KEYS|{'normalization_parent'})
    expected=parameters(value['filter_flags'],value['normalization_parent'])
    # Canonical bytes alone cannot distinguish integer0 from false.
    for key,wanted in expected.items():
        c._need(type(value[key]) is type(wanted) and value[key]==wanted)
    c._need(all(type(v) is str for v in value['steps']))
    return expected


def execution_value(value):
    from . import preprocess_repository as p
    if (type(value) is not dict or set(value)!=p.EXECUTION_KEYS or value['contract']!=PROPOSAL
        or value['entry_kind']!='native_R' or value['r_version']!='R version 4.6.1'
        or value['registered'] is not False or value['process_policy_sha256']!=p.POLICY_SHA256
        or value['limits']!=p.LIMITS):p.fail()
    for name in ('script','validator','runner','executable'):
        if value[name]!=PINS.get(name):p.fail()
    return value


def proposal_bytes():
    from . import preprocess_contract as c,preprocess_repository as p
    value={'contract':PROPOSAL,'entry_kind':'native_R','r_version':'R version 4.6.1',
        **{k:dict(v) for k,v in PINS.items()},'limits':dict(p.LIMITS),
        'process_policy_sha256':p.POLICY_SHA256,'registered':False}
    execution_value(value)
    return c.canonical(value)


def match_strategy(params,execution):
    from . import preprocess_repository as p
    if is_total(params)!=(execution['contract']==PROPOSAL):p.fail()


def parent_metadata(parent,source,params,family,owner):
    """Exact existing saved None authority; not a synthetic stage identity."""
    from . import preprocess_contract as c
    validate_parameters(params);c.version_value(parent)
    ref=params['normalization_parent']
    c._need(parent['processed_dataset_version_id']==ref['processed_dataset_version_id']
        and parent['manifest_sha256']==ref['manifest_sha256'] and parent['created_by']==owner
        and parent['source']==source and parent['quant_family']==family
        and parent['parameters']==c._parameter_value(params['filter_flags'])
        and parent['steps']==list(c.STEPS))
    return parent


def parent_artifacts(parent,outputs):
    from . import preprocess_contract as c
    c._need(type(outputs) is dict and set(outputs)==set(c.ARTIFACT_NAMES))
    for item in parent['artifacts']:
        body=outputs[item['name']]
        c._need(type(body) is bytes and len(body)==item['bytes']
            and c.bytes_sha(body)==item['sha256'])
    c._need(sum(map(len,outputs.values()))<=c.MAX_ARTIFACT_TOTAL)
    return outputs
