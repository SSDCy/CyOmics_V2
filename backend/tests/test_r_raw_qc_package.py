"""Portable package/source checks, not Native execution or containment tests."""
import ast,hashlib,importlib,importlib.util,json,os,sys,types,unittest
from pathlib import Path
from unittest.mock import patch
BACKEND=Path(__file__).resolve().parents[1]
PACKAGE=BACKEND/'cyomics_projects/r_raw_qc'
PINS={'__init__.py': {'bytes': 160, 'sha256': '9be1170a3e01df5833d5bec918dc0b18fa1942086231431529054a979c75111a'}, 'output_contract.py': {'bytes': 8474, 'sha256': '6966be18a920ad4f7c6bba0251e84966602a5294ea2c37689904846282e68a02'}, 'process_policy.py': {'bytes': 13634, 'sha256': '6703e0b23ad7b0d5fdb065dda523807531507760f9b9086f6bf4733e906cad71'}, 'raw_qc_runner.py': {'bytes': 26963, 'sha256': '86d6fc903f09a901b39a0c39a174e2e91411efccbe11eff8c0617154bbe43152'}}
ASTS={'raw_qc_runner.py': 'b0afd0c70571e5f15b46ad201db5cd42fe9a2b6b114495bbd7cc46dcb5215e3c', 'process_policy.py': '4646d7322e6cf44c645d71dfdf3085610347f9873de2a6840a70e699f72454a8', 'output_contract.py': 'd1d6eb50d10ad2abc08a6cda12a2bd7fb25ab6d35178fb31a3fae6e5e432d7de'}
SCRIPT_SHA='2072a20e9dca53ab9ff03ef5a0a5d6aa9556dcee8004b5f28ce2fa4345624d64'
PROFILE_SHA='fe1e05ac949ed027969d4ae4475e0e66bbc44cdc1d9310486d33aa334f40aad0'
POLICY_SHA='2ab8f62a3e1973711c2bd63940161a72d57d4daa641e22967b12c28ed5d5fede'
def sha(raw):return hashlib.sha256(raw).hexdigest()
class Strip(ast.NodeTransformer):
    def visit_Import(self,node):return None
    def visit_ImportFrom(self,node):return None
def normalize(node):
    if isinstance(node,ast.AST):return {'node':type(node).__name__,'fields':{k:normalize(v) for k,v in ast.iter_fields(node) if not(k=='type_params' and not v)}}
    if isinstance(node,list):return [normalize(v) for v in node]
    if node is Ellipsis:return {'literal':'Ellipsis'}
    if isinstance(node,bytes):return {'bytes_hex':node.hex()}
    return node
class PackageTests(unittest.TestCase):
    def test_complete_python_source_pins(self):
        self.assertEqual(set(PINS),{'__init__.py','raw_qc_runner.py','process_policy.py','output_contract.py'})
        for name,pin in PINS.items():
            raw=(PACKAGE/name).read_bytes();self.assertEqual((len(raw),sha(raw)),(pin['bytes'],pin['sha256']))
    def test_scientific_script_is_exact_approved_source(self):
        raw=(PACKAGE/'approved_raw_qc.R').read_bytes();self.assertEqual((len(raw),sha(raw)),(4949,SCRIPT_SHA))
    def test_inert_initializer_has_no_runtime_import_or_call(self):
        raw=(PACKAGE/'__init__.py').read_bytes();tree=ast.parse(raw)
        self.assertEqual(len(tree.body),1);self.assertIsInstance(tree.body[0],ast.Expr);self.assertIsInstance(tree.body[0].value,ast.Constant)
        self.assertIsInstance(tree.body[0].value.value,str)
        spec=importlib.util.spec_from_file_location('_raw_qc_pkg_only',PACKAGE/'__init__.py',submodule_search_locations=[str(PACKAGE)])
        loaded=importlib.util.module_from_spec(spec);spec.loader.exec_module(loaded)
        self.assertNotIn('RawQCRunner',vars(loaded));self.assertNotIn('process_policy',vars(loaded))
        # Guard package-body execution, excluding Python's normal source loader I/O.
        module=types.ModuleType('_qc_init_only')
        def denied(*args,**kwargs):raise AssertionError('package-body I/O/probe')
        with patch('builtins.open',denied),patch('os.open',denied),patch('os.scandir',denied),patch('os.stat',denied):
            exec(compile(tree,'__init__.py','exec'),module.__dict__)
        self.assertNotIn('RawQCRunner',vars(module));self.assertNotIn('process_policy',vars(module))
    def test_algorithm_AST_unchanged_and_imports_relative(self):
        for name,expected in ASTS.items():
            tree=Strip().visit(ast.parse((PACKAGE/name).read_bytes()))
            self.assertEqual(sha(json.dumps(normalize(tree),sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()),expected)
        tree=ast.parse((PACKAGE/'raw_qc_runner.py').read_bytes())
        imports=[n for n in tree.body if isinstance(n,ast.ImportFrom) and n.level==1]
        self.assertTrue(any(n.module=='output_contract' for n in imports))
        self.assertTrue(any(n.module is None and [a.name for a in n.names]==['process_policy'] for n in imports))
        self.assertFalse(any(isinstance(n,ast.Import) and any(a.name=='process_policy' for a in n.names) for n in tree.body))
    def test_v3_profile_identity_without_importing_executor(self):
        tree=ast.parse((PACKAGE/'raw_qc_runner.py').read_bytes())
        limits=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Limits')
        values={n.target.id:ast.literal_eval(n.value) for n in limits.body if isinstance(n,ast.AnnAssign)}
        profile=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='LIMITS_PROFILE_ID' for t in n.targets))
        self.assertEqual(profile,'raw_qc_limits_v3')
        data={'limits_profile':profile,'limits':values}
        self.assertEqual(sha(json.dumps(data,sort_keys=True,separators=(',',':'),allow_nan=False).encode()),PROFILE_SHA)
    def test_development_policy_identity_and_honest_limits(self):
        tree=ast.parse((PACKAGE/'process_policy.py').read_bytes())
        assignment=next(n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='POLICY' for t in n.targets))
        policy=ast.literal_eval(assignment.value.args[0])
        self.assertEqual(sha(json.dumps(policy,sort_keys=True,separators=(',',':'),allow_nan=False).encode()),POLICY_SHA)
        self.assertFalse(policy['hard_per_job_pids_cap']);self.assertFalse(policy['all_descendant_containment_or_cleanup_guaranteed'])
        self.assertTrue(policy['CPU_AS_are_per_process_not_job_aggregate']);self.assertFalse(policy['background_monitor'])
    @unittest.skipUnless(os.name=='posix','POSIX executor module import not attempted on this platform; package presence is not availability')
    def test_explicit_POSIX_module_imports_resolve_inside_package_only(self):
        # Pure import only. No Config/Runner construction, runtime probe, R or input.
        # Exact package overlay, independent of any installed baseline package.
        name='_raw_qc_package_under_test'
        spec=importlib.util.spec_from_file_location(name,PACKAGE/'__init__.py',submodule_search_locations=[str(PACKAGE)])
        package=importlib.util.module_from_spec(spec);sys.modules[name]=package;spec.loader.exec_module(package)
        module=importlib.import_module(name+'.raw_qc_runner')
        self.assertEqual(Path(module.__file__).resolve().parent,PACKAGE.resolve())
        self.assertEqual(module.process_policy.__name__,name+'.process_policy')
        self.assertEqual(module.validate_outputs.__module__,name+'.output_contract')
        self.assertEqual(module.APPROVED_SCRIPT_SHA256,SCRIPT_SHA)
if __name__=='__main__':unittest.main(verbosity=2)
