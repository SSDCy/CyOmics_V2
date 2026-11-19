"""One bounded synthetic composition. No HTTPX/psycopg installed on B.

Explicit HTTPX-stream import/transport facade, DB transaction/version boundary
substitutes and POSIX openat/stat facade. Original Authority.verify, original
PreprocessRepository.read_version/_owner, original FileProvider security methods,
new routes/readers and candidate app assembly actually execute. No SQL/RLS/native
FS/actual auth server/real network/browser/R/scientific acceptance is claimed.
"""
import asyncio,base64,copy,errno,hashlib,json,os,socket,stat,subprocess,sys,time,types
from contextlib import ExitStack
from enum import IntEnum
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

ROOT=Path(__file__).resolve().parents[3]
BASELINE=Path(__file__).resolve().parents[1]
if not (BASELINE/'cyomics_projects/__init__.py').is_file():BASELINE=Path('C:/Users/Lenovo/Documents/Codex/CyOmics_V2/backend')
sys.path.insert(0,str(BASELINE));import cyomics_projects
cyomics_projects.__path__.insert(0,str(Path(__file__).resolve().parents[1]/'cyomics_projects'))
# Dependency facade only; no installer, real connector or driver is invoked.
class TS(IntEnum):IDLE=0;ACTIVE=1;INTRANS=2;INERROR=3;UNKNOWN=4
pq=types.ModuleType('psycopg.pq');pq.TransactionStatus=TS
driver=types.ModuleType('psycopg');driver.AsyncConnection=object;driver.pq=pq
sys.modules['psycopg']=driver;sys.modules['psycopg.pq']=pq
events=[]
class Stream:
    def __init__(self,raw):self.raw=raw;self.status_code=200
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass
    async def aiter_bytes(self):yield self.raw
class Client:
    def __init__(self,*args,**kwargs):pass
    async def aclose(self):pass
    def stream(self,method,url,headers):
        from cyomics_projects.settings import AUTH_USER_URL
        assert (method,url)==('GET',AUTH_USER_URL)
        subject=json.loads(base64.urlsafe_b64decode(headers['Authorization'][7:].split('.')[1]+'=='))['sub']
        events.append('fresh_authority:'+subject)
        return Stream(json.dumps({'id':subject,'aud':'authenticated','role':'authenticated','is_anonymous':False,
            'email':'synthetic@example.invalid','email_confirmed_at':'2025-01-01T00:00:00Z'}).encode())
httpx=types.ModuleType('httpx');httpx.AsyncClient=Client;httpx.AsyncHTTPTransport=object
sys.modules['httpx']=httpx
from cyomics_projects.app import create_app
from cyomics_projects import qc_pca_correlation_owner_read as q,preprocess_repository as p,volcano_owner_read as secure
from cyomics_projects.settings import Fault,EXPECTED_ISSUER

fixture=json.loads((Path(__file__).parent/'fixtures/qc-pca-correlation-descriptor.fixture.json').read_text(encoding='utf-8'))
P=fixture['project_id'];V=fixture['processed_dataset_version_id'];A=fixture['created_by'];B='22222222-2222-4222-8222-222222222222'
parent=fixture['parent']['metadata'];envelope=fixture['design']['envelope'];D=envelope['record']['sample_design_version_id'];REQUEST=envelope['record']['request_id']
canonical=q.canonical;sha=q.sha
bodies={n:('synthetic opaque QC '+n+'\n').encode() for n in q.FILES}
bodies['full-saved-matrix.tsv']=b'synthetic long-data only\n'*50000 # >1MiB boundary, no real cells.
for pin in fixture['artifacts']+fixture['generation']['output_manifest']:
    pin.update(bytes=len(bodies[pin['name']]),sha256=sha(bodies[pin['name']]))
fixture['generation']['script']={k:v for k,v in next(x for x in fixture['artifacts'] if x['name']=='pca_correlation.R').items() if k!='media_type'}
fixture['generation']['r_session']={k:v for k,v in next(x for x in fixture['artifacts'] if x['name']=='R-session-info.txt').items() if k!='media_type'}
registration=dict(fixture,contract=q.REGISTERED)

class Node:
    serial=1
    def __init__(self,body=None,*,uid=1000,mode=None):
        Node.serial+=1;self.ino=Node.serial;self.body=body;self.uid=uid;self.mode=(stat.S_IFDIR|0o700) if body is None else (stat.S_IFREG|0o444)
        if mode is not None:self.mode=mode
        self.nlink=1
    def info(self):return types.SimpleNamespace(st_dev=1,st_ino=self.ino,st_mode=self.mode,st_uid=self.uid,st_gid=1000,
        st_nlink=self.nlink,st_size=0 if self.body is None else len(self.body),st_mtime_ns=1,st_ctime_ns=1)
class FS:
    def __init__(self):
        self.nodes={'/':Node(uid=0,mode=stat.S_IFDIR|0o755),'/qc-private':Node(),'/qc-private/'+V:Node()};self.handles={};self.counter=10;self.mutate=None
        for n,b in bodies.items():self.nodes['/qc-private/'+V+'/'+n]=Node(b)
        self.nodes['/qc-private/'+V+'/metadata.json']=Node(canonical(registration))
    def path(self,name,fd=None):return '/' if name=='/' else self.handles[fd].rstrip('/')+'/'+str(name)
    def open(self,name,flags,dir_fd=None):
        path=self.path(name,dir_fd);events.append('fs_open:'+path)
        if path not in self.nodes:raise FileNotFoundError(path)
        node=self.nodes[path]
        assert flags&os.O_NOFOLLOW and flags&os.O_CLOEXEC
        if stat.S_ISLNK(node.mode):raise OSError(errno.ELOOP,'synthetic symlink')
        if flags&os.O_DIRECTORY:assert stat.S_ISDIR(node.mode)
        self.counter+=1;self.handles[self.counter]=path;return self.counter
    def close(self,fd):del self.handles[fd]
    def fstat(self,fd):return self.nodes[self.handles[fd]].info()
    def lstat(self,name,*,dir_fd,follow_symlinks):assert follow_symlinks is False;return self.nodes[self.path(name,dir_fd)].info()
    def listdir(self,fd):
        prefix=self.handles[fd]+'/'
        return [k[len(prefix):] for k in self.nodes if k.startswith(prefix) and '/' not in k[len(prefix):]]
    def pread(self,fd,size,at):
        path=self.handles[fd];node=self.nodes[path]
        result=node.body[at:at+size]
        if self.mutate==path and result:self.mutate=None;node.ino+=1
        return result

def token(subject=A,role='authenticated'):
    enc=lambda v:base64.urlsafe_b64encode(json.dumps(v).encode()).decode().rstrip('=')
    now=time.time();return '.'.join((enc({'alg':'HS256'}),enc({'sub':subject,'role':role,'aud':'authenticated','iss':EXPECTED_ISSUER,'iat':now-2,'exp':now+120,'is_anonymous':False}),enc('synthetic-only')))

async def main():
    scenarios=[];state={'slow':False};pool=object();repo=p.PreprocessRepository(pool,enabled=True);fs=FS()
    async def run(self,subject,budget,operation,writing=False):
        assert writing is False;events.append('original_owner_transaction:'+subject)
        async def query(sql,args):
            assert sql=='SELECT project_id FROM cyomics_app.projects WHERE owner_id=%s AND project_id=%s'
            return (UUID(P),) if tuple(map(str,args))==(A,P) else None
        return await operation(query)
    async def version(self,query,subject,project,version,*,verify_output):
        assert verify_output is True;events.append('parent_full11_boundary')
        if version!=V:raise Fault(404,'processed_dataset_version_not_found')
        if state['slow']:await asyncio.sleep(0.02)
        return copy.deepcopy(parent)
    async def design(self,subject,project,dataset,family,budget,request_id=None):
        assert (subject,project,dataset,family,request_id)==(A,P,parent['parent_dataset_version_id'],parent['quant_family'],REQUEST)
        events.append('fresh_exact_design');return copy.deepcopy(envelope)
    async def request(app,suffix='',*,subject=A,method='GET',body=b'',query=b'',path=None,headers=None,disconnect=False):
        path=path or '/api/v1/projects/'+P+'/processed-datasets/'+V+'/qc-pca-correlation'+suffix
        hs=[(b'host',b'127.0.0.1:57430'),(b'origin',b'http://127.0.0.1:4206'),(b'sec-fetch-site',b'same-origin')]
        if subject is not None:hs.append((b'authorization',('Bearer '+token(subject)).encode()))
        hs+=headers or [];queue=asyncio.Queue();await queue.put({'type':'http.request','body':body,'more_body':False})
        if disconnect:await queue.put({'type':'http.disconnect'})
        scope={'type':'http','asgi':{'version':'3.0'},'http_version':'1.1','method':method,'scheme':'http','path':path,'raw_path':path.encode(),
            'query_string':query,'root_path':'','headers':hs,'server':('127.0.0.1',57430),'client':('127.0.0.1',12345)}
        out=[]
        async def send(m):out.append(m)
        await app(scope,queue.get,send)
        if not out:return None,{},b''
        return out[0]['status'],dict((k.decode(),v.decode()) for k,v in out[0]['headers']),b''.join(x.get('body',b'') for x in out[1:])
    with ExitStack() as stack:
        for name,value in [('ANALYSIS',fixture['analysis']),('PROJECT',P),('VERSION',V),('META_SHA',sha(canonical(parent))),('DESIGN_VERSION',D),('DESIGN_REQUEST',REQUEST),
            ('DESIGN_SHA',fixture['design']['design_sha256']),('SCRIPT',fixture['generation']['script']),('RECORD_SHA',fixture['generation']['record_sha256']),
            ('OUTPUT_PINS',{n:(len(bodies[n]),sha(bodies[n])) for n in q.OUTPUT_PINS})]:stack.enter_context(patch.object(q,name,value))
        stack.enter_context(patch.object(p.PreprocessRepository,'_run',run));stack.enter_context(patch.object(p.PreprocessRepository,'_read_version',version))
        stack.enter_context(patch.object(q.SampleDesignRepository,'read',design))
        active=create_app(auth_transport=object(),pool=pool,anon_key_loader=lambda:token(role='anon'),preprocessing_repository=repo,sample_designs_enabled=True,
            qc_pca_correlation_read_config=q.Config(True,Path('C:/qc-private')))
        off=create_app(auth_transport=object(),pool=pool,anon_key_loader=lambda:token(role='anon'))
        stack.enter_context(patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')))
        stack.enter_context(patch.object(subprocess,'Popen',side_effect=AssertionError('native forbidden')))
        for name,value in [('O_DIRECTORY',0x10000),('O_NOFOLLOW',0x20000),('O_CLOEXEC',0x40000),('O_NONBLOCK',0x80000)]:stack.enter_context(patch.object(os,name,value,create=True))
        for name,fn in [('open',fs.open),('close',fs.close),('fstat',fs.fstat),('stat',fs.lstat),('listdir',fs.listdir),('pread',fs.pread)]:stack.enter_context(patch.object(os,name,fn,create=True))
        events.clear();status,_,_=await request(off);assert status==503 and events==[];scenarios.append('default_off_no_auth_db_fs')
        events.clear();status,h,raw=await request(active);assert status==200,(status,raw)
        assert json.loads(raw)==fixture;assert events.index('parent_full11_boundary')<next(i for i,x in enumerate(events) if x.startswith('fs_open:'))<events.index('fresh_exact_design')
        scenarios.append('original_Auth_and_Owner_before_FS_exact_design_and_complete_descriptor')
        for n in q.NAMES:
            status,h,raw=await request(active,'/artifacts/'+n);assert status==200,(n,status,raw[:100]);assert raw==bodies[n]
            assert h['content-length']==str(len(raw)) and h['x-cyomics-file-sha256']==sha(raw) and h['content-type']==q.MEDIA[n]
            assert h['content-disposition']=='attachment; filename="'+n+'"'
            for k,v in q.SECURITY.items():assert h[k.lower()]==v
        scenarios.append('all19_exact_bytes_hash_MIME_CSP_attachment_over1MiB_no_original_1MiB_cap')
        for kw,expected in [({'subject':B},404),({'subject':None},401),({'path':'/api/v1/projects/99999999-9999-4999-8999-999999999999/processed-datasets/'+V+'/qc-pca-correlation'},404),
            ({'path':'/api/v1/projects/'+P+'/processed-datasets/99999999-9999-4999-8999-999999999999/qc-pca-correlation'},404),({'query':b'x=1'},400),({'body':b'x'},400),({'method':'POST'},405),({'suffix':'/artifacts/full-provenance.rds'},404),({'suffix':'/artifacts/../PCA.svg'},404)]:
            events.clear();status,_,_=await request(active,**kw);assert status==expected,(kw,status);assert not any(x.startswith('fs_open:') for x in events)
        scenarios.append('foreign_Owner_project_version_unauth_body_query_method_privateRDS_and_path_denied_before_FS')
        path='/qc-private/'+V+'/metadata.json';node=fs.nodes.pop(path);status,_,raw=await request(active);assert status==404 and json.loads(raw)['error']['code']=='qc_pca_correlation_not_ready';fs.nodes[path]=node
        target='/qc-private/'+V+'/PCA.svg';node=fs.nodes[target]
        for field,bad in [('body',b'corrupt'),('uid',1001),('mode',stat.S_IFREG|0o644),('mode',stat.S_IFLNK|0o444),('nlink',2)]:
            old=getattr(node,field);setattr(node,field,bad);status,_,raw=await request(active);assert status==503 and json.loads(raw)['error']['code']=='qc_pca_correlation_integrity_failed',(field,status,raw);setattr(node,field,old)
        fs.mutate=target;status,_,_=await request(active);assert status==503
        root=fs.nodes['/qc-private'];root.mode=stat.S_IFDIR|0o722;status,_,_=await request(active);assert status==503;root.mode=stat.S_IFDIR|0o700
        scenarios.append('missing_registration_not_ready_corrupt_bytes_UID_write_mode_symlink_hardlink_inode_mutation_unsafe_root_rejected')
        real_root=q.FileProvider._root
        def timed_root(provider,stack,check):
            def expired_checkpoint():raise TimeoutError('explicit FS checkpoint timeout')
            return real_root(provider,stack,expired_checkpoint)
        with patch.object(q.FileProvider,'_root',timed_root):
            status,_,raw=await request(active);assert status==504 and json.loads(raw)['error']['code']=='qc_pca_correlation_read_timeout'
        scenarios.append('FS_checkpoint_TimeoutError_preserved_to_HTTP504')
        state['slow']=True;status,_,raw=await request(active,headers=[(b'x-cyomics-remaining-ms',b'1'),(b'x-cyomics-deadline-unix-ms',str(int(time.time()*1000)+1000).encode())]);assert status==504,(status,raw);state['slow']=False
        status,_,_=await request(active,disconnect=True);assert status is None
        scenarios.append('real_budget_shrinking_and_disconnect_no_late_success')
        assert fs.handles=={},fs.handles
        await active.state.authority.close();await off.state.authority.close()
    print(json.dumps({'task':'S1-QC-PCA-CORRELATION-OWNER-READ-BE-001','status':'PASS','new_target_runs':1,'scenarios':scenarios,
        'substitutes':['HTTPX import+stream transport facade: original Authority.verify executes','psycopg import facade; original Owner method executes with synthetic transaction/version bundle boundary','POSIX FS openat/stat facade: original secure methods execute','fixed reviewed profile pins substituted only inside target with synthetic fixture values'],
        'actual_Auth_HTTP_DB_RLS_native_FS_R_scientific_validation':False,'source_default_off':True}))

if __name__=='__main__':asyncio.run(main())
