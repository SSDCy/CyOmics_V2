"""Pure byte contracts only. Never executes R, writes artifacts or grants authority.

Coordinates are checked against the frozen display transform, not used to render
or recompute statistical membership. Real rendering remains ggplot2's task.
"""
from collections import Counter
from dataclasses import dataclass
import hashlib,math,re,struct,zlib
import xml.etree.ElementTree as ET

CONTRACT='r_volcano_v0.1'
CLASSIFICATION_HEADER=('source_row_ordinal', 'protein_ids_raw', 'contrast_id', 'test_status', 'reason', 'log2FC', 'FC', 'FC_reason', 'AveExpr', 'moderated_t', 'df_residual', 'df_total', 'P.Value', 'adj.P.Val', 'n_obs_num', 'n_obs_den', 'n_fit_num', 'n_fit_den', 'classification', 'selected_DE')
SOURCE_AUDIT_KEYS=('schema_version', 'classification_status', 'DE_members_evaluated', 'source_contract', 'source_output_status', 'source_warning_count', 'source_contrast_id', 'source_numerator', 'source_denominator', 'contrast_direction', 'threshold_scale', 'adjusted_p_max', 'absolute_log2fc_min', 'boundary_rule', 'zero_log2FC', 'input_rows', 'source_testable_rows', 'source_not_testable_rows', 'Up', 'Down', 'NS', 'not_testable', 'not_evaluated', 'selected_DE_rows', 'source_bh_method', 'source_bh_m', 'source_bh_n', 'source_bh_scope', 'FC_column', 'statistical_recomputation', 'row_identity', 'row_order', 'specific_detection')
AUDIT_KEYS=('contract', 'plot_status', 'input_classification_sha256', 'input_parameters_sha256', 'input_audit_sha256', 'source_classification_status', 'source_contrast_id', 'source_numerator', 'source_denominator', 'R_version', 'ggplot2_version', 'digest_version', 'x_source', 'axis', 'y_transform', 'adjusted_p_max', 'absolute_log2fc_min', 'boundary_rule', 'threshold_x_negative', 'threshold_x_positive', 'threshold_y_true', 'threshold_y_display', 'threshold_y_floored', 'q_display_floor', 'q_zero_floor_rows', 'q_small_floor_rows', 'input_rows', 'plotted_rows', 'excluded_rows', 'input_Up', 'input_Down', 'input_NS', 'input_not_testable', 'input_not_evaluated', 'plotted_Up', 'plotted_Down', 'plotted_NS', 'plotted_not_testable', 'plotted_not_evaluated', 'excluded_Up', 'excluded_Down', 'excluded_NS', 'excluded_not_testable', 'excluded_not_evaluated', 'reason_plotted', 'reason_plotted_q_floor', 'reason_not_testable', 'reason_not_evaluated', 'reason_non_finite_coordinate', 'statistical_recomputation', 'row_identity', 'row_order', 'png_width_px', 'png_height_px', 'png_dpi', 'svg_width_in', 'svg_height_in', 'rendering_package', 'y_axis_upper', 'image_download_format', 'internal_preview_format')
POINTS_HEADER=CLASSIFICATION_HEADER+('plot_x','plot_y','plotted','plot_reason')
CLASSES=('Up','Down','NS','not_testable','not_evaluated')
REASONS=('plotted','plotted_q_floor','not_testable','not_evaluated','non_finite_coordinate')
OUTPUTS=('R-session.txt','volcano-audit.tsv','volcano-points.tsv','volcano.png','volcano.svg')
IMAGE_DOWNLOADS=('volcano.svg',)
INTERNAL_PREVIEWS=('volcano.png',)
PARAMETER_KEYS=('schema_version','input_scale','adjusted_p_max','absolute_log2fc_min')
BOUNDARY='adj.P.Val_le_max_and_abs_log2FC_ge_min_and_strict_direction_sign'
FLOOR=1e-300
ROW_CAP=10000
FILE_CAP=32*1024*1024
SMALL_CAP=65536
TOTAL_CAP=128*1024*1024
_NUMBER=re.compile(r'^[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)([eE][+-]?[0-9]+)?$')
_NONFINITE={'NA':math.nan,'NaN':math.nan,'Inf':math.inf,'+Inf':math.inf,'-Inf':-math.inf}

class InvalidVolcano(ValueError):
    """Fixed code only; do not embed original input/paths in errors."""
    def __init__(self,code='invalid_volcano'):self.code=code;super().__init__(code)
def need(value,code='invalid_volcano'):
    if not value:raise InvalidVolcano(code)
def sha(body):return hashlib.sha256(body).hexdigest()
def _text(body,cap):
    need(type(body) is bytes and 0<len(body)<=cap and body.endswith(b'\n') and not body.startswith(b'\xef\xbb\xbf')
        and b'\r' not in body and b'\0' not in body,'invalid_volcano_text')
    try:return body.decode('utf8')
    except UnicodeError:raise InvalidVolcano('invalid_volcano_text') from None
def table(body,header,cap=FILE_CAP,max_rows=ROW_CAP):
    lines=_text(body,cap).split('\n')[:-1];rows=tuple(tuple(line.split('\t')) for line in lines)
    need(rows and rows[0]==tuple(header) and len(rows)-1<=max_rows and all(len(row)==len(header) for row in rows),'invalid_volcano_table')
    return rows[1:]
def _kv(body,keys,cap):
    rows=table(body,('key','value'),cap,len(keys));need(tuple(k for k,v in rows)==tuple(keys),'invalid_volcano_keys')
    return dict(rows)
def _number(token):
    if token in _NONFINITE:return _NONFINITE[token]
    need(_NUMBER.fullmatch(token) is not None,'invalid_volcano_number')
    try:return float(token)
    except ValueError:raise InvalidVolcano('invalid_volcano_number') from None
def _integer(token):
    need(re.fullmatch(r'0|[1-9][0-9]*',token) is not None,'invalid_volcano_count');return int(token)

@dataclass(frozen=True,slots=True)
class Inputs:
    classification:bytes
    parameter_bytes:bytes
    source_audit_bytes:bytes
    rows:tuple
    parameters:tuple
    source_audit:tuple

def validate_inputs(classification,parameters,audit):
    rows=table(classification,CLASSIFICATION_HEADER,26*1024*1024)
    p=_kv(parameters,PARAMETER_KEYS,4096);a=_kv(audit,SOURCE_AUDIT_KEYS,32768)
    q,f=_number(p['adjusted_p_max']),_number(p['absolute_log2fc_min'])
    need(p['schema_version']=='de_classification_v0.1' and p['input_scale']=='log2'
        and math.isfinite(q) and 0<q<=1 and math.isfinite(f) and f>=0,'invalid_volcano_parameters')
    need(a['schema_version']=='de_classification_v0.1' and a['classification_status']=='CLASSIFIED'
        and a['DE_members_evaluated']=='TRUE','volcano_source_not_classified')
    need(a['source_contract']=='limma_core_v0.1' and a['source_output_status']=='COMPUTED_CANDIDATE'
        and a['source_warning_count']=='0' and a['threshold_scale']=='log2FC' and a['boundary_rule']==BOUNDARY
        and a['statistical_recomputation']=='none' and a['row_order']=='original'
        and a['row_identity']=='source_row_ordinal_no_ProteinID_deduplication'
        and a['adjusted_p_max']==p['adjusted_p_max'] and a['absolute_log2fc_min']==p['absolute_log2fc_min'],'invalid_volcano_binding')
    seen=set();counts=Counter();tested=0
    for values in rows:
        r=dict(zip(CLASSIFICATION_HEADER,values));ordinal=_integer(r['source_row_ordinal'])
        need(0<ordinal<=9007199254740991 and ordinal not in seen,'invalid_volcano_identity');seen.add(ordinal)
        label=r['classification'];need(label in CLASSES and r['contrast_id']==a['source_contrast_id'],'invalid_volcano_binding')
        need(r['test_status']==('not_testable' if label=='not_testable' else 'testable'),'invalid_volcano_binding')
        selected='TRUE' if label in ('Up','Down') else 'NOT_EVALUATED' if label=='not_evaluated' else 'FALSE'
        need(r['selected_DE']==selected,'invalid_volcano_binding')
        _number(r['log2FC']);probability=_number(r['adj.P.Val'])
        if r['test_status']=='testable':
            tested+=1;need(not math.isfinite(probability) or 0<=probability<=1,'volcano_q_out_of_range')
        counts[label]+=1
    need(_integer(a['input_rows'])==len(rows) and _integer(a['source_testable_rows'])==tested
        and _integer(a['source_not_testable_rows'])==len(rows)-tested,'invalid_volcano_counts')
    need(all(_integer(a[k])==counts[k] for k in CLASSES) and _integer(a['selected_DE_rows'])==counts['Up']+counts['Down'],'invalid_volcano_counts')
    return Inputs(classification,parameters,audit,rows,tuple(p.items()),tuple(a.items()))

def _expected(value):
    out=[]
    for values in value.rows:
        r=dict(zip(CLASSIFICATION_HEADER,values));label=r['classification'];x=_number(r['log2FC']);q=_number(r['adj.P.Val'])
        if label=='not_testable':reason='not_testable'
        elif label=='not_evaluated':reason='not_evaluated'
        elif not math.isfinite(x) or not math.isfinite(q):reason='non_finite_coordinate'
        else:
            out.append((x,-math.log10(max(q,FLOOR)),True,'plotted_q_floor' if q<FLOOR else 'plotted'));continue
        out.append((None,None,False,reason))
    return tuple(out)

def _versions(session):
    text=_text(session,SMALL_CAP);lines=text.splitlines();need(len(lines)>=4,'invalid_volcano_session')
    names=('R_VERSION','GGPLOT2_VERSION','DIGEST_VERSION');values=[]
    for line,name in zip(lines,names):
        pair=line.split('\t');need(len(pair)==2 and pair[0]==name and pair[1],'invalid_volcano_session');values.append(pair[1])
    need(values[0].startswith('R version ') and all(re.fullmatch(r'[0-9]+(\.[0-9]+)*([+-][0-9A-Za-z.]+)?',v) for v in values[1:]),'invalid_volcano_session')
    tail='\n'.join(lines[3:]);need(values[0] in tail and 'ggplot2_'+values[1] in tail and 'digest_'+values[2] in tail,'invalid_volcano_session')
    return values

def validate_tables(value,points,audit,session):
    need(type(value) is Inputs);actual=table(points,POINTS_HEADER);expected=_expected(value);need(len(actual)==len(value.rows),'invalid_volcano_rows')
    for original,row,(x,y,plotted,reason) in zip(value.rows,actual,expected):
        need(row[:20]==original,'volcano_original_token_changed')
        need(row[22]==('TRUE' if plotted else 'FALSE') and row[23]==reason,'invalid_volcano_point')
        if not plotted:need(row[20:22]==('NA','NA'),'invalid_volcano_point')
        else:
            got_x,got_y=_number(row[20]),_number(row[21])
            need(math.isfinite(got_x) and got_x==x and math.isfinite(got_y) and 0<=got_y<=300
                and math.isclose(got_y,y,rel_tol=1e-12,abs_tol=1e-12),'invalid_volcano_point')
    a=_kv(audit,AUDIT_KEYS,SMALL_CAP);p=dict(value.parameters);source=dict(value.source_audit);versions=_versions(session)
    classes=[r[18] for r in value.rows];inputs=Counter(classes);plotted=Counter(label for label,e in zip(classes,expected) if e[2]);excluded=inputs-plotted
    reasons=Counter(e[3] for e in expected);q_threshold=_number(p['adjusted_p_max']);fc=_number(p['absolute_log2fc_min'])
    true_y=-math.log10(q_threshold);display_y=-math.log10(max(q_threshold,FLOOR))
    want={'contract':CONTRACT,'plot_status':'PLOTTED' if any(e[2] for e in expected) else 'EMPTY_NO_PLOTTABLE_ROWS',
        'input_classification_sha256':sha(value.classification),'input_parameters_sha256':sha(value.parameter_bytes),'input_audit_sha256':sha(value.source_audit_bytes),
        'source_classification_status':source['classification_status'],'source_contrast_id':source['source_contrast_id'],
        'source_numerator':source['source_numerator'],'source_denominator':source['source_denominator'],
        'R_version':versions[0],'ggplot2_version':versions[1],'digest_version':versions[2],
        'x_source':'log2FC','axis':'adj.P.Val','y_transform':'-log10(pmax(adj.P.Val,1e-300))',
        'adjusted_p_max':p['adjusted_p_max'],'absolute_log2fc_min':p['absolute_log2fc_min'],'boundary_rule':source['boundary_rule'],
        'threshold_y_floored':'TRUE' if q_threshold<FLOOR else 'FALSE','q_display_floor':'1e-300',
        'q_zero_floor_rows':str(sum(e[2] and _number(r[13])==0 for r,e in zip(value.rows,expected))),
        'q_small_floor_rows':str(sum(e[2] and 0<_number(r[13])<FLOOR for r,e in zip(value.rows,expected))),
        'input_rows':str(len(value.rows)),'plotted_rows':str(sum(e[2] for e in expected)),'excluded_rows':str(sum(not e[2] for e in expected)),
        'statistical_recomputation':'none','row_identity':source['row_identity'],'row_order':'original',
        'png_width_px':'2400','png_height_px':'1800','png_dpi':'300','svg_width_in':'8','svg_height_in':'6',
        'image_download_format':'svg','internal_preview_format':'png','rendering_package':'ggplot2'}
    for prefix,counts in (('input',inputs),('plotted',plotted),('excluded',excluded)):
        for label in CLASSES:want[prefix+'_'+label]=str(counts[label])
    for reason in REASONS:want['reason_'+reason]=str(reasons[reason])
    numeric={'threshold_x_negative':-fc,'threshold_x_positive':fc,'threshold_y_true':true_y,'threshold_y_display':display_y,
        'y_axis_upper':min(300,1.05*max([1,display_y]+[e[1] for e in expected if e[2]]))}
    need(set(want)|set(numeric)==set(AUDIT_KEYS))
    need(all(a[k]==v for k,v in want.items()),'invalid_volcano_audit')
    for k,v in numeric.items():
        n=_number(a[k]);need(math.isfinite(n) and math.isclose(n,v,rel_tol=1e-12,abs_tol=1e-12),'invalid_volcano_audit')
    return {'contract':CONTRACT,'plot_status':a['plot_status'],'input_rows':len(value.rows),'plotted_rows':sum(e[2] for e in expected),'excluded_rows':sum(not e[2] for e in expected)}

def _png(body):
    need(body.startswith(b'\x89PNG\r\n\x1a\n'),'invalid_volcano_png');pos=8;seen=[];idat=0;physical=None
    while pos<len(body):
        need(pos+12<=len(body),'invalid_volcano_png');size=struct.unpack('>I',body[pos:pos+4])[0]
        end=pos+12+size;need(end<=len(body),'invalid_volcano_png');kind=body[pos+4:pos+8];data=body[pos+8:pos+8+size]
        need((zlib.crc32(kind+data)&0xffffffff)==struct.unpack('>I',body[end-4:end])[0],'invalid_volcano_png')
        if not seen:need(kind==b'IHDR' and size==13 and struct.unpack('>II',data[:8])==(2400,1800),'invalid_volcano_png')
        if kind==b'IHDR':need(not seen,'invalid_volcano_png')
        if kind==b'pHYs':need(physical is None and size==9,'invalid_volcano_png');physical=struct.unpack('>IIB',data)
        if kind==b'IDAT':idat+=size
        seen.append(kind);pos=end
        if kind==b'IEND':need(size==0 and pos==len(body),'invalid_volcano_png');break
    need(seen and seen[-1]==b'IEND' and idat>0 and physical==(11811,11811,1),'invalid_volcano_png')

def _svg(body):
    need(b'<!DOCTYPE' not in body and b'<!ENTITY' not in body and b'\0' not in body,'invalid_volcano_svg')
    try:root=ET.fromstring(body.decode('utf8'))
    except (UnicodeError,ET.ParseError):raise InvalidVolcano('invalid_volcano_svg') from None
    need(root.tag=='{http://www.w3.org/2000/svg}svg','invalid_volcano_svg')
    need(root.get('width') in ('576pt','8in') and root.get('height') in ('432pt','6in'),'invalid_volcano_svg')
    try:need(tuple(map(float,root.get('viewBox','').split()))==(0,0,576,432),'invalid_volcano_svg')
    except ValueError:raise InvalidVolcano('invalid_volcano_svg') from None
    for node in root.iter():
        tag=node.tag.rsplit('}',1)[-1]
        need(node.tag.startswith('{http://www.w3.org/2000/svg}') and tag in
            ('svg','g','defs','path','rect','circle','ellipse','line','polyline','polygon','text','tspan','symbol','use','clipPath','title','desc'),'invalid_volcano_svg')
        for key,value in node.attrib.items():
            attr=key.rsplit('}',1)[-1]
            need(not attr.lower().startswith('on') and (attr!='href' or value.startswith('#')),'invalid_volcano_svg')
            urls=re.findall(r'url\(([^)]*)\)',value,re.I)
            need(all(re.fullmatch(r'#[A-Za-z0-9_.:-]+',u) is not None for u in urls),'invalid_volcano_svg')

def validate_outputs(value,outputs):
    need(type(outputs) is dict and set(outputs)==set(OUTPUTS),'invalid_volcano_outputs')
    need(all(type(v) is bytes and 0<len(v)<=(SMALL_CAP if k in ('R-session.txt','volcano-audit.tsv') else FILE_CAP) for k,v in outputs.items())
        and sum(map(len,outputs.values()))<=TOTAL_CAP,'invalid_volcano_output_size')
    summary=validate_tables(value,outputs['volcano-points.tsv'],outputs['volcano-audit.tsv'],outputs['R-session.txt'])
    _png(outputs['volcano.png']);_svg(outputs['volcano.svg'])
    # Format/CRC/vector guards are not proof of R execution or scientific origin.
    return summary
