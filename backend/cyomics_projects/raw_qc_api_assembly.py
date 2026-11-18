"""Fixed private single-job API binding; no worker launch or I/O on import.

This trusted startup object is not approval, source lineage or Owner evidence.
Public create_app defaults and its original production gate remain unchanged.
"""
from dataclasses import dataclass
from pathlib import Path

PUBLISH_ROOT='/home/user01/Cy_Omics_v2.0/runtime/raw-qc-live-001/published'
EXECUTION_JSON='{"entry_kind":"native_R","executable":{"bytes":14488,"sha256":"9c572a708d444e0ec91c59935bf9b5c3fd426a75fe2b7101b2a65f4783155283"},"limits_profile":"raw_qc_limits_v3","limits_sha256":"fe1e05ac949ed027969d4ae4475e0e66bbc44cdc1d9310486d33aa334f40aad0","output_validator":{"bytes":8474,"sha256":"6966be18a920ad4f7c6bba0251e84966602a5294ea2c37689904846282e68a02"},"r_version":"R version 4.6.1","runner":{"bytes":477,"sha256":"c08809cbddedaacfab382e3ab85b0196645c5a2b44218708aadfd5308303ce3c"},"script":{"bytes":4949,"sha256":"2072a20e9dca53ab9ff03ef5a0a5d6aa9556dcee8004b5f28ce2fa4345624d64"}}'


class APIAssemblyRefused(RuntimeError):
    def __init__(self):super().__init__('raw_qc_api_assembly_refused')


@dataclass(frozen=True,slots=True)
class RawQCAPIConfig:
    execution_json:str
    publish_root:str
    expected_uid:int


def prepare_raw_qc_api_config(config=None):
    """Pure pre-I/O checks; None loads no repository/reader/runtime components."""
    if config is None:return None
    if (type(config) is not RawQCAPIConfig or type(config.expected_uid) is not int or config.expected_uid!=1000
        or type(config.execution_json) is not str or config.execution_json!=EXECUTION_JSON
        or type(config.publish_root) is not str or config.publish_root!=PUBLISH_ROOT):
        raise APIAssemblyRefused()
    return config


def _components():
    # Exact adjacent original classes. No configurable module/transport/owner.
    from .repository import FixedPool
    from .raw_qc_repository import RawQCRepository
    from .raw_qc_reader import RawQCArtifactReader
    from .raw_qc_contract import ExecutionRegistration
    return FixedPool,RawQCRepository,RawQCArtifactReader,ExecutionRegistration


def bind_raw_qc_api(config=None,pool=None,*,enqueue_observation_enabled=False):
    """Only after create_app's original gate and final internal pool selection.

    Neither constructor opens a socket/file or creates a process. The single
    Reader instance must remain attached for the complete application lifetime.
    Runtime software/root checks and actual activation need separate approval.
    """
    if type(enqueue_observation_enabled) is not bool:raise APIAssemblyRefused()
    config=prepare_raw_qc_api_config(config)
    if config is None:return None,None
    FixedPool,RawQCRepository,RawQCArtifactReader,ExecutionRegistration=_components()
    if type(pool) is not FixedPool:raise APIAssemblyRefused()
    registration=ExecutionRegistration(config.execution_json)
    try:
        snapshot=registration.snapshot()
        # Compare all eight keys to the exact approved canonical JSON, not only
        # the contract's permissive runner/executable/output-validator shapes.
        import json
        if json.dumps(snapshot,sort_keys=True,separators=(',',':'),allow_nan=False)!=EXECUTION_JSON:
            raise APIAssemblyRefused()
    except BaseException:raise APIAssemblyRefused() from None
    repository=(RawQCRepository(pool,registration,enqueue_observation_enabled=True)
        if enqueue_observation_enabled else RawQCRepository(pool,registration))
    return repository,RawQCArtifactReader(Path(PUBLISH_ROOT))


def _launcher_components():
    from .raw_qc_single_launcher import Config,SingleRawQCLauncher,SingleLaunchRepository,configured
    return Config,SingleRawQCLauncher,SingleLaunchRepository,configured


def prepare_raw_qc_launcher_config(config=None):
    """Server-only source config, not a physical review/activation attestation."""
    if config is None:return None
    Config,Launcher,Proxy,configured=_launcher_components()
    if type(config) is not Config:raise APIAssemblyRefused()
    try:return config if configured(config) else None
    except BaseException:raise APIAssemblyRefused() from None


def bind_raw_qc_submit(repository,config=None):
    """Return original repo or one proxy/controller pair; no request-time reset."""
    config=prepare_raw_qc_launcher_config(config)
    if config is None:return repository,None
    Config,Launcher,Proxy,configured=_launcher_components()
    controller=Launcher(config)
    return Proxy(repository,controller),controller


def _launcher_closed(controller):
    """Known exact controller invariant plus no pending task/handle proof.

    close() returning None alone is not proof; any getter exception propagates.
    This observes only this controller, not a global process/FD census.
    """
    Config,Launcher,Proxy,configured=_launcher_components()
    if type(controller) is not Launcher:raise APIAssemblyRefused()
    if controller.snapshot()['state'] not in ('off','idle','replayed','observed'):raise APIAssemblyRefused()
    if controller._monitor is not None and not controller._monitor.done():raise APIAssemblyRefused()
    if any(not task.done() for task in controller._tasks) or controller._pidfd is not None:raise APIAssemblyRefused()
    if controller._proc is not None:
        if (type(controller._proc.returncode) is not int or controller._transport_close_attempted is not True
            or controller._proc._transport._finished is not True
            or controller._proc._transport.is_closing() is not True):raise APIAssemblyRefused()


async def close_raw_qc_live_resources(controller,imports,authority,pool):
    """Only the new configured branch. Each teardown is attempted exactly once."""
    try:imports.close()
    finally:
        try:
            await controller.close()
            _launcher_closed(controller)
        finally:
            try:await authority.close()
            finally:pool.close()
