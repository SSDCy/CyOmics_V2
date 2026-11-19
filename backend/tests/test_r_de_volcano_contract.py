"""One finite pure IO test. All fixtures synthetic; no R rendering/execution proof."""
from collections import Counter
from pathlib import Path
from unittest.mock import patch
import copy,hashlib,json,math,socket,subprocess,sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cyomics_projects/r_de_volcano'))
import contract as v

def tsv(header,rows):return ('\t'.join(header)+'\n'+''.join('\t'.join(r)+'\n' for r in rows)).encode('utf8')
def kv(keys,values):return tsv(('key','value'),[(k,values[k]) for k in keys])
def row(ordinal,identity,x,q,label):
    tested=label!='not_testable'
    return (str(ordinal),identity,'contrast_demo','testable' if tested else 'not_testable','original_reason_keep',x,
        '2.000','' if tested else 'not_testable','7','0','4','20','0.012300',q,'3','3','3','3',label,
        'TRUE' if label in ('Up','Down') else 'NOT_EVALUATED' if label=='not_evaluated' else 'FALSE')

def inputs(rows,q_limit='0.0500',fc='1.00'):
    p={'schema_version':'de_classification_v0.1','input_scale':'log2','adjusted_p_max':q_limit,'absolute_log2fc_min':fc}
    counts=Counter(r[18] for r in rows)
    a={k:'original_opaque_token' for k in v.SOURCE_AUDIT_KEYS}
    a.update(schema_version='de_classification_v0.1',classification_status='CLASSIFIED',DE_members_evaluated='TRUE',
        source_contract='limma_core_v0.1',source_output_status='COMPUTED_CANDIDATE',source_warning_count='0',
        source_contrast_id='contrast_demo',source_numerator='g1',source_denominator='g2',threshold_scale='log2FC',
        adjusted_p_max=q_limit,absolute_log2fc_min=fc,boundary_rule=v.BOUNDARY,input_rows=str(len(rows)),
        source_testable_rows=str(len(rows)-counts['not_testable']),source_not_testable_rows=str(counts['not_testable']),
        selected_DE_rows=str(counts['Up']+counts['Down']),statistical_recomputation='none',
        row_identity='source_row_ordinal_no_ProteinID_deduplication',row_order='original')
    a.update({k:str(counts[k]) for k in v.CLASSES})
    return tsv(v.CLASSIFICATION_HEADER,rows),kv(v.PARAMETER_KEYS,p),kv(v.SOURCE_AUDIT_KEYS,a)

SESSION=(b'R_VERSION\tR version 4.6.1 (synthetic test only)\nGGPLOT2_VERSION\t0.0.0\nDIGEST_VERSION\t0.0.0\n'
    b'R version 4.6.1 (synthetic test only)\nloaded via a namespace: ggplot2_0.0.0 digest_0.0.0\n')

def reference_tables(raw,rows,oracle,q_limit='0.0500',fc='1.00'):
    """Oracle coordinates/reasons passed explicitly, never call validator internals."""
    points=tsv(v.POINTS_HEADER,[tuple(r)+tuple(e) for r,e in zip(rows,oracle)])
    p=float(q_limit);f=float(fc);inputs_count=Counter(r[18] for r in rows)
    plotted=Counter(r[18] for r,e in zip(rows,oracle) if e[2]=='TRUE');excluded=inputs_count-plotted
    reasons=Counter(e[3] for e in oracle);ys=[float(e[1]) for e in oracle if e[2]=='TRUE'];line=-math.log10(max(p,1e-300))
    a={'contract':'r_volcano_v0.1','plot_status':'PLOTTED' if ys else 'EMPTY_NO_PLOTTABLE_ROWS',
        'input_classification_sha256':hashlib.sha256(raw[0]).hexdigest(),'input_parameters_sha256':hashlib.sha256(raw[1]).hexdigest(),
        'input_audit_sha256':hashlib.sha256(raw[2]).hexdigest(),'source_classification_status':'CLASSIFIED',
        'source_contrast_id':'contrast_demo','source_numerator':'g1','source_denominator':'g2',
        'R_version':'R version 4.6.1 (synthetic test only)','ggplot2_version':'0.0.0','digest_version':'0.0.0',
        'x_source':'log2FC','axis':'adj.P.Val','y_transform':'-log10(pmax(adj.P.Val,1e-300))',
        'adjusted_p_max':q_limit,'absolute_log2fc_min':fc,'boundary_rule':v.BOUNDARY,
        'threshold_x_negative':format(-f,'.17g'),'threshold_x_positive':format(f,'.17g'),
        'threshold_y_true':format(-math.log10(p),'.17g'),'threshold_y_display':format(line,'.17g'),
        'threshold_y_floored':'TRUE' if p<1e-300 else 'FALSE','q_display_floor':'1e-300',
        'q_zero_floor_rows':str(sum(e[2]=='TRUE' and float(r[13])==0 for r,e in zip(rows,oracle))),
        'q_small_floor_rows':str(sum(e[2]=='TRUE' and 0<float(r[13])<1e-300 for r,e in zip(rows,oracle))),
        'input_rows':str(len(rows)),'plotted_rows':str(len(ys)),'excluded_rows':str(len(rows)-len(ys)),
        'statistical_recomputation':'none','row_identity':'source_row_ordinal_no_ProteinID_deduplication','row_order':'original',
        'png_width_px':'2400','png_height_px':'1800','png_dpi':'300','svg_width_in':'8','svg_height_in':'6',
        'rendering_package':'ggplot2','y_axis_upper':format(min(300,1.05*max([1,line]+ys)),'.17g'),
        'image_download_format':'svg','internal_preview_format':'png'}
    for prefix,counts in (('input',inputs_count),('plotted',plotted),('excluded',excluded)):
        for label in v.CLASSES:a[prefix+'_'+label]=str(counts[label])
    for reason in v.REASONS:a['reason_'+reason]=str(reasons[reason])
    return points,kv(v.AUDIT_KEYS,a),SESSION

class VolcanoBoundary(unittest.TestCase):
    def test_one_finite_boundary_case(self):
        scenarios=[]
        rows=[row(2,' P1;P2 ','1.00','0.0500','Up'),row(5,'蛋白;A','-2.0','0','Down'),
            row(6,'P3','0','1e-310','NS'),row(8,'NT;IDs','NA','NA','not_testable'),
            row(11,'NE','0.5','0.2','not_evaluated'),row(12,'infinite x','Inf','0.1','NS'),
            row(19,'nonfinite q','3','NaN','Up'),row(20,'negative-zero raw','-0','1','NS')]
        oracle=[('1','1.3010299956639813','TRUE','plotted'),('-2','300','TRUE','plotted_q_floor'),
            ('0','300','TRUE','plotted_q_floor'),('NA','NA','FALSE','not_testable'),
            ('NA','NA','FALSE','not_evaluated'),('NA','NA','FALSE','non_finite_coordinate'),
            ('NA','NA','FALSE','non_finite_coordinate'),('0','0','TRUE','plotted')]
        raw=inputs(rows);value=v.validate_inputs(*raw);points,audit,session=reference_tables(raw,rows,oracle)
        with patch.object(socket.socket,'connect',side_effect=AssertionError('network prohibited')) as network,patch.object(subprocess,'Popen',side_effect=AssertionError('R/native prohibited')) as native:
            summary=v.validate_tables(value,points,audit,session)
            self.assertEqual((summary['input_rows'],summary['plotted_rows'],summary['excluded_rows']),(8,4,4))
            self.assertEqual(value.rows[0][1],' P1;P2 ');self.assertEqual(value.rows[1][1],'蛋白;A')
            self.assertEqual(v.POINTS_HEADER[:20],v.CLASSIFICATION_HEADER);self.assertEqual(v.POINTS_HEADER[4],'reason')
            self.assertEqual(v.POINTS_HEADER[-4:],('plot_x','plot_y','plotted','plot_reason'))
            scenarios.append('raw_tokens_multiID_UTF8_reason_header_zero_smallq_p1_NS_preserved')
            for old,new in ((b' P1;P2 ',b'P1'),(b'1.00\t2.000',b'1\t2.000'),(b'\tTRUE\tplotted\n',b'\tTRUE\tunknown\n'),
                (b'\t300\tTRUE',b'\t310\tTRUE'),(b'\tNA\tNA\tFALSE',b'\t0\t0\tFALSE')):
                changed=points.replace(old,new,1);self.assertNotEqual(changed,points)
                with self.assertRaises(v.InvalidVolcano):v.validate_tables(value,changed,audit,session)
            scenarios.append('changed_original_or_plotted_fields_rejected')
            for q in ('-0.1','1.01'):
                bad=copy.deepcopy(rows);bad[5]=row(12,'infinite x','Inf',q,'NS')
                with self.assertRaises(v.InvalidVolcano):v.validate_inputs(*inputs(bad))
            scenarios.append('out_of_range_testable_q_refuses_even_when_x_nonfinite')
            bad=copy.deepcopy(rows);bad[1]=row(2,'duplicate ordinal','-2','0','Down')
            with self.assertRaises(v.InvalidVolcano):v.validate_inputs(*inputs(bad))
            for changed in (b'\xef\xbb\xbf'+raw[0],raw[0].replace(b'\n',b'\r\n'),raw[0].replace(b'P1',b'\xff',1),raw[0][:-1]):
                with self.assertRaises(v.InvalidVolcano):v.validate_inputs(changed,raw[1],raw[2])
            scenarios.append('identity_UTF8_BOM_CR_missingLF_rejected')
            for old,new in ((b'classification_status\tCLASSIFIED',b'classification_status\tBLOCKED_SOURCE_REVIEW_REQUIRED'),
                (b'input_rows\t8',b'input_rows\t9'),(b'adjusted_p_max\t0.0500',b'adjusted_p_max\t0.05')):
                with self.assertRaises(v.InvalidVolcano):v.validate_inputs(raw[0],raw[1],raw[2].replace(old,new,1))
            scenarios.append('blocked_source_wrong_count_parameter_token_binding_rejected')
            for empty_rows,empty_oracle in (([row(1,'NT','NA','NA','not_testable')],[('NA','NA','FALSE','not_testable')]),([],[])):
                data=inputs(empty_rows);bound=v.validate_inputs(*data);parts=reference_tables(data,empty_rows,empty_oracle)
                got=v.validate_tables(bound,*parts);self.assertEqual(got['plot_status'],'EMPTY_NO_PLOTTABLE_ROWS');self.assertEqual(got['plotted_rows'],0)
            scenarios.append('CLASSIFIED_empty_and_all_excluded_have_no_fake_point')
            tiny_rows=[row(1,'small_threshold','-2','0','Down')];tiny_oracle=[('-2','300','TRUE','plotted_q_floor')]
            tiny=inputs(tiny_rows,q_limit='1e-310',fc='2.0');bound=v.validate_inputs(*tiny)
            parts=reference_tables(tiny,tiny_rows,tiny_oracle,q_limit='1e-310',fc='2.0');v.validate_tables(bound,*parts)
            self.assertIn(b'threshold_y_true\t310\n',parts[1]);self.assertIn(b'threshold_y_display\t300\n',parts[1])
            self.assertIn(b'threshold_y_floored\tTRUE\n',parts[1]);scenarios.append('tiny_threshold_true_display_lines_audited')
            for old,new in ((b'statistical_recomputation\tnone',b'statistical_recomputation\trecomputed'),
                (b'q_small_floor_rows\t1',b'q_small_floor_rows\t0'),(b'plotted_NS\t2',b'plotted_NS\t3')):
                with self.assertRaises(v.InvalidVolcano):v.validate_tables(value,points,audit.replace(old,new,1),session)
            scenarios.append('audit_stats_floor_and_per_class_counts_checked')
            # Tiny inline vector is a format fixture, never a claimed R plot.
            svg=b'<svg xmlns="http://www.w3.org/2000/svg" width="576pt" height="432pt" viewBox="0 0 576 432"><desc>Synthetic validator fixture, not R output</desc><path d="M0 0L1 1"/></svg>'
            v._svg(svg)
            for changed in (svg.replace(b'576pt',b'1pt'),svg.replace(b'<path',b'<image'),
                svg.replace(b'<path',b'<script'),svg.replace(b'd="M0 0L1 1"',b'href="https://example.invalid/x"'),b'<!DOCTYPE svg>'+svg):
                with self.assertRaises(v.InvalidVolcano):v._svg(changed)
            with self.assertRaises(v.InvalidVolcano):v._png(b'not a PNG')
            with self.assertRaises(v.InvalidVolcano):v.validate_outputs(value,{'volcano.pdf':b'%PDF-'})
            self.assertEqual(v.IMAGE_DOWNLOADS,('volcano.svg',));self.assertEqual(v.INTERNAL_PREVIEWS,('volcano.png',))
            self.assertNotIn('volcano.pdf',v.OUTPUTS);scenarios.append('SVG_only_download_vector_dimensions_and_unsafe_or_old_format_rejected')
            self.assertEqual(network.call_count,0);self.assertEqual(native.call_count,0)
        print(json.dumps({'finite_case':'PASS','scenario_groups':scenarios,'R_executions':0,'actual_R_images_validated':0,
            'image_download':'SVG only','old_suites_run':0,'fixture_authority':'synthetic pure IO only; not an R executor or scientific proof'}))

if __name__=='__main__':unittest.main()
