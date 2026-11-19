# One bounded new scientific target; invoked by normalization_median_target.py.
args <- commandArgs(trailingOnly=TRUE)
stopifnot(length(args)==5L)
source(args[[1L]], local=TRUE)
input <- args[[2L]]; manifest <- args[[3L]]; manifest_sha <- args[[4L]]; output <- args[[5L]]
read <- function(name) utils::read.delim(file.path(input,name), check.names=FALSE,
  stringsAsFactors=FALSE, quote="\"", comment.char="", na.strings="NA")
working <- read("working-matrix.tsv")
masks <- list(original_blank=read("original-blank-mask.tsv"),
  original_zero=read("original-zero-mask.tsv"), working_NA=read("working-NA-mask.tsv"),
  imputed=read("imputed-mask.tsv"))
parent <- list(processed_dataset_version_id="00000000-0000-4000-8000-000000000001",
  project_id="00000000-0000-4000-8000-000000000002", manifest_sha256=manifest_sha,
  artifacts=utils::read.delim(manifest, stringsAsFactors=FALSE),
  parameters=list(output_scale="log2", log2="positive_only", normalization="none",
    imputation="none", zero_policy="to_NA", blank_policy="to_NA", pseudocount=0),
  fixture_only=TRUE)
before <- serialize(list(working,masks,parent),NULL)
result <- median_center_log2(working,masks,parent,"median")
expected <- working
expected[[3L]] <- c(1.5,NA,-1.5,NA)
expected[[4L]] <- c(1.5,-1.5,NA,NA)
stopifnot(isTRUE(all.equal(result$working_matrix,expected,tolerance=1e-12)),
  identical(result$masks,masks), identical(result$provenance$parent,parent),
  identical(result$factors$input_median_log2,c(-0.5,0.5)),
  identical(result$factors$reference_log2,c(0,0)),
  identical(result$factors$additive_offset_log2,c(0.5,-0.5)),
  all(result$factors$output_median_log2==0),
  identical(result$factors$observed_count,c(2L,2L)),
  result$provenance$missing_before==4L, result$provenance$missing_after==4L,
  result$provenance$imputed_before==0L, result$provenance$imputed_after==0L,
  identical(serialize(list(working,masks,parent),NULL),before),
  !result$analysis_ready, !result$provenance$byte_integrity_attested,
  !result$provenance$published, is.null(result$provenance$output_artifact_pins))
identity <- median_center_log2(working,masks,parent,"none")
stopifnot(identical(identity$working_matrix,working), identical(identity$masks,masks),
  all(identity$factors$additive_offset_log2==0))
fails <- function(expr,code) {
  actual <- tryCatch({force(expr);"unexpected_success"},error=function(e)conditionMessage(e))
  stopifnot(identical(actual,code))
}
all_na <- working; all_na[[3L]] <- rep(NA_real_,nrow(working))
na_masks <- masks
na_masks$original_blank[[3L]] <- TRUE; na_masks$original_zero[[3L]] <- FALSE
na_masks$working_NA[[3L]] <- TRUE
fails(median_center_log2(all_na,na_masks,parent),"insufficient_observed_values")
stopifnot(identical(median_center_log2(all_na,na_masks,parent,"none")$working_matrix,all_na))
single <- all_na; single[[3L]][[1L]] <- 3
single_masks <- na_masks
single_masks$original_blank[[3L]][[1L]] <- FALSE
single_masks$working_NA[[3L]][[1L]] <- FALSE
one <- median_center_log2(single,single_masks,parent)
stopifnot(one$factors$observed_count[[1L]]==1L,
  identical(one$warnings,"sample_with_single_observation"))
bad <- working; bad[[3L]][[1L]] <- Inf
fails(median_center_log2(bad,masks,parent),"nonfinite_matrix_values")
bad[[3L]][[1L]] <- NaN
fails(median_center_log2(bad,masks,parent),"nonfinite_matrix_values")
bad_masks <- masks; bad_masks$imputed[[3L]][[1L]] <- TRUE
fails(median_center_log2(working,bad_masks,parent),"unsupported_imputed_input")
bad_masks <- masks; bad_masks$working_NA[[3L]][[1L]] <- TRUE
fails(median_center_log2(working,bad_masks,parent),"mask_partition_mismatch")
bad_masks <- masks; bad_masks$original_blank$record_number[[1L]] <- 9L
fails(median_center_log2(working,bad_masks,parent),"mask_identity_mismatch")
bad_parent <- parent; bad_parent$parameters$output_scale <- "raw_nonnegative"
fails(median_center_log2(working,masks,bad_parent),"unsupported_parent_strategy")
fails(median_center_log2(working,masks,parent,"total"),"unsupported_normalization")
fails(median_center_log2(working[FALSE,],lapply(masks,function(x)x[FALSE,]),parent),"invalid_matrix_shape")

# An unequal-count odd-sample fixture distinguishes equal sample weighting from pooling.
odd <- working; odd[[3L]] <- c(0,0,100,NA); odd[[4L]] <- c(10,NA,NA,NA)
odd[["LFQ intensity third raw name"]] <- c(20,20,NA,NA)
odd_masks <- lapply(masks,function(mask) {
  mask[[3L]] <- rep(FALSE,4L); mask[[4L]] <- rep(FALSE,4L)
  mask[["LFQ intensity third raw name"]] <- rep(FALSE,4L)
  mask
})
odd_masks$original_blank[-(1:2)] <- as.data.frame(is.na(odd[-(1:2)]))
odd_masks$working_NA[-(1:2)] <- as.data.frame(is.na(odd[-(1:2)]))
odd_result <- median_center_log2(odd,odd_masks,parent)
stopifnot(identical(odd_result$factors$input_median_log2,c(0,10,20)),
  all(odd_result$factors$reference_log2==10),
  identical(odd_result$factors$additive_offset_log2,c(10,0,-10)))

stopifnot(dir.create(output,recursive=FALSE))
write <- function(value,name) utils::write.table(value,file.path(output,name),sep="\t",
  quote=TRUE,row.names=FALSE,na="NA",qmethod="double",eol="\n",fileEncoding="UTF-8")
write(result$working_matrix,"working-matrix.tsv")
write(result$factors,"normalization-factors.tsv")
for (name in names(result$masks)) write(result$masks[[name]],paste0(name,"-mask.tsv"))
saveRDS(result,file.path(output,"core-result.rds"),version=3)
dput(result$parameters,file=file.path(output,"parameters.R.txt"))
writeLines(utils::capture.output(utils::sessionInfo()),file.path(output,"R-session-info.txt"))
cat("NORMALIZATION_MEDIAN_CORE_PASS\n")
cat(R.version.string,"\n",sep="")
cat("full saved log2 fixture; literal expected values; median/None; masks/NA/duplicate IDs; unequal-count reference; minimum observations; unsupported inputs\n")
