"""Pure server startup configuration; reuses the final existing API app pool.

This object is source configuration, never registration or startup approval.
The worker is a separate newly approved process; no worker starts in ASGI.
"""
from dataclasses import dataclass
from . import preprocess_repository as p


@dataclass(frozen=True,slots=True)
class PreprocessAPIConfig:
    proposal_json:bytes
    median_proposal_json:bytes|None=None
    total_proposal_json:bytes|None=None


def prepare_preprocess_api_config(config=None):
    if config is None:return None
    if type(config) is not PreprocessAPIConfig:p.fail(503,'preprocessing_unavailable')
    if type(config.proposal_json) is not bytes:p.fail(503,'preprocessing_unavailable')
    try:
        if p.execution_value(config.proposal_json.decode('utf8'))['contract']!='log2_local_source_proposal_v0.1':p.fail()
    except BaseException:p.fail(503,'preprocessing_unavailable')
    if config.median_proposal_json is not None:
        from .median_processed_profile import PROPOSAL
        if type(config.median_proposal_json) is not bytes:p.fail(503,'preprocessing_unavailable')
        try:
            if p.execution_value(config.median_proposal_json.decode()).get('contract')!=PROPOSAL:p.fail()
        except BaseException:p.fail(503,'preprocessing_unavailable')
    if config.total_proposal_json is not None:
        from .total_processed_profile import PROPOSAL
        if type(config.total_proposal_json) is not bytes:p.fail(503,'preprocessing_unavailable')
        try:
            if p.execution_value(config.total_proposal_json.decode()).get('contract')!=PROPOSAL:p.fail()
        except BaseException:p.fail(503,'preprocessing_unavailable')
    return config


def bind_preprocess_api(config=None,pool=None):
    config=prepare_preprocess_api_config(config)
    if config is None:return None
    from .repository import FixedPool
    if type(pool) is not FixedPool:p.fail(503,'preprocessing_unavailable')
    return p.PreprocessRepository(pool,config.proposal_json,enabled=True,median_execution_json=config.median_proposal_json,total_execution_json=config.total_proposal_json)
