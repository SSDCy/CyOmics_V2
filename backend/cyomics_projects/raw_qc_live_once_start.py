"""Fixed private ordinary once startup; no server/user arguments or secrets."""
import os,sys,threading
from pathlib import Path
BACKEND=Path('/home/user01/Cy_Omics_v2.0/releases/raw-qc-live-review-001/candidate/backend')
BASE=Path('/home/user01/Cy_Omics_v2.0/runtime/raw-qc-live-001')
NS={'pid':'pid:[4026532213]','net':'net:[4026531833]','mnt':'mnt:[4026532211]'}
NATIVE=(('R_HOME','/usr/lib/R',2096,100664531,0,0o755),
 ('R_SHARE_DIR','/usr/share/R/share',2096,100665610,0,0o755),
 ('R_INCLUDE_DIR','/usr/share/R/include',2096,100665564,0,0o755),
 ('R_DOC_DIR','/usr/share/R/doc',2096,100665518,0,0o755),
 ('LD_LIBRARY_PATH','/usr/lib/R/lib',2096,100664563,0,0o755))

def main(argv=()):
    if tuple(argv)!=('--once',):
        # Default no filesystem/config import, R, DB or credential read.
        sys.stdout.write('{"status":"off","executed":false}\n');return 2
    try:
        if not (os.name=='posix' and sys.platform.startswith('linux') and sys.dont_write_bytecode and os.getuid()==os.geteuid()==1000 and threading.current_thread() is threading.main_thread() and threading.active_count()==1):raise ValueError()
        if {k:os.readlink('/proc/self/ns/'+k) for k in NS}!=NS:raise ValueError()
        if Path.cwd()!=BACKEND:raise ValueError()
        sys.path.insert(0,str(BACKEND))
        from cyomics_projects.raw_qc_runtime import Config as RuntimeConfig,NativeDirectoryPin
        from cyomics_projects.raw_qc_checkpoint import WorkerRoots
        from cyomics_projects.raw_qc_dispatch import Config
        from cyomics_projects.raw_qc_once_entry import main as ordinary_main
        roots=WorkerRoots(BASE/'control',BASE/'inputs',BASE/'staging',BASE/'published',BASE/'receipts')
        runtime=RuntimeConfig(roots.inputs,roots.staging,roots.published,1000,1000,
            tuple(NativeDirectoryPin(k,Path(p),dev,ino,owner,mode) for k,p,dev,ino,owner,mode in NATIVE))
    except BaseException:
        sys.stdout.write('{"status":"startup_refused","executed":false}\n');return 2
    # Original exclusive MainThread entry; no selector, ACK or next action.
    return ordinary_main(('--once',),config=Config(runtime,roots,1000))

if __name__=='__main__':raise SystemExit(main(tuple(sys.argv[1:])))
