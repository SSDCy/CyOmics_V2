"""Pure local-first planning. Trusted metadata consistency is not authorization.

No filesystem, clock, network, process, URL, secret or resource-probing interface.
Only a future authorized Worker may supply server records and enact these plans.
"""
from dataclasses import dataclass, asdict
from datetime import datetime
import hashlib
import json
import re
from uuid import UUID

POLICY = 'resource_provider_local_first_v1'
CAPABILITIES = frozenset(('blast.protein_homology', 'pfam.domains', 'smart.domains', 'cdd.domains',
    'eggnog.annotation', 'go.enrichment', 'kegg.enrichment', 'string.network', 'string.id_mapping',
    'alphafold.sequence_search', 'alphafold.metadata', 'pdb.sequence_search', 'pdb.metadata',
    'subcellular_localization.prediction'))
CHECKS = ('registered_resource', 'tool_available', 'index_compatible', 'capability_coverage', 'identifier_compatible')
LOCAL_STATES = frozenset(('usable', 'missing', 'incompatible', 'unknown', 'unverified', 'error'))
PROVIDERS = frozenset(('local', 'official_api', 'needs_verification', 'unavailable', 'error'))

def text(value, maximum=200):
    if not isinstance(value,str) or not 1 <= len(value) <= maximum or any(ord(c)<32 or 127<=ord(c)<=159 or 0xd800<=ord(c)<=0xdfff for c in value):
        raise ValueError('invalid text')
    return value

def key(value):
    if not isinstance(value,str) or not re.fullmatch(r'[a-z][a-z0-9_.-]{0,79}',value):raise ValueError('invalid registered key')
    return value

def uuid(value):
    if not isinstance(value,str) or str(UUID(value))!=value:raise ValueError('canonical UUID required')
    return value

def digest(value):
    if not isinstance(value,str) or not re.fullmatch('[0-9a-f]{64}',value):raise ValueError('invalid SHA256')
    return value

def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def sha(value):return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()

@dataclass(frozen=True)
class InputIdentity:
    version: str
    original_ids: tuple
    sequence_sha256: tuple = ()  # ((original_id, sha256), ...); residues are never accepted.
    taxonomy_id: int | None = None
    identifier_namespace: str = 'native_protein'

@dataclass(frozen=True)
class ServerProjectContext:
    project_id: str
    external_allowed: bool | None
    timestamp_utc: str
    # Contains no actor/owner flag. Authorization must occur outside this module.

@dataclass(frozen=True)
class LocalResource:
    capability: str
    resource_key: str
    state: str
    evidence_key: str
    checks: tuple = ()  # exact five bool/None checks for a declared usable resource
    species_scope: str = 'unknown'  # any/exact/unknown; STRING requires exact.
    taxonomy_id: int | None = None
    mapping_method: str | None = None
    database_name: str = 'unknown'
    database_version: str | None = None
    version_status: str = 'unverified'
    fingerprints: tuple = ()
    engine: str = 'unknown'
    engine_version: str | None = None
    parameters_json: str = '{}'

@dataclass(frozen=True)
class OfficialCapability:
    capability: str
    service_key: str
    evidence_key: str
    identity_verified: bool | None
    capability_documented: bool | None
    parameters_verified: bool | None
    payload_scope: str  # selected_ids / selected_sequences only
    service_version: str | None = None
    timeout_seconds: int = 10
    parameters_json: str = '{}'

@dataclass(frozen=True)
class Selection:
    capability: str
    provider: str
    reason: str
    resource_key: str | None
    provenance_json: str

@dataclass(frozen=True)
class JobPlan:
    job_id: str
    project_id: str
    input: InputIdentity
    selections: tuple
    plan_sha256: str
    authorization_verified: bool = False
    execution_enabled: bool = False
    external_calls: int = 0

@dataclass(frozen=True)
class ResultObservation:
    job_id: str
    capability: str
    provider: str
    result_state: str
    plan_sha256: str
    external_job_id: str | None
    provider_changed: bool = False
    external_calls: int = 0

def parameters(raw):
    if not isinstance(raw,str):raise ValueError('parameters must be canonical JSON object')
    if len(raw.encode())>8192:raise ValueError('parameters too large')
    value=json.loads(raw,parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
    if not isinstance(value,dict) or canonical(value)!=raw:raise ValueError('canonical parameters object required')
    # Parameters are provenance, never a command/path/URL carrier.
    def inspect(v,depth=0):
        if depth>8:raise ValueError('parameters too deeply nested')
        if isinstance(v,dict):
            for k,x in v.items():
                key(k)
                if k.lower() in ('path','command','argv','url','endpoint','binary','owner','authorization','token','password','secret'):raise ValueError('unsafe parameter field')
                inspect(x,depth+1)
        elif isinstance(v,list):
            if len(v)>100:raise ValueError('parameter list too large')
            for x in v:inspect(x,depth+1)
        elif isinstance(v,str):
            text(v)
            if '/' in v or '\\' in v or ';' in v or '$(' in v:raise ValueError('unsafe parameter value')
        elif v is not None and type(v) not in (bool,int,float):raise ValueError('invalid parameter value')
    inspect(value)
    return value

def capability(value):
    if value not in CAPABILITIES:raise ValueError('unsupported capability')
    return value

def validate_input(value):
    if not isinstance(value,InputIdentity):raise ValueError('InputIdentity required')
    uuid(value.version);key(value.identifier_namespace)
    if type(value.original_ids) is not tuple or not 1<=len(value.original_ids)<=1000:raise ValueError('selected IDs required')
    for identifier in value.original_ids:text(identifier)
    if len(set(value.original_ids))!=len(value.original_ids):raise ValueError('duplicate ID')
    if type(value.sequence_sha256) is not tuple:raise ValueError('immutable sequence bindings required')
    seen=set()
    for pair in value.sequence_sha256:
        if type(pair) is not tuple or len(pair)!=2 or pair[0] not in value.original_ids or pair[0] in seen:raise ValueError('invalid ID/hash binding')
        digest(pair[1]);seen.add(pair[0])
    if value.taxonomy_id is not None and (type(value.taxonomy_id) is not int or value.taxonomy_id<=0):raise ValueError('invalid taxonomy')

def local_record(value):
    if not isinstance(value,LocalResource):raise ValueError('server local record required')
    capability(value.capability);key(value.resource_key);key(value.evidence_key)
    if value.state not in LOCAL_STATES:raise ValueError('invalid local state')
    if value.species_scope not in ('any','exact','unknown'):raise ValueError('invalid species scope')
    if value.taxonomy_id is not None and (type(value.taxonomy_id) is not int or value.taxonomy_id<=0):raise ValueError('invalid local taxonomy')
    if type(value.checks) is not tuple or len(value.checks) not in (0,len(CHECKS)) or any(type(x) is not bool and x is not None for x in value.checks):raise ValueError('invalid local checks')
    if value.version_status not in ('verified','unverified') or value.version_status=='verified' and value.database_version is None:raise ValueError('invalid version status')
    for v in (value.database_name,value.engine):text(v)
    for v in (value.database_version,value.engine_version,value.mapping_method):
        if v is not None:text(v)
    if type(value.fingerprints) is not tuple:raise ValueError('immutable fingerprints required')
    seen=set()
    for pair in value.fingerprints:
        if type(pair) is not tuple or len(pair)!=2 or pair[0] in seen:raise ValueError('invalid fingerprint')
        key(pair[0]);digest(pair[1]);seen.add(pair[0])
    parameters(value.parameters_json)

def official_record(value):
    if not isinstance(value,OfficialCapability):raise ValueError('server official record required')
    capability(value.capability);key(value.service_key);key(value.evidence_key)
    for v in (value.identity_verified,value.capability_documented,value.parameters_verified):
        if type(v) is not bool and v is not None:raise ValueError('invalid verification state')
    if value.payload_scope not in ('selected_ids','selected_sequences'):raise ValueError('whole-project/unsupported payload forbidden')
    if type(value.timeout_seconds) is not int or not 1<=value.timeout_seconds<=60:raise ValueError('bounded timeout required')
    if value.service_version is not None:text(value.service_version)
    parameters(value.parameters_json)

def select_job(request,input_identity,server_context,local_resources=(),official_services=()):
    """Server-only metadata arguments are never hydrated from the client request."""
    if type(request) is not dict or set(request)!= {'job_id','capabilities'}:raise ValueError('only job_id and capabilities accepted from client')
    uuid(request['job_id']);validate_input(input_identity)
    if not isinstance(server_context,ServerProjectContext):raise ValueError('server context required')
    uuid(server_context.project_id)
    if server_context.external_allowed is not None and type(server_context.external_allowed) is not bool:raise ValueError('invalid project external policy')
    stamp=server_context.timestamp_utc
    if not isinstance(stamp,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z',stamp):raise ValueError('explicit UTC timestamp required')
    datetime.fromisoformat(stamp[:-1]+'+00:00')
    requested=request['capabilities']
    if type(requested) is not tuple or not 1<=len(requested)<=len(CAPABILITIES) or len(set(requested))!=len(requested):raise ValueError('unique immutable capabilities required')
    for c in requested:capability(c)
    if type(local_resources) is not tuple or type(official_services) is not tuple:raise ValueError('immutable server registration required')
    local={};official={}
    for record in local_resources:
        local_record(record)
        if record.capability in local:raise ValueError('ambiguous local capability registration')
        local[record.capability]=record
    for record in official_services:
        official_record(record)
        if record.capability in official:raise ValueError('ambiguous official capability registration')
        official[record.capability]=record
    selected=[]
    for c in requested:
        resource=local.get(c);service=official.get(c)
        state=resource.state if resource else 'unverified'
        reason='local_'+state
        if state=='usable':
            if not resource.checks or any(x is None for x in resource.checks):state='unverified';reason='local_checks_pending'
            elif not all(resource.checks):state='incompatible';reason='local_checks_incompatible'
            elif resource.species_scope=='unknown' or resource.species_scope=='exact' and input_identity.taxonomy_id is None:
                state='unverified';reason='species_pending'
            elif resource.species_scope=='exact' and resource.taxonomy_id is None:
                state='unverified';reason='registered_species_pending'
            elif resource.species_scope=='exact' and resource.taxonomy_id!=input_identity.taxonomy_id:
                state='incompatible';reason='species_mismatch'
            elif c.startswith('string.') and resource.species_scope!='exact':
                state='incompatible';reason='string_exact_species_required'
            elif c.startswith('string.') and resource.mapping_method is None:
                state='unverified';reason='mapping_method_pending'
        provider='local' if state=='usable' else 'needs_verification' if state in ('unverified','unknown') else 'error' if state=='error' else 'unavailable'
        if state in ('missing','incompatible'):
            if server_context.external_allowed is None:provider='needs_verification';reason+=':project_external_choice_pending'
            elif server_context.external_allowed is False:reason+=':project_external_disabled'
            elif service is None:reason+=':no_registered_official_capability'
            elif any(v is not True for v in (service.identity_verified,service.capability_documented,service.parameters_verified)):
                provider='needs_verification';reason+=':official_capability_unverified'
            elif c.startswith('string.') and (input_identity.taxonomy_id is None or 'species' not in parameters(service.parameters_json)):
                provider='needs_verification';reason+=':official_species_binding_pending'
            elif c.startswith('string.') and (type(parameters(service.parameters_json)['species']) is not int or parameters(service.parameters_json)['species']!=input_identity.taxonomy_id):
                provider='error';reason+=':official_species_mismatch'
            elif service.payload_scope=='selected_sequences' and len(input_identity.sequence_sha256)!=len(input_identity.original_ids):
                provider='error';reason+=':selected_sequence_hashes_required'
            else:provider='official_api';reason+=':verified_official_fallback'
        provenance={'policy':POLICY,'capability':c,'input':asdict(input_identity),'project_id':server_context.project_id,
            'provider':provider,'selection_reason':reason,'timestamp':stamp,'result_state':'not_run',
            'local_registration':asdict(resource) if resource else None,
            'official_registration':asdict(service) if provider=='official_api' else None,
            'external_payload_scope':service.payload_scope if provider=='official_api' else 'none',
            'external_job_id':None,'transport_invoked':False,'authorization_verified':False,'parameters_fixed_per_job':True}
        selected.append(Selection(c,provider,reason,resource.resource_key if provider=='local' else service.service_key if provider=='official_api' else None,canonical(provenance)))
    content={'job_id':request['job_id'],'project_id':server_context.project_id,'input':asdict(input_identity),'selections':[asdict(s) for s in selected]}
    return JobPlan(request['job_id'],server_context.project_id,input_identity,tuple(selected),sha(content))

def validate_locked_plan(plan):
    if not isinstance(plan,JobPlan) or plan.execution_enabled is not False or plan.authorization_verified is not False or type(plan.external_calls) is not int or plan.external_calls!=0:raise ValueError('invalid nonexecuting plan')
    uuid(plan.job_id);uuid(plan.project_id);validate_input(plan.input)
    if type(plan.selections) is not tuple or not plan.selections:raise ValueError('immutable selections required')
    for s in plan.selections:
        if not isinstance(s,Selection) or s.provider not in PROVIDERS:raise ValueError('invalid selection')
        capability(s.capability)
        if canonical(json.loads(s.provenance_json))!=s.provenance_json:raise ValueError('invalid provenance')
    content={'job_id':plan.job_id,'project_id':plan.project_id,'input':asdict(plan.input),'selections':[asdict(s) for s in plan.selections]}
    if sha(content)!=plan.plan_sha256:raise ValueError('locked provider/provenance changed')

def observe_result(plan,*,job_id,capability_key,provider,result_state,external_job_id=None):
    """An observation never calls select_job, retries, changes providers or sends data."""
    validate_locked_plan(plan)
    if job_id!=plan.job_id:raise ValueError('job binding mismatch')
    matches=[s for s in plan.selections if s.capability==capability_key]
    if len(matches)!=1 or matches[0].provider!=provider or provider not in ('local','official_api'):raise ValueError('provider binding mismatch')
    if result_state not in ('hit','no_hit','error','timeout','rate_limited','cancelled'):raise ValueError('invalid result state')
    if external_job_id is not None:
        if provider!='official_api':raise ValueError('local result has no external job')
        text(external_job_id)
    return ResultObservation(plan.job_id,capability_key,provider,result_state,plan.plan_sha256,external_job_id)

def domain_runner_resource_key(plan,capability_key):
    """Only existing reviewed domain registry keys; no path/command construction."""
    validate_locked_plan(plan)
    fixed={'blast.protein_homology':'swissprot-reviewed','pfam.domains':'pfam-raw-reviewed'}
    selected=[s for s in plan.selections if s.capability==capability_key]
    if capability_key not in fixed or len(selected)!=1 or selected[0].provider!='local' or selected[0].resource_key!=fixed[capability_key]:raise ValueError('no reviewed local domain runner binding')
    return fixed[capability_key]
