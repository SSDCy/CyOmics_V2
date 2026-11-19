"""One fixed saved-None-parent Median strategy; data contracts, never approval."""
import hashlib
import json

STRATEGY = 'median_observed_log2_saved_parent_v1'
CORE_CONTRACT = 'median_observed_log2_v1'
PROPOSAL = 'median_saved_parent_source_proposal_v1'
CORE_PIN = {'bytes': 6899, 'sha256': 'feca45a9d255ae60fa50a6c63849ee68a3df2ae363e6f1b9f1b7e03224b4d885'}
# Filled from the final isolated source bytes, never from a client/environment.
PINS = {'script': {'bytes': 3981, 'sha256': 'bf6a486859dd350c9b3d1937c123eae0d4d233118af13c3e196f30d340433f92'}, 'validator': {'bytes': 5639, 'sha256': '8a414077569be9ea1da281f688826b993dc2b1165a1f45207d7f69088897fbff'}, 'runner': {'bytes': 14378, 'sha256': '498698b9bce3f30685e45833e76a8902f0e31c90c9220886797406f3116aadbb'}, 'executable': {'bytes': 14488, 'sha256': '9c572a708d444e0ec91c59935bf9b5c3fd426a75fe2b7101b2a65f4783155283'}}
SUMMARY_COLUMNS = ('strategy_contract','core_contract','normalization','imputation','analysis_ready',
    'parent_processed_dataset_version_id','parent_manifest_sha256','parent_dataset_version_id',
    'parent_file_id','parent_source_sha256','sample_ordinal','sample_name_raw','observed_count',
    'missing_count','imputed_count','original_blank_count','original_zero_count','input_median_log2',
    'reference_log2','additive_offset_log2','output_median_log2')


def parameters(flags,parent):
    from . import preprocess_contract as c
    c.exact(parent,('processed_dataset_version_id','manifest_sha256'))
    c._need(c._canonical_uuid(parent['processed_dataset_version_id']) and c.hashed(parent['manifest_sha256']))
    result=c._parameter_value(flags)
    result.update(normalization='median',preset='positive_log2_median_zero_na_no_impute_v1',
        normalization_parent=dict(parent))
    result['steps'][3]='normalization_median'
    return result


def is_median(value):
    return type(value) is dict and value.get('normalization')=='median'


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
    if is_median(params)!=(execution['contract']==PROPOSAL):p.fail()


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
