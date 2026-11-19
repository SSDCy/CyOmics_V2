args <- commandArgs(trailingOnly=TRUE)
source(args[[1]])
parent <- list(processed_dataset_version_id="11111111-1111-4111-8111-111111111111",
 project_id="22222222-2222-4222-8222-222222222222",manifest_sha256=paste(rep("a",64),collapse=""),
 parameters=list(normalization="none",imputation="none",output_scale="log2",input_scale="raw_nonnegative",
 log2="positive_only",zero_policy="to_NA",blank_policy="to_NA",pseudocount=0))
frame <- function(m) {d <- data.frame(record_number=seq_len(nrow(m)),protein_ids_raw=rep("duplicate蛋白",nrow(m)),check.names=FALSE);for(j in seq_len(ncol(m)))d[[c("样本A","样本B")[[j]]]]<-m[,j];d}
make <- function(m) {
 raw <- frame(m);m0<-!is.na(m)&m==0;work<-m;work[is.na(m)|m0]<-NA_real_;work[!is.na(work)]<-log2(work[!is.na(work)])
 list(raw=raw,working=frame(work),masks=list(original_blank=frame(is.na(m)),original_zero=frame(m0),working_NA=frame(is.na(work)),imputed=frame(matrix(FALSE,nrow(m),ncol(m)))))
}
run <- function(v) total_intensity_log2(v$raw,v$working,v$masks,parent)
fails <- function(expr,code) stopifnot(identical(tryCatch({force(expr);"NO_ERROR"},error=function(e)conditionMessage(e)),code))
v<-make(matrix(c(10,20,0,NA,20,40,0,NA),4,2));z<-run(v)
stopifnot(identical(unname(z$factors$input_total_raw),c(30,60)),identical(unname(z$factors$reference_total_raw),c(45,45)),
 all(abs(z$factors$multiplicative_factor_raw-c(1.5,.75))<1e-15),
 max(abs(as.matrix(z$working_matrix[1:2,-(1:2)])-matrix(log2(c(15,30,15,30)),2,2)))<1e-14,
 identical(z$masks,v$masks),identical(z$working_matrix[1:2],v$working[1:2]),
 identical(is.na(z$working_matrix),is.na(v$working)),!z$analysis_ready,!z$provenance$published)
single<-run(make(matrix(c(5,NA,10,20),2,2)))
stopifnot(all(c("sample_with_single_observation","unequal_observed_counts_missingness_bias_not_corrected") %in% single$warnings))
fails(run(make(matrix(c(NA,0,1,2),2,2))),"insufficient_observed_values")
fails(run(make(matrix(c(1e308,1e308,1,2),2,2))),"invalid_sample_total")
bad<-v;bad$raw[[3]][1]<--1;fails(run(bad),"invalid_numeric_values")
bad<-v;bad$working[[3]][1]<-bad$working[[3]][1]+1;fails(run(bad),"raw_log2_mismatch")
bad<-v;bad$masks$imputed[[3]][1]<-TRUE;fails(run(bad),"mask_partition_mismatch")
bad<-v;bad$raw$record_number[1]<-2;fails(run(bad),"raw_identity_mismatch")
cat("PASS_NEW_TOTAL_HAND_TARGET: known factors/duplicateUnicode/zeroBlankNA/singleUnequal/allmissing/overflow/rawscale/masks/identity\n")
if(length(args)==4L) {
 p <- dget(args[[3]])
 result<-total_saved_parent(args[[2]],p,args[[4]])
 stopifnot(nrow(result$working_matrix)==2252L,nrow(result$factors)==21L,
   result$provenance$missing_before==23473L,result$provenance$missing_after==23473L,
   result$provenance$imputed_after==0L,sum(!is.na(as.matrix(result$working_matrix[-(1:2)])))==23819L)
 cat("PASS_NEW_TOTAL_ACTUAL_ONCE rows2252 samples21 NA23473 imputed0\n")
}
