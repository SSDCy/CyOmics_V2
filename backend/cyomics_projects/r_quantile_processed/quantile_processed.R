# One native child, full saved parent, approved scientific core unchanged.
fail <- function(code) stop(code,call.=FALSE)
read_text_table <- function(path) {
  value <- utils::read.delim(path,sep="\t",quote="\"",comment.char="",check.names=FALSE,
    colClasses="character",na.strings=character(0),strip.white=FALSE,
    stringsAsFactors=FALSE,fileEncoding="UTF-8")
  if (anyNA(value) || ncol(value)<3L || nrow(value)<1L) fail("quantile_parent_shape")
  value
}
main <- function(args) {
  if (length(args)!=1L) fail("quantile_arguments")
  job <- args[[1L]]
  parent <- dget(file.path(job,"parent-provenance.R"))
  source(file.path(job,"quantile_core.R"),local=TRUE)
  working <- read_text_table(file.path(job,"parent-working-matrix.tsv"))
  if (any(!grepl("^[1-9][0-9]*$",working[[1L]]))) fail("quantile_parent_rows")
  working[[1L]] <- as.integer(working[[1L]])
  for (j in 3:ncol(working)) {
    raw <- working[[j]]
    value <- rep(NA_real_,length(raw));observed <- raw!="NA"
    value[observed] <- as.numeric(raw[observed])
    if (any(!is.finite(value[observed]))) fail("quantile_parent_numeric")
    working[[j]] <- value
  }
  mask_files <- c(original_blank="original-blank-mask.tsv",original_zero="original-zero-mask.tsv",
    working_NA="working-NA-mask.tsv",imputed="imputed-mask.tsv")
  masks <- lapply(mask_files,function(name) {
    m <- read_text_table(file.path(job,paste0("parent-",name)))
    if (any(!grepl("^[1-9][0-9]*$",m[[1L]]))) fail("quantile_parent_rows")
    m[[1L]] <- as.integer(m[[1L]])
    for (j in 3:ncol(m)) {
      if (any(!m[[j]] %in% c("TRUE","FALSE"))) fail("quantile_parent_mask")
      m[[j]] <- m[[j]]=="TRUE"
    }
    m
  })
  fixed_libs <- c("/home/user01/Cy_Omics_v2.0/audit/R-LIMMA-ENV-003/staged-library","/usr/local/lib/R/site-library","/usr/lib/R/library")
  .libPaths(fixed_libs,include.site=FALSE)
  if(!identical(normalizePath(.libPaths(),mustWork=TRUE),fixed_libs)) fail("quantile_library_paths")
  expected_paths <- c(limma=paste0(fixed_libs[[1]],"/limma"),statmod=paste0(fixed_libs[[1]],"/statmod"),digest=paste0(fixed_libs[[2]],"/digest"))
  expected_versions <- c(limma="3.68.5",statmod="1.5.2",digest="0.6.39")
  for(pkg in names(expected_paths)) {
    if(!requireNamespace(pkg,quietly=TRUE)) fail("quantile_dependency_missing")
    actual_path <- normalizePath(getNamespaceInfo(asNamespace(pkg),"path"),mustWork=TRUE)
    if(!identical(actual_path,unname(expected_paths[[pkg]])) || as.character(utils::packageVersion(pkg))!=expected_versions[[pkg]]) fail("quantile_namespace_path_or_version")
  }
  result <- quantile_saved_log2(working,masks,parent)
  if (!identical(result$masks,masks) || result$analysis_ready) fail("quantile_mask_changed")
  out <- file.path(job,"output")
  if (file.exists(out) || !dir.create(out,mode="0700")) fail("quantile_output_exists")
  fixed <- c("filter-log.tsv","row-map.tsv","raw-retained.tsv",unname(mask_files))
  for (name in fixed) {
    if (!file.copy(file.path(job,paste0("parent-",name)),file.path(out,name),overwrite=FALSE)) fail("quantile_output_copy")
  }
  if (!file.copy(file.path(job,"request-parameters.json"),file.path(out,"parameters.json"),overwrite=FALSE)) fail("quantile_output_copy")
  write <- function(value,name) utils::write.table(value,file.path(out,name),sep="\t",quote=TRUE,
    row.names=FALSE,col.names=TRUE,na="NA",qmethod="double",eol="\n",fileEncoding="UTF-8")
  rendered <- result$working_matrix
  for (j in 3:ncol(rendered)) {
    value <- rendered[[j]]
    rendered[[j]] <- ifelse(is.na(value),"NA",sprintf("%.17g",value))
  }
  write(rendered,"working-matrix.tsv")
  factors <- result$factors
  for (name in c("input_median_log2","output_median_log2","minimum_change_log2","maximum_change_log2"))
    factors[[name]] <- sprintf("%.17g",factors[[name]])
  factors$scalar_factor <- rep("NA",nrow(factors))
  factors$warning_codes <- rep(paste(result$warnings,collapse=";"),nrow(factors))
  n <- nrow(factors)
  summary <- data.frame(strategy_contract=rep("limma_quantile_saved_parent_v1",n),
    core_contract=rep("limma_quantile_saved_log2_no_imputation_v1",n),normalization=rep("quantile",n),
    imputation=rep("none",n),analysis_ready=rep(FALSE,n),
    parent_processed_dataset_version_id=rep(parent$processed_dataset_version_id,n),
    parent_manifest_sha256=rep(parent$manifest_sha256,n),
    parent_dataset_version_id=rep(parent$parent_dataset_version_id,n),parent_file_id=rep(parent$file_id,n),
    parent_source_sha256=rep(parent$source$source_sha256,n),check.names=FALSE)
  write(cbind(summary,factors),"summary.tsv")
  writeLines(utils::capture.output(utils::sessionInfo()),file.path(out,"R-session-info.txt"),useBytes=TRUE)
  cat("QUANTILE_PROCESSED_COMPLETE\n")
}
code <- tryCatch(withCallingHandlers({main(commandArgs(trailingOnly=TRUE));0L},
  warning=function(w) stop("quantile_warning",call.=FALSE)),error=function(e) {
  # Never print private sample names/cells or arbitrary R exception text.
  cat("QUANTILE_PROCESSED_ERROR\n",file=stderr());2L
})
quit(status=code,save="no",runLast=FALSE)
