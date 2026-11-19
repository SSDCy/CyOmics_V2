"""One fixed saved-None-parent Quantile strategy; data contracts, never approval."""
import hashlib
import json

STRATEGY = 'limma_quantile_saved_parent_v1'
CORE_CONTRACT = 'limma_quantile_saved_log2_no_imputation_v1'
PROPOSAL = 'quantile_saved_parent_source_proposal_v1'
CORE_PIN = {'bytes': 10849, 'sha256': '943e63ac82ecf317e87c514a9b891c94b401c83de9916d64ff07174c080ac5b6'}
# Filled from the final isolated source bytes, never from a client/environment.
PINS = {'script': {'bytes': 5120, 'sha256': 'b9d0026fc068d5f12ad303c1921151dabc69be7556ac38f44216bbc0892ec1d1'}, 'validator': {'bytes': 9031, 'sha256': '892154895ab61b1a9efc0c8f3f190276c5179b452794234c4abf2ac3032122b2'}, 'runner': {'bytes': 18063, 'sha256': 'a0f0b07b6767f1b00d35572f3a0be7b7deff6e2bb678e3b9dcc1cb52a2bb8949'}, 'executable': {'bytes': 14488, 'sha256': '9c572a708d444e0ec91c59935bf9b5c3fd426a75fe2b7101b2a65f4783155283'}}
SUMMARY_COLUMNS = ('strategy_contract', 'core_contract', 'normalization', 'imputation', 'analysis_ready', 'parent_processed_dataset_version_id', 'parent_manifest_sha256', 'parent_dataset_version_id', 'parent_file_id', 'parent_source_sha256', 'sample_ordinal', 'sample_name_raw', 'observed_count', 'missing_count', 'imputed_count', 'original_blank_count', 'original_zero_count', 'input_median_log2', 'output_median_log2', 'minimum_change_log2', 'maximum_change_log2', 'scalar_factor', 'factor_status', 'warning_codes')


def parameters(flags,parent):
    from . import preprocess_contract as c
    c.exact(parent,('processed_dataset_version_id','manifest_sha256'))
    c._need(c._canonical_uuid(parent['processed_dataset_version_id']) and c.hashed(parent['manifest_sha256']))
    result=c._parameter_value(flags)
    result.update(normalization='quantile',preset='positive_log2_quantile_zero_na_no_impute_v1',
        normalization_parent=dict(parent))
    result['steps'][3]='normalization_quantile'
    return result


def is_quantile(value):
    return type(value) is dict and value.get('normalization')=='quantile'


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
    if is_quantile(params)!=(execution['contract']==PROPOSAL):p.fail()


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

DEPENDENCIES = {'library_root': '/home/user01/Cy_Omics_v2.0/audit/R-LIMMA-ENV-003/staged-library', 'limma_version': '3.68.5', 'library_files_sha256': '7ab763eb038759748400b1ff055f2d4ea2733fd81a4ded99de2169670f76ff56', 'library_file_count': 36, 'statmod_version': '1.5.2', 'digest_version': '0.6.39', 'digest_library_root': '/usr/local/lib/R/site-library/digest', 'digest_file_count': 43, 'digest_inventory_sha256': 'f99916f754e37a8051e2994f1ca7ad817abece22aa932f57d45effc4c9ed1619', 'namespace_paths': {'limma': '/home/user01/Cy_Omics_v2.0/audit/R-LIMMA-ENV-003/staged-library/limma', 'statmod': '/home/user01/Cy_Omics_v2.0/audit/R-LIMMA-ENV-003/staged-library/statmod', 'digest': '/usr/local/lib/R/site-library/digest'}, 'child_library_paths': ['/home/user01/Cy_Omics_v2.0/audit/R-LIMMA-ENV-003/staged-library', '/usr/local/lib/R/site-library', '/usr/lib/R/library']}
LIBRARY_FILES = {'limma/CITATION': {'bytes': 791, 'sha256': '9c4d916b7c7a7703e2c61420635e9016badd2f6b37f0f3132cce76c5acd45b54'}, 'limma/DESCRIPTION': {'bytes': 4634, 'sha256': '45740bc22236fac00d7ca1de8428d512a6a8a8bd033a0d02f6b2adc0fdea640b'}, 'limma/INDEX': {'bytes': 12636, 'sha256': '8e2463a8a9b8708f0f5d354a152307c9db1422e18c774b1bb9365e13d3f48532'}, 'limma/Meta/Rd.rds': {'bytes': 8074, 'sha256': '60b72fa91f5aaa7165a59d1b0ae37dd768329625b54320422db2a6c9bfcb1180'}, 'limma/Meta/features.rds': {'bytes': 123, 'sha256': 'b4f074bc0f45cfe56d976c0d67a1453067f4b34693828ed02d674a6641b3e332'}, 'limma/Meta/hsearch.rds': {'bytes': 7649, 'sha256': 'b159d0ef38fa40686c68422692e24de973d0272707e7e20d730b12155e17d957'}, 'limma/Meta/links.rds': {'bytes': 3750, 'sha256': '14da8250a6190ea5f17ba1f6d34ad59c34b5e440e338a783d3e6e8f6a6796bc0'}, 'limma/Meta/nsInfo.rds': {'bytes': 1308, 'sha256': '3758cf93936197939d22e870eb241bdca4c2e3530277d0200fb6b10b8fbdfc01'}, 'limma/Meta/package.rds': {'bytes': 1891, 'sha256': 'aca1ac303fe21e2cc83e27e03891fd2ecbac749a162d7401f8c68c242472173d'}, 'limma/Meta/vignette.rds': {'bytes': 209, 'sha256': '19ae3b722005aefd160cda2e653c7a08f63e2668d018a106bb9e6eb55f145f89'}, 'limma/NAMESPACE': {'bytes': 4808, 'sha256': '7aec816f42ef3d70cc4a0709fe4e076662e2f1a5583040be73f525d1b0830973'}, 'limma/NEWS.Rd': {'bytes': 77763, 'sha256': 'f73df67a6c38180a3f15a8f7f1318a42b0fe1b7910fd2cfa0a44c8647c31ed15'}, 'limma/R/limma': {'bytes': 1058, 'sha256': '570ca456b280cdeb201ef5ebdf22dc8f80092e2c0c68e33c7f73340e420f3759'}, 'limma/R/limma.rdb': {'bytes': 962424, 'sha256': 'aee82cbc8cd05936fb76f6fc2ca3c31a598fbffd12a2207fcf53e87ea46c91a9'}, 'limma/R/limma.rdx': {'bytes': 5536, 'sha256': 'c5a366dba6dfe16b028d1a085804123e0e1e7d007942e21ea0a93b4b77f9d00b'}, 'limma/doc/changelog.txt': {'bytes': 199787, 'sha256': '39d8d75922ecc737e7583510115afe03d3906d7c236fdb8a45877c11185a9fcf'}, 'limma/doc/index.html': {'bytes': 1588, 'sha256': 'bd505b1435f9fba2d310c5c8130273ed8fa89fad63406c8b7156fb20f6e411d6'}, 'limma/doc/usersguide.Rnw': {'bytes': 202, 'sha256': '66a38015785ade2aed8bbd52fdd92e72c9ef44ee532459ce8a9bbf12ddb1315d'}, 'limma/libs/limma.so': {'bytes': 54408, 'sha256': 'b2fbbe74a3ed5dda833081298c2256581259ae6bb0bdbac383799ce67ee5c38a'}, 'statmod/CITATION': {'bytes': 3531, 'sha256': 'effadab1ee1a2125880eb1981b7f5da149b8f3539ac3df16dfb78796569c60c1'}, 'statmod/DESCRIPTION': {'bytes': 1781, 'sha256': '78df2466dbb91e25379a08c3b648c9b7942313f1d8d2a911fe410a623cee2f7c'}, 'statmod/INDEX': {'bytes': 2148, 'sha256': '80fe7744e002db35b88b42dad78a1a938f62aec6b39853cea62e3128e2d04fa8'}, 'statmod/Meta/Rd.rds': {'bytes': 1713, 'sha256': '41ece762cd036409d6964f6dd209861c56dc54ca0ca9600cb5020d708f21e96a'}, 'statmod/Meta/data.rds': {'bytes': 117, 'sha256': 'd507e7540c3bad6c4b60ae96d86c1bb1f783299d924e6fa324a1d3ed7d352e24'}, 'statmod/Meta/features.rds': {'bytes': 123, 'sha256': 'b4f074bc0f45cfe56d976c0d67a1453067f4b34693828ed02d674a6641b3e332'}, 'statmod/Meta/hsearch.rds': {'bytes': 1681, 'sha256': '8920759769437ffb69cd2a0e63131dd96bbfd483b0d3c61c00a67e93c4babf18'}, 'statmod/Meta/links.rds': {'bytes': 714, 'sha256': 'ff73e719d037196ae6bc2095c0fed125a556b7a82fc6528e329db86cd6584058'}, 'statmod/Meta/nsInfo.rds': {'bytes': 899, 'sha256': '5743579be5bc6567538ca6a8d1460e1fd35b17804070e6ac4b9a6da0022df448'}, 'statmod/Meta/package.rds': {'bytes': 1176, 'sha256': 'c5a5dba8e8061a1b4bb84f795135cd0e2d7ab1566fa84b62f6954cb17536eca4'}, 'statmod/NAMESPACE': {'bytes': 1495, 'sha256': '167189bb507181955d95ca92c3b2b8a0b903bd8ca4ffbf7662129f1f77b6b1e6'}, 'statmod/NEWS': {'bytes': 18355, 'sha256': '9a89e1969025cdf6075a95a1bd4df6f8fefe49698a59890192b23267d2e83e5d'}, 'statmod/R/statmod': {'bytes': 1058, 'sha256': '570ca456b280cdeb201ef5ebdf22dc8f80092e2c0c68e33c7f73340e420f3759'}, 'statmod/R/statmod.rdb': {'bytes': 151155, 'sha256': '1d3b053977a9db27dc734233925531ada99b1061f0f06e7b199c0772ab5e2b81'}, 'statmod/R/statmod.rdx': {'bytes': 1070, 'sha256': '8d5ecaf6c99f2796ffc331ed4b21fdfd16238baa4c67595624895abe1f8d5c51'}, 'statmod/data/welding.rdata': {'bytes': 1252, 'sha256': '206af06f9a6b9c8ce3ffc42b15b09e4eca8048dcbae65989aeb90d4e27b507b6'}, 'statmod/libs/statmod.so': {'bytes': 79216, 'sha256': '2ce4e42cd18e2981207ba4f8fd8e885bed1b273875c97075dcf2323618ef6998'}}

DIGEST_INVENTORY = {'root_uid': 0, 'root_mode': 493, 'files': {'CITATION': {'bytes': 624, 'sha256': '472ecc524a96bfb425d32169fc6450828f88f97ffaf20533c31b4eddef18a344', 'uid': 0, 'mode': 420}, 'DESCRIPTION': {'bytes': 7267, 'sha256': '0a9586e98f8fd853752f8cfb7220e98c8622288b15e342f8c174f35168f92901', 'uid': 0, 'mode': 420}, 'GPL-2': {'bytes': 18092, 'sha256': '8177f97513213526df2cf6184d8ff986c675afb514d4e68a404010521b880643', 'uid': 0, 'mode': 420}, 'INDEX': {'bytes': 531, 'sha256': '631b6eafadd79f485a5ab8752bb7020ffefa51a11ae68f016c9601d2e1bebb9b', 'uid': 0, 'mode': 420}, 'Meta/Rd.rds': {'bytes': 686, 'sha256': 'fefd8bdc9b5233586de3eac57ceaa04bd32787f6e9712e90da2dc998324c980d', 'uid': 0, 'mode': 420}, 'Meta/demo.rds': {'bytes': 134, 'sha256': 'bcbcb7f32837e781c676ae5f8432aa64629383023ed60bcd6edf7d12e811a34f', 'uid': 0, 'mode': 420}, 'Meta/features.rds': {'bytes': 123, 'sha256': 'b4f074bc0f45cfe56d976c0d67a1453067f4b34693828ed02d674a6641b3e332', 'uid': 0, 'mode': 420}, 'Meta/hsearch.rds': {'bytes': 717, 'sha256': 'ecc22f7b3896b8e800ca186903494dc28d7e4a28efec76b54aa608e80e0970c2', 'uid': 0, 'mode': 420}, 'Meta/links.rds': {'bytes': 418, 'sha256': '753d18b6393bf96cbc056f181b1ef1e4f70a348111d9dfe29a7cf712d83cb1d2', 'uid': 0, 'mode': 420}, 'Meta/nsInfo.rds': {'bytes': 643, 'sha256': '5dbe3e294e730344b23aca428b85603e97a20823d7edb4c06aa51b9149cf1095', 'uid': 0, 'mode': 420}, 'Meta/package.rds': {'bytes': 2543, 'sha256': '8c8b644371df065e2d0af642e613aefe802172e77ad4335a4736edeecf3b4fcb', 'uid': 0, 'mode': 420}, 'Meta/vignette.rds': {'bytes': 210, 'sha256': '14a3dd56a82aae8604f1b33e46205807c72db4228d51f03eff3a3345a77c55b9', 'uid': 0, 'mode': 420}, 'NAMESPACE': {'bytes': 1156, 'sha256': '59af728247a71e33aa313407ff9085abf31d284a463e58965e932dfecaf92ee2', 'uid': 0, 'mode': 420}, 'R/digest': {'bytes': 1058, 'sha256': '570ca456b280cdeb201ef5ebdf22dc8f80092e2c0c68e33c7f73340e420f3759', 'uid': 0, 'mode': 420}, 'R/digest.rdb': {'bytes': 46207, 'sha256': '72a5014ad5ecc1e60ba5ba1e8c1d81b1de0e56e0a22c9dbda8af0670519d3f20', 'uid': 0, 'mode': 420}, 'R/digest.rdx': {'bytes': 1065, 'sha256': 'd44f6f5b1f41ed1ef725a79ebf6da2f1e54e1c6b699b1d273faf91fb5e76f794', 'uid': 0, 'mode': 420}, 'README.md': {'bytes': 4112, 'sha256': '46829b782062dd3ff106763d57b91f83388c57a0c730f898581a981424defb02', 'uid': 0, 'mode': 420}, 'demo/vectorised.R': {'bytes': 918, 'sha256': '7f17834f4bfc96d3718629a581480c439dee3e8295474987a7ffcefda928acdf', 'uid': 0, 'mode': 420}, 'doc/index.html': {'bytes': 1353, 'sha256': 'd6a61c4f31f3abfa52939a530c649fa500b9883047738168f4383dbcf96ad79b', 'uid': 0, 'mode': 420}, 'doc/sha1.R': {'bytes': 2808, 'sha256': '5be39e631f2834a0c274ca3723db3fe7655e686cbf6088aa7a13136528293015', 'uid': 0, 'mode': 420}, 'doc/sha1.html': {'bytes': 71109, 'sha256': '20d1c3fe6a1564233464a4fd39da2173b0ceb7c2bf46a95f39e06f6e36cd6018', 'uid': 0, 'mode': 420}, 'doc/sha1.md': {'bytes': 7298, 'sha256': 'fc23285bd2263b289ae97f1041c92d5fddcca2e95cf91fd29dba8255989e1c56', 'uid': 0, 'mode': 420}, 'help/AnIndex': {'bytes': 638, 'sha256': '3dce18980b6840a5d4e15b7144c285df3a77c71fac52f8ad5b4cf758e5c74184', 'uid': 0, 'mode': 420}, 'help/aliases.rds': {'bytes': 348, 'sha256': '12a94d4fd31428a417781556531086b2c7dcad9c34f6d3f6e27fc1ee3a329048', 'uid': 0, 'mode': 420}, 'help/digest.rdb': {'bytes': 39668, 'sha256': '3114a01c70fff517bc4fef6bfd711df609aa4981c35398ece1475b0739d956d7', 'uid': 0, 'mode': 420}, 'help/digest.rdx': {'bytes': 304, 'sha256': '97c6840e3ffecd40a9641296153409b05dfd44fd606bd2278a423b6797dd7a0e', 'uid': 0, 'mode': 420}, 'help/paths.rds': {'bytes': 185, 'sha256': 'de58d951aec6aed0fc5b0287ee2ce523cad15159323cefdc23d240179bd325c8', 'uid': 0, 'mode': 420}, 'html/00Index.html': {'bytes': 5587, 'sha256': '4444fa6a5a553515307acf2b676d985045f7243e2b61c7adb45402c2094ed611', 'uid': 0, 'mode': 420}, 'html/R.css': {'bytes': 2133, 'sha256': '3d290675266eebeba6efff07db879d699843c80bf428f9e71000e2fc6078f5db', 'uid': 0, 'mode': 420}, 'include/pmurhashAPI.h': {'bytes': 3165, 'sha256': '554bc6bdac616df8e27a024b1a4558a994f874cdc0f88e4320c92f3f442f9fcf', 'uid': 0, 'mode': 420}, 'libs/digest.so': {'bytes': 863896, 'sha256': 'de075acc4efd4114f12f09161f60af7949a5f0e00eb39fafce9925537a8ed897', 'uid': 0, 'mode': 493}, 'tinytest/test_aes.R': {'bytes': 6596, 'sha256': 'cab0c5c92a5c8ca60e1cec177c05b18d1d2442d48e999a967214ae8c3548b239', 'uid': 0, 'mode': 420}, 'tinytest/test_blake3.R': {'bytes': 1560, 'sha256': '8132d0e368dbb84d50327c9ccf5914629e6b24ea48e4563560d64f4431c1de24', 'uid': 0, 'mode': 420}, 'tinytest/test_crc32.R': {'bytes': 549, 'sha256': '208aa7505793b4c71a8f5747eb8ff7f9cb1b55d772eba99138e6890029cc69d9', 'uid': 0, 'mode': 420}, 'tinytest/test_digest.R': {'bytes': 13081, 'sha256': '3f9e71be0e40f1908b5960dd65eb8a73942832c2fce2b24d8b0022b7cf0b32cf', 'uid': 0, 'mode': 420}, 'tinytest/test_digest2int.R': {'bytes': 535, 'sha256': 'd262f81a71352d07ba1977731e65f57135f0fa320be0a0a71f38fbd10b1b44c6', 'uid': 0, 'mode': 420}, 'tinytest/test_encoding.R': {'bytes': 745, 'sha256': 'f945582de7fe55d072dca4c0aced2fd87d3c99a648cb809e48e715e170e4ffd9', 'uid': 0, 'mode': 420}, 'tinytest/test_hmac.R': {'bytes': 3653, 'sha256': '4e2d7a087630a8df9688a25c300f67954636716e4b16147bbf82eec0576b1fbb', 'uid': 0, 'mode': 420}, 'tinytest/test_misc.R': {'bytes': 2185, 'sha256': '92236c0c354ef02909779a830624959090aed72faadf6d789d0553fff7020e30', 'uid': 0, 'mode': 420}, 'tinytest/test_new_matrix_behaviour.R': {'bytes': 3549, 'sha256': '8413c0d2644ae3b811e68d8e9146e556818708a2bbc5dd58f6bba683030d8836', 'uid': 0, 'mode': 420}, 'tinytest/test_num2hex.R': {'bytes': 1664, 'sha256': 'ca26fd438192a91d498260e7d46f1e6aeca3cce3ad3affe0fd5d1dff60a7d70b', 'uid': 0, 'mode': 420}, 'tinytest/test_raw.R': {'bytes': 1172, 'sha256': '88a240802be0d64ff4cf7f93d0d6618ab88c75a770360329e0362f3acb186ccc', 'uid': 0, 'mode': 420}, 'tinytest/test_sha1.R': {'bytes': 14352, 'sha256': '88405439d0dc6325dc630fe8d5a7aeeb2cb1b80ae388f94bd5e0c9eb37aae460', 'uid': 0, 'mode': 420}}, 'directories': {'Meta': {'uid': 0, 'mode': 493}, 'R': {'uid': 0, 'mode': 493}, 'demo': {'uid': 0, 'mode': 493}, 'doc': {'uid': 0, 'mode': 493}, 'help': {'uid': 0, 'mode': 493}, 'html': {'uid': 0, 'mode': 493}, 'include': {'uid': 0, 'mode': 493}, 'libs': {'uid': 0, 'mode': 493}, 'tinytest': {'uid': 0, 'mode': 493}}}

def validate_digest_inventory(root,observed):
    # Fixed trusted server dependency record, never a wire/configurable library.
    from .preprocess_contract import canonical
    if (type(root) is not str or root!=DEPENDENCIES['digest_library_root']
        or type(observed) is not dict or observed!=DIGEST_INVENTORY
        or hashlib.sha256(canonical(observed)).hexdigest()!=DEPENDENCIES['digest_inventory_sha256']):
        raise ValueError('quantile_digest_dependency_changed')
    return observed
