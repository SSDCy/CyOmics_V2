"""Root-installed control shim only; import does not activate or access runtime.

Deploy as a pinned control file alongside the existing physical/source verifier.
Root applies015, approves a bounded scope and precreates the fixed0700 bases.
This file never registers a scope, reads credentials, serves or runs a second worker.
"""
import hashlib,importlib.util,json,os,stat,sys,time
from pathlib import Path

HERE=Path(__file__).parent
BASE=Path('/home/user01/Cy_Omics_v2.0')
WORKERS=BASE/'runtime/differential-service-001'
NATIVE_BASE=BASE/'runtime/limma-bound-input-001'

def need(ok):
    if not ok:raise RuntimeError('differential_service_startup_refused')
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def sha(raw):return hashlib.sha256(raw).hexdigest()
def read(path,cap=2097152,private=False):
    need(not any(p.is_symlink() for p in (path,*path.parents)))
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        st=os.fstat(fd);need(stat.S_ISREG(st.st_mode) and st.st_uid==1000 and st.st_nlink==1 and not st.st_mode&0o022 and 0<st.st_size<=cap)
        if private:need(stat.S_IMODE(st.st_mode)==0o600)
        raw=os.read(fd,cap+1);after=os.fstat(fd)
        need(len(raw)==st.st_size and (st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns)==(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns))
        return raw
    finally:os.close(fd)
def write_once(path,raw):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
    fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:os.fsync(fd)
    finally:os.close(fd)
def load_base():
    raw=read(HERE/'control-pins.json');control=json.loads(raw)
    name='classification_service_startup.py';expected=control['control_files'][name];body=read(HERE/name)
    need(len(body)==expected['bytes'] and sha(body)==expected['sha256'])
    own=control['control_files']['differential_service_startup.py'];body=read(HERE/'differential_service_startup.py')
    need(len(body)==own['bytes'] and sha(body)==own['sha256'])
    backend=Path(control['manifests']['source']['root'])/'backend';need(Path.cwd()==backend)
    sys.path.insert(0,str(backend))
    spec=importlib.util.spec_from_file_location('differential_physical_verifier',HERE/name)
    helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
    # Reuse physical/source/native checks only, not classification callbacks or worker.
    ops,checked,pins,native=helper.load_control();need(checked==control)
    return helper,ops,control,pins,native,backend
def mkdir_fresh(parent,name):
    from cyomics_projects.raw_qc_artifacts import open_root
    fd=open_root(parent)
    try:os.mkdir(name,mode=0o700,dir_fd=fd);os.fsync(fd)
    finally:os.close(fd)
    path=parent/name;fd=open_root(path)
    try:need(stat.S_IMODE(os.fstat(fd).st_mode)==0o700)
    finally:os.close(fd)
    return path

def make_api_config(control,*,service_scope_id,service_scope_sha256,expires_unix):
    """Root bootstrap supplies actual015 scope; no Owner constant or approval here."""
    helper,ops,checked,pins,native,backend=load_base();need(checked==control)
    from cyomics_projects.validation import canonical_uuid
    from cyomics_projects.classification_on_demand import Config as Launcher,hashed
    from cyomics_projects.differential_on_demand import Config,ServiceScope
    need(canonical_uuid(service_scope_id) and hashed(service_scope_sha256))
    now=time.time();need(type(expires_unix) in (int,float) and now+432<expires_unix<=now+43200)
    api=ops.identity(os.getpid());ops.validate('api',api,control)
    # Per-scope roots are fresh; no old journal reset or recovery inference.
    scope_path=mkdir_fresh(WORKERS,service_scope_id)
    context={'schemaVersion':1,'contract':'root_differential_service_context_v1','api':api,
        'service_scope_id':service_scope_id,'scope_sha256':service_scope_sha256,'approved_unix':now,'expires_unix':expires_unix,
        'source_manifest_sha256':control['manifests']['source']['manifest_file']['sha256'],
        'control_sha256':sha(read(HERE/'control-pins.json')),'maximum_parallel_children':1,'child_total_seconds':420,
        'native_R_wall_seconds':60,'automatic_retry':False,'roots':str(scope_path)}
    path=HERE/'differential-service-context.json';write_once(path,canonical(context));pins[path]=helper.identity(path)
    def verify():
        helper.verify_snapshot(pins);need(ops.identity(os.getpid())==api and context['approved_unix']<=time.time()<expires_unix)
    return Config(launcher=Launcher(enabled=True,startup=HERE/'differential_service_startup.py',backend=backend,
        control=scope_path,startup_sha256=control['control_files']['differential_service_startup.py']['sha256'],
        python_sha256=helper.PYTHON_SHA,verify=verify),service_scope=ServiceScope(service_scope_id,service_scope_sha256,control['manifests']['source']['manifest_file']['sha256']))

def child_callbacks(notification):
    helper,ops,control,pins,native,backend=load_base()
    from cyomics_projects.differential_on_demand import Ticket,ServiceScope
    from cyomics_projects.differential_worker_once import OnceConfig
    from cyomics_projects.raw_qc_checkpoint import WorkerRoots
    from cyomics_projects.validation import canonical_uuid
    path=HERE/'differential-service-context.json';raw=read(path,private=True);context=ops.strict_json(raw);pins[path]=helper.identity(path)
    fields={'schemaVersion','contract','api','service_scope_id','scope_sha256','approved_unix','expires_unix','source_manifest_sha256','control_sha256','maximum_parallel_children','child_total_seconds','native_R_wall_seconds','automatic_retry','roots'}
    need(set(context)==fields and type(context['schemaVersion']) is int and context['schemaVersion']==1 and context['contract']=='root_differential_service_context_v1')
    need(canonical_uuid(context['service_scope_id']) and context['roots']==str(WORKERS/context['service_scope_id'])
        and context['control_sha256']==sha(read(HERE/'control-pins.json'))
        and context['source_manifest_sha256']==control['manifests']['source']['manifest_file']['sha256']
        and context['maximum_parallel_children']==1 and context['child_total_seconds']==420 and context['native_R_wall_seconds']==60 and context['automatic_retry'] is False)
    parent=context['api'];ops.validate('api',parent,control)
    need(os.getppid()==parent['pid'] and ops.identity(parent['pid'])==parent)
    own=os.getpid();start=int(Path('/proc/self/stat').read_bytes().rsplit(b')',1)[1].split()[19])
    scope_path=WORKERS/context['service_scope_id'];intent_path=scope_path/(notification['job_id']+'.launch.once')
    raw=read(intent_path,4096,True);intent=ops.strict_json(raw);pins[intent_path]=helper.identity(intent_path)
    need(raw==canonical(intent)+b'\n' and set(intent)==set(notification)|{'registration_id','epoch_id','scope_sha256','service_scope_id'}
        and all(intent[k]==v for k,v in notification.items()) and intent['service_scope_id']==context['service_scope_id']
        and intent['scope_sha256']==context['scope_sha256'] and canonical_uuid(intent['registration_id']) and canonical_uuid(intent['epoch_id']))
    def verify():
        helper.verify_snapshot(pins)
        need(os.getpid()==own and int(Path('/proc/self/stat').read_bytes().rsplit(b')',1)[1].split()[19])==start
            and os.getppid()==parent['pid'] and ops.identity(parent['pid'])==parent
            and context['approved_unix']<=time.time()<context['expires_unix'])
    def activate(factory):
        verify()
        from cyomics_projects import settings
        need(settings.AUTH_POLICY_VERIFIED is False and settings.RUNTIME_START_APPROVED is False)
        settings.AUTH_POLICY_VERIFIED=True;settings.RUNTIME_START_APPROVED=True;settings.production_gate();return factory()
    def configure(value):
        need(value==notification);verify()
        root=mkdir_fresh(scope_path,notification['job_id'])
        paths=[mkdir_fresh(root,name) for name in ('control','inputs','staging','published','receipts')]
        epoch=mkdir_fresh(NATIVE_BASE,intent['epoch_id']);mkdir_fresh(epoch,'staging')
        cfg=OnceConfig(owner_id=notification['owner_id'],project_id=notification['project_id'],registration_id=intent['registration_id'],
            scope_sha256=intent['scope_sha256'],epoch_id=intent['epoch_id'],job_id=notification['job_id'],native_directory_pins=native,verify=verify)
        roots=WorkerRoots(*paths);roots.verify();return cfg,roots
    verify();return activate,configure,verify

def main():
    if sys.argv[1:]!=['--once']:return 2
    try:
        helper,ops,control,pins,native,backend=load_base()
        from cyomics_projects.differential_on_demand import parse_notification,main_from_server
        raw=sys.stdin.buffer.read(1025);notification=parse_notification(raw)
        activate,configure,verify=child_callbacks(notification)
        return main_from_server(raw,configure=configure,activate_factory=activate,verify=verify)
    except BaseException:
        sys.stdout.write('{"status":"differential_service_startup_refused","executed":false}\n');return 2
if __name__=='__main__':raise SystemExit(main())
