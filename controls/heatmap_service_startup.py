"""Pinned Root service shim template. Import is inert; no HTTP or approvals.

Future Root bootstrap supplies one scope/config after physical review. Persistent
control/staging/cache bases survive API restart; no historical claim is cleared.
"""
from pathlib import Path
import hashlib,importlib.util,json,os,stat,sys,time

HERE=Path(__file__).parent
BASE=Path('/home/user01/Cy_Omics_v2.0')
CONTROL=BASE/'runtime/r-heatmap-render-001/control'
STAGING=BASE/'runtime/r-heatmap-render-001/staging'
PUBLISHED=BASE/'runtime/r-heatmap-saved-001'

def need(ok):
    if not ok:raise RuntimeError('heatmap_service_startup_refused')
def canonical(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def sha(raw):return hashlib.sha256(raw).hexdigest()
def read(path,cap=2097152,private=False):
    need(not any(p.is_symlink() for p in (path,*path.parents)))
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        before=os.fstat(fd);need(stat.S_ISREG(before.st_mode) and before.st_uid==1000 and before.st_nlink==1
            and not before.st_mode&0o022 and 0<before.st_size<=cap)
        if private:need(stat.S_IMODE(before.st_mode)==0o600)
        raw=os.read(fd,cap+1);after=os.fstat(fd)
        need(len(raw)==before.st_size and (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)==
            (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns));return raw
    finally:os.close(fd)
def write_once(path,raw):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
    fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:os.fsync(fd)
    finally:os.close(fd)
def load_base():
    control=json.loads(read(HERE/'control-pins.json'))
    for name in ('classification_service_startup.py','heatmap_service_startup.py'):
        raw=read(HERE/name);need({'bytes':len(raw),'sha256':sha(raw)}==control['control_files'][name])
    backend=Path(control['manifests']['source']['root'])/'backend';need(Path.cwd()==backend);sys.path.insert(0,str(backend))
    spec=importlib.util.spec_from_file_location('heatmap_physical_checks',HERE/'classification_service_startup.py')
    helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
    ops,checked,pins,native=helper.load_control();need(control==checked)
    from cyomics_projects.raw_qc_artifacts import open_root
    for root in (CONTROL,STAGING,PUBLISHED):
        fd=open_root(root)
        try:need(stat.S_IMODE(os.fstat(fd).st_mode)==0o700)
        finally:os.close(fd)
    return helper,ops,control,pins,native,backend
def profile(control):
    from cyomics_projects import heatmap_runner as runner
    source=json.loads(read(Path(control['manifests']['source']['path'])))
    return {'contract':'r_heatmap_profile_v0.1','library_root':'/usr/local/lib/R/site-library',
        'library_metadata':[dict(path=p,bytes=n,sha256=s) for p,n,s in runner.KNOWN_METADATA],
        'font':runner.KNOWN_FONT,'versions':{'R':'4.6.1','pheatmap':'1.0.13','digest':'0.6.39'},
        'adapter':source['files']['backend/cyomics_projects/heatmap_runner.py']}
def launcher(control,backend,verify):
    from cyomics_projects.classification_on_demand import Config
    return Config(enabled=True,startup=HERE/'heatmap_service_startup.py',backend=backend,control=CONTROL,
        startup_sha256=control['control_files']['heatmap_service_startup.py']['sha256'],
        python_sha256='e74c565bbccaaf3c2ec6527962890418ab6fe740c19129b02ae1b7effc322230',verify=verify)

def make_api_config(control,*,scope_json,expires_unix):
    helper,ops,checked,pins,native,backend=load_base();need(checked==control)
    from cyomics_projects.heatmap_on_demand import Config,prepare
    now=time.time();need(type(expires_unix) in (int,float) and now+432<expires_unix<=now+43200)
    api=ops.identity(os.getpid());ops.validate('api',api,control)
    from cyomics_projects.validation import strict_json
    scope=strict_json(scope_json);p=profile(control)
    context={'schemaVersion':1,'contract':'root_heatmap_service_context_v0.1','api':api,'approved_unix':now,'expires_unix':expires_unix,
        'source_manifest_sha256':control['manifests']['source']['manifest_file']['sha256'],
        'control_sha256':sha(read(HERE/'control-pins.json')),'scope':scope,'profile':p,
        'automatic_retry':False,'maximum_parallel_children':1,'child_total_seconds':420,'native_R_wall_seconds':60}
    # Profile excludes API pid/expiry/control hash: restart cannot change dedup identity.
    def verify():
        helper.verify_snapshot(pins);need(ops.identity(os.getpid())==api and now<=time.time()<expires_unix)
    cfg=Config(launcher(control,backend,verify),STAGING,PUBLISHED,canonical(p),canonical(scope));prepare(cfg)
    path=HERE/'heatmap-service-context.json';write_once(path,canonical(context));pins[path]=helper.identity(path)
    return cfg

def child_config(notification):
    helper,ops,control,pins,native,backend=load_base()
    from cyomics_projects.heatmap_on_demand import Config,prepare
    path=HERE/'heatmap-service-context.json';raw=read(path,private=True);context=ops.strict_json(raw);pins[path]=helper.identity(path)
    need(raw==canonical(context) and set(context)=={'schemaVersion','contract','api','approved_unix','expires_unix',
        'source_manifest_sha256','control_sha256','scope','profile','automatic_retry','maximum_parallel_children','child_total_seconds','native_R_wall_seconds'}
        and type(context['schemaVersion']) is int and context['schemaVersion']==1 and context['contract']=='root_heatmap_service_context_v0.1'
        and context['control_sha256']==sha(read(HERE/'control-pins.json'))
        and context['source_manifest_sha256']==control['manifests']['source']['manifest_file']['sha256']
        and context['profile']==profile(control) and context['automatic_retry'] is False and context['maximum_parallel_children']==1
        and context['child_total_seconds']==420 and context['native_R_wall_seconds']==60)
    parent=context['api'];ops.validate('api',parent,control);need(os.getppid()==parent['pid'] and ops.identity(parent['pid'])==parent)
    own=os.getpid();start=int(Path('/proc/self/stat').read_bytes().rsplit(b')',1)[1].split()[19])
    intent=CONTROL/(notification['job_id']+'.launch.once');raw=read(intent,4096,True)
    from cyomics_projects.classification_on_demand import canonical as wire
    need(raw==wire(notification)+b'\n');pins[intent]=helper.identity(intent)
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
    cfg=Config(launcher(control,backend,verify),STAGING,PUBLISHED,canonical(context['profile']),canonical(context['scope']))
    prepare(cfg);verify();return cfg,activate,verify,native

def main():
    if sys.argv[1:]!=['--once']:return 2
    try:
        load_base()
        from cyomics_projects.heatmap_on_demand import parse_notification,main_from_server
        raw=sys.stdin.buffer.read(1025);n=parse_notification(raw);config,activate,verify,native=child_config(n)
        return main_from_server(raw,config=config,activate_factory=activate,verify=verify,native_pins=native)
    except BaseException:
        sys.stdout.write('{"status":"heatmap_service_startup_refused","executed":false}\n');return 2
if __name__=='__main__':raise SystemExit(main())
