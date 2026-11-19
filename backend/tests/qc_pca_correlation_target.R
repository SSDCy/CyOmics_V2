# Unique new target: sign-invariant hand oracles plus one actual saved2fc call.
args<-commandArgs(trailingOnly=TRUE)
stopifnot(length(args)==5L,as.character(getRversion())=="4.6.1")
source(args[[1L]],local=TRUE)
square<-rbind(c(-1,-1,1,1),c(-1,1,-1,1),c(5,5,5,5),c(NA,2,3,4))
colnames(square)<-c("sample A","sample B","sample C","sample D")
hand<-qc_pca_correlation_statistics(square,c(2L,5L,8L,9L),c("dup;P","dup;P","constant","missing"))
stopifnot(hand$pca_status=="estimated",identical(which(hand$included),c(1L,2L)),
  hand$row_audit$PCA_reason[[3L]]=="constant_across_samples",
  hand$row_audit$PCA_reason[[4L]]=="missing_in_one_or_more_samples",
  all(abs(hand$fit$sdev-sqrt(4/3))<1e-12),
  all(abs(hand$fit$sdev^2/sum(hand$fit$sdev^2)-c(0.5,0.5))<1e-12))
original_scores<-t(square[1:2,,drop=FALSE])
# Distances and Gram matrix survive arbitrary PC signs or tied-PC rotations.
stopifnot(max(abs(as.matrix(stats::dist(hand$fit$x))-as.matrix(stats::dist(original_scores))))<1e-12,
  max(abs(tcrossprod(hand$fit$x)-tcrossprod(original_scores)))<1e-12,
  hand$paired_n[1,2]==3,abs(hand$correlation[1,2]-5/sqrt(28))<1e-12,
  hand$paired_n[1,4]==3,abs(hand$correlation[1,4]-1)<1e-12)
edge<-cbind(A=1:4,B=c(2,4,6,NA),constant=rep(7,4),sparse=c(NA,NA,1,2))
bad<-qc_pca_correlation_statistics(edge,1:4,letters[1:4])
stopifnot(bad$paired_n[1,2]==3,abs(bad$correlation[1,2]-1)<1e-12,
  is.na(bad$correlation[1,3]),bad$pair_reasons[1,3]=="constant_paired_sample",
  is.na(bad$correlation[3,3]),bad$pair_reasons[3,3]=="constant_paired_sample",
  bad$paired_n[1,4]==2,is.na(bad$correlation[1,4]),
  is.na(bad$correlation[4,4]),bad$pair_reasons[4,4]=="insufficient_paired_n")
few<-qc_pca_correlation_statistics(square[,1:2,drop=FALSE],1:4,letters[1:4])
stopifnot(few$pca_status=="insufficient_samples",is.null(few$fit),!any(few$row_audit$included_PCA))
empty<-qc_pca_correlation_statistics(matrix(NA_real_,2L,3L),1:2,c("a","b"))
stopifnot(empty$pca_status=="insufficient_eligible_proteins",empty$correlation_status=="no_estimable_sample_pairs",
  all(is.na(empty$correlation)))
cat("HAND_ORACLES_PASS PCA50/50 distances/Gram/sign invariant; complete-case/constant; Pearson5/sqrt28 and1; paired n; NA not0/1\n")
parent<-dget(args[[3L]]);design<-dget(args[[4L]])
stopifnot(parent$processed_dataset_version_id=="2fc0a8e6-ef3d-4afe-a15c-f27043b00227",
  design$sample_design_version_id=="4cf9e303-46a1-4c04-969c-6400edc5490c")
before<-serialize(list(parent,design),NULL)
result<-withCallingHandlers(qc_saved_pca_correlation(args[[2L]],parent,design,args[[5L]]),
  warning=function(w)stop("unexpected_actual_target_warning",call.=FALSE))
stopifnot(identical(before,serialize(list(parent,design),NULL)),result$summary$source_rows==2252L,
  result$summary$samples==21L,result$statistics$pca_status=="estimated",
  result$statistics$correlation_status%in%c("estimated","estimated_with_NA_pairs"),
  sum(result$statistics$row_audit$included_PCA)>=2L,
  abs(sum(result$explained)-1)<1e-12,
  nrow(result$statistics$fit$x)==21L,nrow(result$statistics$fit$rotation)==sum(result$statistics$included),
  max(abs(result$statistics$correlation-t(result$statistics$correlation)),na.rm=TRUE)<1e-12,
  identical(unname(result$statistics$paired_n),unname(t(result$statistics$paired_n))))
read<-function(path)utils::read.delim(path,check.names=FALSE,quote="\"",comment.char="",
  colClasses="character",na.strings=character(0),stringsAsFactors=FALSE)
original<-read(file.path(args[[2L]],"working-matrix.tsv"))
retained<-read(file.path(args[[5L]],"full-saved-matrix.tsv"))
stopifnot(identical(original,retained))
selected<-read(file.path(args[[5L]],"PCA-selected-source.tsv"))
stopifnot(identical(unname(as.matrix(selected)),unname(as.matrix(original[result$statistics$included,,drop=FALSE]))))
map<-read(file.path(args[[5L]],"sample-map.tsv"))
stopifnot(identical(map$sample_name_raw,names(original)[-(1:2)]),
  identical(map$group_label_raw,design$rows$group))
for(name in c("PCA.svg","correlation.svg")) {
  text<-paste(readLines(file.path(args[[5L]],name),warn=FALSE),collapse="\n")
  stopifnot(grepl("<svg",text,fixed=TRUE),grepl("</svg>",text,fixed=TRUE),
    !grepl("<image",text,fixed=TRUE),!grepl("<script",text,fixed=TRUE))
}
stopifnot(!any(grepl("[.](png|pdf)$",list.files(args[[5L]]),ignore.case=TRUE)))
cat("QC_PCA_CORRELATION_ACTUAL_TARGET_PASS\n")
cat("source_rows=",result$summary$source_rows," samples=",result$summary$samples,
  " PCA_eligible=",result$summary$PCA_eligible_rows," PCA_included=",result$summary$PCA_included_rows,
  " missing_excluded=",result$summary$missing_excluded_rows," constant_excluded=",result$summary$constant_excluded_rows,
  " estimable_pairs=",result$summary$estimable_ordered_pairs," nonestimable_pairs=",result$summary$nonestimable_ordered_pairs,"\n",sep="")
