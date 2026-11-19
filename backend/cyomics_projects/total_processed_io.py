"""Bound saved-parent export into original worker inputs; no credentials or DB IO."""
from dataclasses import dataclass
from pathlib import Path
import json
from . import preprocess_contract as c
from . import total_processed_profile as profile


def r_literal(value):
    if value is None:return 'NULL'
    if type(value) is bool:return 'TRUE' if value else 'FALSE'
    if type(value) in (int,float):return json.dumps(value,allow_nan=False)
    if type(value) is str:return json.dumps(value,ensure_ascii=True)
    if type(value) is list:return 'list('+','.join(map(r_literal,value))+')'
    if type(value) is dict:
        return 'structure(list('+','.join(r_literal(v) for v in value.values())+'),names=c('+','.join(r_literal(k) for k in value)+'))'
    raise ValueError('invalid_parent_literal')


def provenance_bytes(parent):
    c.version_value(parent)
    pins=parent['artifacts']
    frame='data.frame(name=c('+','.join(r_literal(x['name']) for x in pins)+'),bytes=c('+','.join(str(x['bytes']) for x in pins)+'),sha256=c('+','.join(r_literal(x['sha256']) for x in pins)+'),stringsAsFactors=FALSE,check.names=FALSE)'
    return ('local({ p <- '+r_literal(parent)+'; p$artifacts <- '+frame+'; p })\n').encode()


@dataclass(frozen=True,slots=True)
class TotalPrepared:
    original:object
    extras:tuple
    parent_json:bytes
    parameters_json:bytes
    def __getattr__(self,name):return getattr(self.original,name)


def prepare_total_input(bound,roots,parent,*,check):
    from .preprocess_execution import prepare_input,_snapshot
    from . import log2_matrix_validator as old
    task=bound.capsule.task();metadata,outputs=parent
    profile.parent_metadata(metadata,task['source'],task['parameters'],task['quant_family'],bound.capsule.record()['admission']['owner_id'])
    profile.parent_artifacts(metadata,outputs)
    original=prepare_input(bound,roots)
    selected=original.selected.read_bytes()
    checked=old.validate(bound.content,selected,task['quant_family'],tuple(task['parameters']['filter_flags'][k] for k in ('reverse','contaminant','site_only')),outputs,check=check)
    c._need(checked.manifest_sha256==metadata['manifest_sha256'])
    attempt=task['attempt_id'];extras=[]
    from .log2_matrix_runner import Pin
    for item in metadata['artifacts']:
        name='parent-'+item['name'];data=outputs[item['name']]
        path=_snapshot(roots.inputs,attempt+'.'+name,data)
        extras.append((name,path,Pin(len(data),c.bytes_sha(data)),True))
    for name,data in (('parent-provenance.R',provenance_bytes(metadata)),('request-parameters.json',c.canonical(task['parameters']))):
        path=_snapshot(roots.inputs,attempt+'.'+name,data)
        extras.append((name,path,Pin(len(data),c.bytes_sha(data)),True))
    core=Path(__file__).absolute().parent/'r_normalization_total/total_core.R'
    extras.append(('total_core.R',core,Pin(**profile.CORE_PIN),False))
    return TotalPrepared(original,tuple(extras),c.canonical(metadata),c.canonical(task['parameters']))


def total_runner_config(capsule,prepared,roots,native_directory_pins):
    from .total_processed_runner import Config
    from .log2_matrix_runner import Pin
    c._need(type(prepared) is TotalPrepared)
    task=capsule.task()
    return Config(enabled=True,job_id=task['job_id'],attempt_id=task['attempt_id'],
        source=prepared.source,source_pin=Pin(prepared.source_bytes,prepared.source_sha256),
        selected=prepared.selected,selected_pin=Pin(prepared.selected_bytes,prepared.selected_sha256),
        parameters_pin=Pin(prepared.parameters_bytes,prepared.parameters_sha256),
        script=Path(__file__).absolute().parent/'r_total_processed/total_processed.R',staging_root=roots.staging,
        family=task['quant_family'],flags=tuple(task['parameters']['filter_flags'][k] for k in ('reverse','contaminant','site_only')),
        native_directory_pins=native_directory_pins,extras=prepared.extras,
        parent_metadata_json=prepared.parent_json,parameters_json=prepared.parameters_json)
