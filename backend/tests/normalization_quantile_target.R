args<-commandArgs(trailingOnly=TRUE);source(args[[1]])
parent<-list(processed_dataset_version_id="11111111-1111-4111-8111-111111111111",project_id="22222222-2222-4222-8222-222222222222",
 manifest_sha256=paste(rep("a",64),collapse=""),parameters=list(normalization="none",imputation="none",output_scale="log2",
 input_scale="raw_nonnegative",log2="positive_only",zero_policy="to_NA",blank_policy="to_NA",pseudocount=0),
 artifacts=data.frame(name=c("working-matrix.tsv","original-blank-mask.tsv","original-zero-mask.tsv","working-NA-mask.tsv","imputed-mask.tsv","parameters.json"),bytes=rep(1,6),sha256=rep(paste(rep("a",64),collapse=""),6)))
frame<-function(m) {d<-data.frame(record_number=seq_len(nrow(m)),protein_ids_raw=rep("重复蛋白",nrow(m)),check.names=FALSE);for(j in seq_len(ncol(m)))d[[c("样本甲","样本乙")[[j]]]]<-m[,j];d}
make<-function(m) list(working=frame(m),masks=list(original_blank=frame(is.na(m)),original_zero=frame(matrix(FALSE,nrow(m),ncol(m))),working_NA=frame(is.na(m)),imputed=frame(matrix(FALSE,nrow(m),ncol(m)))))
run<-function(v) quantile_saved_log2(v$working,v$masks,parent)
fails<-function(expr,code) stopifnot(identical(tryCatch({force(expr);"NO_ERROR"},error=function(e)conditionMessage(e)),code))
v<-make(matrix(c(5,1,3,2,4,6),3,2));z<-run(v)
stopifnot(max(abs(as.matrix(z$working_matrix[-(1:2)])-matrix(c(5.5,1.5,3.5,1.5,3.5,5.5),3,2)))<1e-14,
 identical(z$masks,v$masks),identical(z$working_matrix[1:2],v$working[1:2]),all(is.na(z$factors$scalar_factor)),!z$analysis_ready)
ties<-run(make(matrix(c(1,1,4,2,4,6),3,2)))
stopifnot(max(abs(as.matrix(ties$working_matrix[-(1:2)])-matrix(c(2,2,5,1.5,2.5,5),3,2)))<1e-14)
v<-make(matrix(c(1,NA,5,2,4,6),3,2));z<-run(v)
expected<-matrix(c(1.5,NA,5.5,1.5,3.5,5.5),3,2)
stopifnot(identical(is.na(z$working_matrix),is.na(v$working)),max(abs(as.matrix(z$working_matrix[-(1:2)])-expected),na.rm=TRUE)<1e-14,
 "unequal_missingness_interpolated_quantile_curves_no_cell_imputation" %in% z$warnings)
fails(run(make(matrix(c(NA,NA,NA,1,2,3),3,2))),"all_missing_sample")
fails(run(make(matrix(c(1,NA,NA,1,2,3),3,2))),"insufficient_observed_for_quantile_interpolation")
v<-make(matrix(c(1,2,3,2,4,6),3,2));v$masks$imputed[[3]][1]<-TRUE;fails(run(v),"unsupported_imputed_input")
cat("PASS_NEW_QUANTILE_HAND noNA/ties/missing/native-oracles/zeroOneObservedRefusal/UnicodeDuplicate/masks\n")
if(length(args)==4L) {
 p<-dget(args[[3]]);result<-quantile_saved_parent(args[[2]],p,args[[4]])
 stopifnot(nrow(result$working_matrix)==2252L,nrow(result$factors)==21L,
 result$provenance$missing_before==23473L,result$provenance$missing_after==23473L,result$provenance$imputed_after==0L,
 sum(!is.na(as.matrix(result$working_matrix[-(1:2)])))==23819L)
 cat("PASS_NEW_QUANTILE_ACTUAL_ONCE rows2252 samples21 observed23819 NA23473 imputed0\n")
}
