"""Pure heatmap bytes/identity/display checks. No R, IO, fitting or authority."""
from collections import Counter
from dataclasses import dataclass
from uuid import UUID
import hashlib,json,math,re,struct,zlib
import xml.etree.ElementTree as ET

CONTRACT='r_heatmap_output_v0.1'
FILE_CAP=27262976
TOTAL_CAP=134217728
SAMPLE_KEYS=tuple('s'+str(i) for i in range(1,7))
MATRIX_HEADER=('source_row_ordinal','protein_ids_raw',*SAMPLE_KEYS)
SAMPLES_HEADER=('sample_key','source_column_ordinal','sample_name','group_key','group_label','replicate_key','replicate_type')
SELECTED_HEADER=tuple('source_row_ordinal protein_ids_raw contrast_id test_status reason log2FC FC FC_reason AveExpr moderated_t df_residual df_total P.Value adj.P.Val n_obs_num n_obs_den n_fit_num n_fit_den classification selected_DE'.split())
ROW_MAP_HEADER=tuple('source_row_ordinal protein_ids_raw classification observed_n row_mean row_sd row_scaling_status'.split())
INPUT_NAMES=('matrix.tsv','samples.tsv','selected-DE.tsv')
OUTPUTS=('R-session.txt','heatmap-audit.tsv','heatmap-matrix.tsv','heatmap-row-map.tsv','heatmap-samples.tsv','heatmap-zscore.tsv','heatmap.png','heatmap.svg')
IMAGE_DOWNLOADS=('heatmap.svg',)
INTERNAL_PREVIEWS=('heatmap.png',)
STATUSES=('scaled','zero_sd','all_missing','insufficient_observations')
AUDIT_KEYS=tuple('schema_version output_status input_scale display_scale center scale normalization imputation pseudocount cluster_rows cluster_cols missing_policy invalid_scaling_policy row_order sample_order input_rows selected_rows samples observed_cells missing_cells plotted_observed_cells plotted_missing_cells scaled_rows zero_sd_rows all_missing_rows insufficient_observations_rows color_limit seed R_version pheatmap_version SVG_width_inches SVG_height_inches PNG_width_pixels PNG_height_pixels matrix_bytes matrix_sha256 selected_DE_bytes selected_DE_sha256 samples_bytes samples_sha256 provenance_bytes provenance_sha256'.split())
PROVENANCE_KEYS=frozenset(('schemaVersion','contract','project_id','created_by','source_result_id','source_metadata_sha256','source_manifest_sha256','derived_version_id','derived_metadata_sha256','derived_manifest_sha256','source_binding_sha256','parameters_sha256','inputs'))
FIXED=dict(schema_version=CONTRACT,output_status='HEATMAP_RENDERED',input_scale='log2',display_scale='row_zscore',center='observed_row_mean',scale='observed_sample_sd',normalization='none',imputation='none',pseudocount='0',cluster_rows='FALSE',cluster_cols='FALSE',missing_policy='preserve_NA_grey',invalid_scaling_policy='retain_row_display_NA_grey',row_order='source_row_ordinal_ascending',sample_order='input_header',seed='not_used_no_clustering',SVG_width_inches='16',SVG_height_inches='9',PNG_width_pixels='2400',PNG_height_pixels='1350')
NUMBER=re.compile(r'^[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)([eE][+-]?[0-9]+)?$')

class InvalidHeatmap(ValueError):
    def __init__(self,code='invalid_heatmap'):self.code=code;super().__init__(code)
def need(ok,code='invalid_heatmap'):
    if not ok:raise InvalidHeatmap(code)
def sha(raw):return hashlib.sha256(raw).hexdigest()
def text(raw,cap=FILE_CAP,newline=True):
    need(type(raw) is bytes and 0<len(raw)<=cap and (not newline or raw.endswith(b'\n')) and not raw.startswith(b'\xef\xbb\xbf') and b'\r' not in raw and b'\0' not in raw)
    try:return raw.decode('utf8')
    except UnicodeError:raise InvalidHeatmap() from None
def table(raw,header,max_rows=20000):
    rows=tuple(tuple(l.split('\t')) for l in text(raw).split('\n')[:-1])
    need(rows and rows[0]==tuple(header) and len(rows)-1<=max_rows and all(len(r)==len(header) for r in rows));return rows[1:]
def number(token):
    if token=='NA':return None
    need(NUMBER.fullmatch(token) is not None)
    try:n=float(token)
    except ValueError:raise InvalidHeatmap() from None
    need(math.isfinite(n));return n
def integer(token):
    need(re.fullmatch(r'[1-9][0-9]*',token) is not None);n=int(token);need(n<=9007199254740991);return n
def label(token):need(bool(token) and all(ord(c)>=32 and ord(c)!=127 for c in token));return token
def json_document(raw):
    def pairs(items):
        out={}
        for k,v in items:need(k not in out);out[k]=v
        return out
    def invalid(_):raise InvalidHeatmap()
    try:return json.loads(text(raw,65536,False),object_pairs_hook=pairs,parse_constant=invalid)
    except (ValueError,RecursionError):raise InvalidHeatmap() from None

@dataclass(frozen=True,slots=True)
class Inputs:
    matrix:bytes
    selected:bytes
    samples:bytes
    provenance:bytes
    rows:tuple
    selected_rows:tuple
    sample_rows:tuple
    chosen:tuple

def validate_inputs(matrix,selected,samples,provenance):
    rows=table(matrix,MATRIX_HEADER);chosen=table(selected,SELECTED_HEADER,200);sample_rows=table(samples,SAMPLES_HEADER,64)
    need(0<len(rows)<=20000 and 0<len(chosen)<=200 and len(sample_rows)==6)
    ordinals=[integer(r[0]) for r in rows];need(ordinals==sorted(set(ordinals)))
    for row in rows:label(row[1]);[number(v) for v in row[2:]]
    selected_ordinals=[integer(r[0]) for r in chosen];need(selected_ordinals==sorted(set(selected_ordinals)))
    identities={(r[0],r[1]):r for r in rows}
    for row in chosen:
        need((row[0],row[1]) in identities and row[18] in ('Up','Down') and row[19]=='TRUE' and row[3]=='testable');label(row[2])
    need(len({r[2] for r in chosen})==1)
    subset=tuple(identities[(r[0],r[1])] for r in chosen)
    chosen_ordinals={r[0] for r in chosen}
    need(tuple(r[0] for r in subset)==tuple(r[0] for r in rows if r[0] in chosen_ordinals))
    need(tuple(r[0] for r in sample_rows)==SAMPLE_KEYS)
    need(len({integer(r[1]) for r in sample_rows})==6)
    groups={};replicates=set()
    for r in sample_rows:
        for token in r[2:]:label(token)
        need(r[6]=='biological' and groups.get(r[3],r[4])==r[4] and (r[3],r[5]) not in replicates)
        groups[r[3]]=r[4];replicates.add((r[3],r[5]))
    need(len(groups)==2 and len(set(groups.values()))==2)
    p=json_document(provenance);need(type(p) is dict and set(p)==PROVENANCE_KEYS and type(p['schemaVersion']) is int and p['schemaVersion']==1 and p['contract']=='r_heatmap_provenance_v0.1')
    need(json.dumps(p,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf8')==provenance)
    for k in ('project_id','created_by','source_result_id','derived_version_id'):
        try:need(type(p[k]) is str and str(UUID(p[k]))==p[k])
        except ValueError:raise InvalidHeatmap() from None
    for k in PROVENANCE_KEYS:
        if k.endswith('_sha256'):need(type(p[k]) is str and re.fullmatch('[0-9a-f]{64}',p[k]) is not None)
    need(type(p['inputs']) is list and p['inputs']==[{'name':n,'bytes':len(b),'sha256':sha(b)} for n,b in zip(INPUT_NAMES,(matrix,samples,selected))])
    for pin in p['inputs']:need(type(pin) is dict and set(pin)=={'name','bytes','sha256'} and type(pin['bytes']) is int)
    return Inputs(matrix,selected,samples,provenance,rows,chosen,sample_rows,subset)

def expected_rows(value):
    need(type(value) is Inputs);out=[]
    for raw,selected in zip(value.chosen,value.selected_rows):
        xs=[number(v) for v in raw[2:]];observed=[x for x in xs if x is not None];n=len(observed)
        try:
            mean=math.fsum(observed)/n if n else None
            sd=math.sqrt(math.fsum((x-mean)**2 for x in observed)/(n-1)) if n>=2 else None
        except (ArithmeticError,ValueError):raise InvalidHeatmap() from None
        need(mean is None or math.isfinite(mean));need(sd is None or math.isfinite(sd))
        status='all_missing' if n==0 else 'insufficient_observations' if n==1 else 'zero_sd' if sd==0 else 'scaled'
        zs=tuple((x-mean)/sd if x is not None and status=='scaled' else None for x in xs)
        need(all(z is None or math.isfinite(z) for z in zs));out.append((n,mean,sd,status,zs))
    return tuple(out)

def numeric_equal(token,want):
    got=number(token);need(got is None if want is None else got is not None and math.isclose(got,want,rel_tol=1e-12,abs_tol=1e-12))
def versions(session):
    lines=text(session).splitlines();need(len(lines)>3);out=[]
    for line,name in zip(lines,('R_VERSION','PHEATMAP_VERSION','DIGEST_VERSION')):
        pair=line.split('\t');need(len(pair)==2 and pair[0]==name and pair[1]);out.append(pair[1])
    need(out[0].startswith('R version ') and out[1]=='1.0.13' and re.fullmatch(r'[0-9]+(\.[0-9]+)*',out[2]) is not None)
    tail='\n'.join(lines[3:]);need(out[0] in tail and 'pheatmap_'+out[1] in tail and 'digest_'+out[2] in tail);return out

def validate_tables(value,outputs):
    expected=expected_rows(value);maps=table(outputs['heatmap-row-map.tsv'],ROW_MAP_HEADER,200);zs=table(outputs['heatmap-zscore.tsv'],MATRIX_HEADER,200)
    need(outputs['heatmap-samples.tsv']==value.samples)
    want_matrix=('\t'.join(MATRIX_HEADER)+'\n'+''.join('\t'.join(r)+'\n' for r in value.chosen)).encode('utf8')
    need(outputs['heatmap-matrix.tsv']==want_matrix and len(maps)==len(zs)==len(expected))
    for raw,selected,row,z,e in zip(value.chosen,value.selected_rows,maps,zs,expected):
        need(row[:3]==(raw[0],raw[1],selected[18]) and row[3]==str(e[0]) and row[6]==e[3] and z[:2]==raw[:2])
        numeric_equal(row[4],e[1]);numeric_equal(row[5],e[2])
        for token,want in zip(z[2:],e[4]):numeric_equal(token,want)
    audit=table(outputs['heatmap-audit.tsv'],('key','value'),len(AUDIT_KEYS));need(tuple(r[0] for r in audit)==AUDIT_KEYS);audit=dict(audit)
    observed=sum(v!='NA' for r in value.rows for v in r[2:]);plot_observed=sum(z is not None for e in expected for z in e[4]);counts=Counter(e[3] for e in expected)
    version=versions(outputs['R-session.txt']);want=dict(FIXED,input_rows=str(len(value.rows)),selected_rows=str(len(expected)),samples='6',observed_cells=str(observed),missing_cells=str(len(value.rows)*6-observed),plotted_observed_cells=str(plot_observed),plotted_missing_cells=str(len(expected)*6-plot_observed),R_version=version[0],pheatmap_version=version[1])
    for status in STATUSES:want[status+'_rows']=str(counts[status])
    for prefix,body in (('matrix',value.matrix),('selected_DE',value.selected),('samples',value.samples),('provenance',value.provenance)):
        want[prefix+'_bytes']=str(len(body));want[prefix+'_sha256']=sha(body)
    need(set(want)|{'color_limit'}==set(AUDIT_KEYS));need(all(audit[k]==v for k,v in want.items()))
    limit=max([1]+[abs(z) for e in expected for z in e[4] if z is not None]);numeric_equal(audit['color_limit'],limit)
    return dict(contract=CONTRACT,input_rows=len(value.rows),selected_rows=len(expected),samples=6,status_counts=dict(counts),color_limit=limit)

def validate_outputs(value,outputs):
    need(type(outputs) is dict and set(outputs)==set(OUTPUTS))
    need(all(type(b) is bytes and 0<len(b)<=FILE_CAP for b in outputs.values()) and sum(map(len,outputs.values()))<=TOTAL_CAP)
    summary=validate_tables(value,outputs);_png(outputs['heatmap.png']);_svg(outputs['heatmap.svg']);return summary

def _png(body):
    need(body.startswith(b'\x89PNG\r\n\x1a\n'),'invalid_heatmap_png');pos=8;seen=[];idat=0;physical=None
    while pos<len(body):
        need(pos+12<=len(body),'invalid_heatmap_png');size=struct.unpack('>I',body[pos:pos+4])[0]
        end=pos+12+size;need(end<=len(body),'invalid_heatmap_png');kind=body[pos+4:pos+8];data=body[pos+8:pos+8+size]
        need((zlib.crc32(kind+data)&0xffffffff)==struct.unpack('>I',body[end-4:end])[0],'invalid_heatmap_png')
        if not seen:need(kind==b'IHDR' and size==13 and struct.unpack('>II',data[:8])==(2400,1350),'invalid_heatmap_png')
        if kind==b'IHDR':need(not seen,'invalid_heatmap_png')
        if kind==b'pHYs':need(physical is None and size==9,'invalid_heatmap_png');physical=struct.unpack('>IIB',data)
        if kind==b'IDAT':idat+=size
        seen.append(kind);pos=end
        if kind==b'IEND':need(size==0 and pos==len(body),'invalid_heatmap_png');break
    need(seen and seen[-1]==b'IEND' and idat>0,'invalid_heatmap_png')

def _svg(body):
    need(b'<!DOCTYPE' not in body and b'<!ENTITY' not in body and b'\0' not in body,'invalid_heatmap_svg')
    try:root=ET.fromstring(body.decode('utf8'))
    except (UnicodeError,ET.ParseError):raise InvalidHeatmap('invalid_heatmap_svg') from None
    need(root.tag=='{http://www.w3.org/2000/svg}svg','invalid_heatmap_svg')
    need(root.get('width') in ('1152pt','16in') and root.get('height') in ('648pt','9in'),'invalid_heatmap_svg')
    try:need(tuple(map(float,root.get('viewBox','').split()))==(0,0,1152,648),'invalid_heatmap_svg')
    except ValueError:raise InvalidHeatmap('invalid_heatmap_svg') from None
    for node in root.iter():
        tag=node.tag.rsplit('}',1)[-1]
        need(node.tag.startswith('{http://www.w3.org/2000/svg}') and tag in
            ('svg','g','defs','path','rect','circle','ellipse','line','polyline','polygon','text','tspan','symbol','use','clipPath','title','desc'),'invalid_heatmap_svg')
        for key,value in node.attrib.items():
            attr=key.rsplit('}',1)[-1]
            need(not attr.lower().startswith('on') and (attr!='href' or value.startswith('#')),'invalid_heatmap_svg')
            urls=re.findall(r'url\(([^)]*)\)',value,re.I)
            need(all(re.fullmatch(r'#[A-Za-z0-9_.:-]+',u) is not None for u in urls),'invalid_heatmap_svg')

