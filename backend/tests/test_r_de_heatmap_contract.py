"""One finite synthetic pure-contract case; no R, native/network/DB or old tests."""
from pathlib import Path
from unittest.mock import patch
import copy,importlib.util,json,math,socket,struct,subprocess,sys,unittest,zlib

MODULE=Path(__file__).resolve().parents[1]/'cyomics_projects/r_de_heatmap/contract.py'
spec=importlib.util.spec_from_file_location('synthetic_heatmap_contract',MODULE)
h=importlib.util.module_from_spec(spec);sys.modules[spec.name]=h;spec.loader.exec_module(h)

def tsv(header,rows):return ('\t'.join(header)+'\n'+''.join('\t'.join(r)+'\n' for r in rows)).encode('utf8')
def fmt(x):return 'NA' if x is None else format(x,'.17g')
def provenance(matrix,selected,samples):
    p={k:('0'*64 if k.endswith('_sha256') else '11111111-1111-4111-8111-111111111111') for k in h.PROVENANCE_KEYS}
    p.update(schemaVersion=1,contract='r_heatmap_provenance_v0.1',inputs=[{'name':n,'bytes':len(b),'sha256':h.sha(b)} for n,b in zip(h.INPUT_NAMES,(matrix,samples,selected))])
    return json.dumps(p,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
def selected_rows(rows):
    return [tuple(r[:2])+('contrast-original','testable','','1','2','','1','1','4','4','0.001','0.01','3','3','3','3','Up' if i%2==0 else 'Down','TRUE') for i,r in enumerate(rows)]
def fixture():
    rows=[('1','A;A-iso','1.00','2e0','3','4','5','6'),('3','same;group','2','NA','4','NA','6','NA'),
        ('8','same;group','7','7.0','7e0','7','7','7'),('9','all;missing',*('NA',)*6),('12','single;kept','NA','NA','4','NA','NA','NA'),
        ('20','not;selected','9','8','7','6','5','4')]
    sample=[('s'+str(i),str(120+i),'样本'+str(i),'gA' if i<=3 else 'gB','处理组' if i<=3 else 'WT','r'+str(i),'biological') for i in range(1,7)]
    matrix=tsv(h.MATRIX_HEADER,rows);selected=tsv(h.SELECTED_HEADER,selected_rows(rows[:5]));samples=tsv(h.SAMPLES_HEADER,sample)
    return matrix,selected,samples,provenance(matrix,selected,samples)
def png():
    def chunk(kind,body):return struct.pack('>I',len(body))+kind+body+struct.pack('>I',zlib.crc32(kind+body)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',2400,1350,8,2,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+b'\0'*7200)*1350))+chunk(b'IEND',b'')
def synthetic_outputs(value):
    # Independent simple arithmetic for this fixture's expected display only.
    expected=[]
    for raw in value.chosen:
        xs=[None if s=='NA' else float(s) for s in raw[2:]];ys=[v for v in xs if v is not None];n=len(ys)
        mean=sum(ys)/n if n else None;sd=math.sqrt(sum((v-mean)**2 for v in ys)/(n-1)) if n>=2 else None
        status='all_missing' if n==0 else 'insufficient_observations' if n==1 else 'zero_sd' if sd==0 else 'scaled'
        z=[(v-mean)/sd if v is not None and status=='scaled' else None for v in xs];expected.append((n,mean,sd,status,z))
    maps=[(r[0],r[1],sel[18],str(e[0]),fmt(e[1]),fmt(e[2]),e[3]) for r,sel,e in zip(value.chosen,value.selected_rows,expected)]
    zs=[tuple(r[:2])+tuple(fmt(z) for z in e[4]) for r,e in zip(value.chosen,expected)]
    nobs=sum(v!='NA' for r in value.rows for v in r[2:]);nz=sum(v is not None for e in expected for v in e[4])
    audit=dict(h.FIXED,input_rows=str(len(value.rows)),selected_rows=str(len(expected)),samples='6',observed_cells=str(nobs),missing_cells=str(len(value.rows)*6-nobs),plotted_observed_cells=str(nz),plotted_missing_cells=str(len(expected)*6-nz),R_version='R version 4.6.1 (synthetic)',pheatmap_version='1.0.13',color_limit=fmt(max([1]+[abs(v) for e in expected for v in e[4] if v is not None])))
    for status in h.STATUSES:audit[status+'_rows']=str(sum(e[3]==status for e in expected))
    for prefix,b in (('matrix',value.matrix),('selected_DE',value.selected),('samples',value.samples),('provenance',value.provenance)):
        audit[prefix+'_bytes']=str(len(b));audit[prefix+'_sha256']=h.sha(b)
    return {'R-session.txt':b'R_VERSION\tR version 4.6.1 (synthetic)\nPHEATMAP_VERSION\t1.0.13\nDIGEST_VERSION\t0.6.39\nR version 4.6.1 (synthetic)\nloaded via a namespace: pheatmap_1.0.13 digest_0.6.39\n',
        'heatmap-audit.tsv':tsv(('key','value'),[(k,audit[k]) for k in h.AUDIT_KEYS]),'heatmap-matrix.tsv':tsv(h.MATRIX_HEADER,value.chosen),
        'heatmap-row-map.tsv':tsv(h.ROW_MAP_HEADER,maps),'heatmap-samples.tsv':value.samples,'heatmap-zscore.tsv':tsv(h.MATRIX_HEADER,zs),
        'heatmap.png':png(),'heatmap.svg':b'<svg xmlns="http://www.w3.org/2000/svg" width="1152pt" height="648pt" viewBox="0 0 1152 648"><rect width="1152" height="648" fill="grey"/></svg>'}

class HeatmapFiniteContract(unittest.TestCase):
    def test_one_finite_combination(self):
        scenarios=[]
        with patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')) as network, patch.object(subprocess,'Popen',side_effect=AssertionError('native forbidden')) as native:
            args=fixture();value=h.validate_inputs(*args);out=synthetic_outputs(value);summary=h.validate_outputs(value,out)
            self.assertEqual(summary['status_counts'],{'scaled':2,'zero_sd':1,'all_missing':1,'insufficient_observations':1})
            self.assertEqual(h.table(out['heatmap-matrix.tsv'],h.MATRIX_HEADER),value.chosen);self.assertEqual(out['heatmap-samples.tsv'],args[2]);scenarios.append('six_columns_original_tokens_UTF8_semicolons_duplicate_rawIDs_distinct_ordinals')
            self.assertEqual(h.expected_rows(value)[2][4],(None,)*6);scenarios.append('observed_sample_sd_and_all_four_row_statuses_no_imputation')
            # All-undefined rows still have a complete matrix/map/NA display, limit1.
            rows=value.chosen[2:];matrix=tsv(h.MATRIX_HEADER,rows);selected=tsv(h.SELECTED_HEADER,selected_rows(rows));v=h.validate_inputs(matrix,selected,args[2],provenance(matrix,selected,args[2]))
            s=h.validate_outputs(v,synthetic_outputs(v));self.assertEqual(s['color_limit'],1);self.assertEqual(s['selected_rows'],3);scenarios.append('all_undefined_rows_retained_gray_display_limit1')
            for token in ('','NaN','Inf','-Inf','1e999'):
                bad=args[0].replace(b'1.00',token.encode(),1)
                with self.assertRaises(h.InvalidHeatmap):h.validate_inputs(bad,args[1],args[2],provenance(bad,args[1],args[2]))
            for bad in (args[1].replace(b'A;A-iso',b'A',1),args[1].replace(b'\tTRUE\n',b'\tFALSE\n',1)):
                with self.assertRaises(h.InvalidHeatmap):h.validate_inputs(args[0],bad,args[2],provenance(args[0],bad,args[2]))
            p=json.loads(args[3]);p['inputs'][0]['sha256']='f'*64
            for raw in (json.dumps(p,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode(),args[3]+b'\n'):
                with self.assertRaises(h.InvalidHeatmap):h.validate_inputs(args[0],args[1],args[2],raw)
            scenarios.append('nonfinite_empty_identity_selection_pin_and_noncanonical_JSON_refused')
            for key,bad in [('heatmap-matrix.tsv',out['heatmap-matrix.tsv'].replace(b'1.00',b'1',1)),('heatmap-zscore.tsv',out['heatmap-zscore.tsv'].replace(b'NA',b'0',1)),('heatmap-samples.tsv',out['heatmap-samples.tsv'].replace('处理组'.encode(),b'guess',1)),('heatmap-audit.tsv',out['heatmap-audit.tsv'].replace(b'cluster_rows\tFALSE',b'cluster_rows\tTRUE',1)),('heatmap.svg',out['heatmap.svg'].replace(b'<rect',b'<script'))]:
                altered=dict(out);altered[key]=bad
                with self.assertRaises(h.InvalidHeatmap):h.validate_outputs(value,altered)
            with self.assertRaises(h.InvalidHeatmap):h.validate_outputs(value,dict(out,extra=b'x'))
            with patch.object(h,'FILE_CAP',8):
                with self.assertRaises(h.InvalidHeatmap):h.validate_outputs(value,out)
            with patch.object(h,'TOTAL_CAP',sum(map(len,out.values()))-1):
                with self.assertRaises(h.InvalidHeatmap):h.validate_outputs(value,out)
            scenarios.append('raw_token_NA_label_audit_vector_artifact_set_and_size_guards')
            self.assertEqual(network.call_count,0);self.assertEqual(native.call_count,0)
        print(json.dumps({'single_finite_combination':'PASS','scenario_groups':scenarios,'R_native_DB_HTTP_calls':0,'old_suites':0,'fixtures':'wholly synthetic; image structure is not R execution or visual proof'}))

if __name__=='__main__':unittest.main()
