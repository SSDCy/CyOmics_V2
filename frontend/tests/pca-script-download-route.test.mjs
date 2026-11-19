import test from 'node:test';
import assert from 'node:assert/strict';
import {pcaReadRoute} from '../qc-pca-correlation-owner-contract.mjs';
const base='/api/v1/projects/11111111-1111-4111-8111-111111111111/processed-datasets/22222222-2222-4222-8222-222222222222/qc-pca-correlation';
test('PCA-SCRIPT-DOWNLOAD-ROUTE-FIX-001 exact script underscore route only',()=>{
 const r=pcaReadRoute(base+'/artifacts/pca_correlation.R','GET');assert.equal(r.name,'pca_correlation.R');assert.equal(r.kind,'artifact');assert.equal(r.method,'GET');assert.equal(r.capMs,9000);assert.equal(pcaReadRoute(base,'GET').kind,'descriptor');
 for(const suffix of ['/artifacts/unknown_file.R','/artifacts/full-provenance.rds','/artifacts/pca%5fcorrelation.R','/artifacts/pca%5Fcorrelation.R','/artifacts/../pca_correlation.R','/artifacts/%2e%2e/pca_correlation.R','/artifacts/pca_correlation.R/','/artifacts/pca_correlation.R/..'])assert.throws(()=>pcaReadRoute(base+suffix,'GET'),e=>e.status===404,'must reject '+suffix);
 assert.throws(()=>pcaReadRoute(base+'/artifacts/pca_correlation.R?x=1','GET'),e=>e.status===400&&e.code==='invalid_qc_pca_correlation_request');assert.throws(()=>pcaReadRoute(base+'/artifacts/pca_correlation.R','POST'),e=>e.status===405&&e.code==='method_not_allowed');
});
