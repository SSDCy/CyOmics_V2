"""Explicit server-only native007 factory; no global runtime or import-time probes."""
from dataclasses import dataclass
import hashlib
import importlib
import json
import os
from pathlib import Path
import stat
import sys

SOURCE_PINS = (
    ('__init__.py',160,'9be1170a3e01df5833d5bec918dc0b18fa1942086231431529054a979c75111a'),
    ('output_contract.py',8474,'6966be18a920ad4f7c6bba0251e84966602a5294ea2c37689904846282e68a02'),
    ('process_policy.py',13634,'6703e0b23ad7b0d5fdb065dda523807531507760f9b9086f6bf4733e906cad71'),
    ('raw_qc_runner.py',26963,'86d6fc903f09a901b39a0c39a174e2e91411efccbe11eff8c0617154bbe43152'))
BUNDLE_SHA = 'c08809cbddedaacfab382e3ab85b0196645c5a2b44218708aadfd5308303ce3c'
SCRIPT_SHA = '2072a20e9dca53ab9ff03ef5a0a5d6aa9556dcee8004b5f28ce2fa4345624d64'
EXECUTABLE_SHA = '9c572a708d444e0ec91c59935bf9b5c3fd426a75fe2b7101b2a65f4783155283'
LIMITS_SHA = 'fe1e05ac949ed027969d4ae4475e0e66bbc44cdc1d9310486d33aa334f40aad0'
NATIVE_PATH = '/usr/lib/R/bin/exec/R'
NATIVE_DIRS = (('R_HOME','/usr/lib/R'),('R_SHARE_DIR','/usr/share/R/share'),
               ('R_INCLUDE_DIR','/usr/share/R/include'),('R_DOC_DIR','/usr/share/R/doc'),
               ('LD_LIBRARY_PATH','/usr/lib/R/lib'))
ENV = {'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8',
       'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1',
       'BLIS_NUM_THREADS':'1','VECLIB_MAXIMUM_THREADS':'1','NUMEXPR_NUM_THREADS':'1'}


class FactoryError(RuntimeError):
    """Unavailable or invalid trusted server configuration; never fallback."""


@dataclass(frozen=True, slots=True)
class NativeDirectoryPin:
    env_key: str
    path: Path
    device: int
    inode: int
    owner: int
    mode: int


@dataclass(frozen=True, slots=True)
class Config:
    """Trusted startup object, not JSON. No configurable executable/env/source."""
    input_root: Path
    staging_root: Path
    publish_root: Path
    expected_uid: int
    source_owner_uid: int
    native_directory_pins: tuple[NativeDirectoryPin, ...]


def _require(ok, reason):
    if not ok:
        raise FactoryError(reason)


def _canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def _package_path():
    # Fixed sibling only. No caller-supplied import name, source root or file.
    return Path(__file__).absolute().parent/'r_raw_qc'


def _safe_path(path):
    _require(isinstance(path,Path) and path.is_absolute()
             and '..' not in path.parts and str(path)==os.path.normpath(str(path)),
             'invalid_server_path')
    for item in (path,*path.parents):
        _require(not stat.S_ISLNK(item.lstat().st_mode),'symlink_path')


def _identity(info):
    return (info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_nlink,
            info.st_size,info.st_mtime_ns,info.st_ctime_ns)


def _file(path,size,digest,owner,modes):
    _safe_path(path)
    before=path.lstat()
    _require(stat.S_ISREG(before.st_mode) and before.st_nlink==1
             and before.st_uid==owner and stat.S_IMODE(before.st_mode) in modes
             and before.st_size==size,'unsafe_or_wrong_file_metadata')
    fd=os.open(path,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        _require(_identity(os.fstat(fd))==_identity(before),'file_changed')
        content=bytearray()
        while len(content)<=size:
            part=os.read(fd,min(65536,size+1-len(content)))
            if not part:
                break
            content.extend(part)
        _require(len(content)==size and hashlib.sha256(content).hexdigest()==digest,
                 'file_pin_mismatch')
        _require(_identity(os.fstat(fd))==_identity(before)
                 and _identity(path.lstat())==_identity(before),'file_changed')
    finally:
        os.close(fd)


def _validate_config(config):
    _require(type(config) is Config,'trusted_config_required')
    _require(all(type(n) is int and n>=0 for n in
                 (config.expected_uid,config.source_owner_uid)),'invalid_uid')
    _require(type(config.native_directory_pins) is tuple
             and len(config.native_directory_pins)==5
             and all(type(p) is NativeDirectoryPin and type(p.env_key) is str
                     for p in config.native_directory_pins),
             'approved_native_directory_pins_required')
    _require({p.env_key for p in config.native_directory_pins}==dict(NATIVE_DIRS).keys(),
             'invalid_native_directory_pins')
    for pin in config.native_directory_pins:
        _require(type(pin.env_key) is str and pin.path==Path(dict(NATIVE_DIRS)[pin.env_key])
                 and all(type(n) is int and n>=0 for n in
                         (pin.device,pin.inode,pin.owner,pin.mode))
                 and pin.owner==0 and pin.mode<=0o777 and not pin.mode&0o022,
                 'invalid_native_directory_pins')
    _require(all(isinstance(p,Path) and p.is_absolute() for p in
                 (config.input_root,config.staging_root,config.publish_root)),
             'server_roots_required')


def _platform_available():
    return os.name=='posix' and sys.platform.startswith('linux')


def verify_current(config):
    """Read fixed software/public metadata and own roots only; no R or census.

    This is disk verification, not memory attestation or a TOCTOU guarantee.
    """
    _validate_config(config)
    _require(_platform_available(),'linux_posix_required')
    _require(os.getuid()==config.expected_uid,'worker_uid_mismatch')
    package=_package_path()
    _safe_path(package)
    info=package.lstat()
    _require(stat.S_ISDIR(info.st_mode) and info.st_uid==config.source_owner_uid
             and stat.S_IMODE(info.st_mode) in (0o700,0o755),'unsafe_package_directory')
    # Disallow caches and all extra entries, not just extra .py files. Use -B.
    _require({p.name for p in package.iterdir()}==
             {p[0] for p in SOURCE_PINS}|{'approved_raw_qc.R'},'unexpected_package_entry')
    for name,size,digest in SOURCE_PINS:
        _file(package/name,size,digest,config.source_owner_uid,(0o444,0o644))
    _file(package/'approved_raw_qc.R',4949,SCRIPT_SHA,config.source_owner_uid,(0o444,0o644))
    manifest={'schemaVersion':1,'python_sources':{
        name:{'bytes':size,'sha256':digest} for name,size,digest in SOURCE_PINS}}
    raw=_canonical(manifest).encode('utf-8')
    _require(len(raw)==477 and hashlib.sha256(raw).hexdigest()==BUNDLE_SHA,'source_bundle_mismatch')
    roots=(config.input_root,config.staging_root,config.publish_root)
    for root in roots:
        _safe_path(root);info=root.lstat()
        _require(stat.S_ISDIR(info.st_mode) and info.st_uid==config.expected_uid
                 and stat.S_IMODE(info.st_mode)==0o700,'private_root_required')
    _require(all(a!=b and not a.is_relative_to(b) for i,a in enumerate(roots)
                 for j,b in enumerate(roots) if i!=j),'overlapping_roots')
    _require(roots[1].stat().st_dev==roots[2].stat().st_dev,'publication_cross_filesystem')
    for pin in config.native_directory_pins:
        _safe_path(pin.path);info=pin.path.lstat()
        _require(stat.S_ISDIR(info.st_mode) and
                 (info.st_dev,info.st_ino,info.st_uid,stat.S_IMODE(info.st_mode))==
                 (pin.device,pin.inode,pin.owner,pin.mode),'native_directory_pin_mismatch')
    _file(Path(NATIVE_PATH),14488,EXECUTABLE_SHA,0,(0o755,))
    return {'runner':{'bytes':len(raw),'sha256':BUNDLE_SHA},
            'script':{'bytes':4949,'sha256':SCRIPT_SHA},
            'output_validator':{'bytes':8474,'sha256':SOURCE_PINS[1][2]},
            'executable':{'bytes':14488,'sha256':EXECUTABLE_SHA},
            'r_version':'R version 4.6.1','entry_kind':'native_R',
            'limits_profile':'raw_qc_limits_v3','limits_sha256':LIMITS_SHA}


def _load_runner():
    return importlib.import_module('.r_raw_qc.raw_qc_runner',__package__)


def create_runtime(config=None):
    """Return (ordinary RawQCRunner, ExecutionRegistration), or None disabled.

    Requires a dedicated single-thread startup with bytecode writes disabled.
    The returned objects do not activate a service or authorize any input.
    """
    if config is None:
        return None
    execution=verify_current(config)  # Complete verification precedes native imports.
    _require(sys.dont_write_bytecode,'start_python_with_B_required')
    module=_load_runner()
    package=_package_path()
    for loaded,name in ((sys.modules.get(__package__+'.r_raw_qc'),'__init__.py'),
                        (module,'raw_qc_runner.py'),
                        (module.process_policy,'process_policy.py'),
                        (sys.modules.get(module.validate_outputs.__module__),'output_contract.py')):
        _require(loaded is not None and Path(loaded.__file__).absolute()==package/name,
                 'imported_module_location_mismatch')
    _require(module.process_policy.__name__==__package__+'.r_raw_qc.process_policy'
             and module.validate_outputs.__module__==__package__+'.r_raw_qc.output_contract',
             'relative_helper_mismatch')
    _require(module.ENV==ENV and dict(module.NATIVE_ENV)==dict(NATIVE_DIRS)
             and module.NATIVE_EXECUTABLE==Path(NATIVE_PATH)
             and module.NATIVE_EXECUTABLE_SHA256==EXECUTABLE_SHA
             and module.APPROVED_SCRIPT_SHA256==SCRIPT_SHA
             and module.LIMITS_PROFILE_ID=='raw_qc_limits_v3'
             and module.limits_metadata(module.Limits())['limits_sha256']==LIMITS_SHA,
             'imported_fixed_metadata_mismatch')
    contract=importlib.import_module('.raw_qc_contract',__package__)
    registration=contract.ExecutionRegistration(_canonical(execution))
    _require(registration.snapshot()==execution,'registration_mismatch')
    # Recheck disk after imports. Still no claim against a privileged source writer.
    _require(verify_current(config)==execution,'verification_changed')
    pins=tuple(module.DirectoryPin(p.env_key,p.path,p.device,p.inode,p.owner,p.mode)
               for p in config.native_directory_pins)
    runner_config=module.Config(
        rscript=Path(NATIVE_PATH),executable_pin=module.Pin(14488,EXECUTABLE_SHA),
        script=package/'approved_raw_qc.R',script_pin=module.Pin(4949,SCRIPT_SHA),
        input_root=config.input_root,staging_root=config.staging_root,publish_root=config.publish_root,
        limits=module.Limits(),executor_label='native007_source_bundle',
        expected_r_version='R version 4.6.1',entry_kind='native_R',native_directory_pins=pins)
    return module.RawQCRunner(runner_config),registration
