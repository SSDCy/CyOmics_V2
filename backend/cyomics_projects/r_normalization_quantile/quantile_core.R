# Inert source. Validation follows saved None contract; science is installed limma.
quantile_saved_log2 <- function(working,masks,parent) {
  need <- function(ok,code) if (!isTRUE(ok)) stop(code,call.=FALSE)
  need(is.data.frame(working) && nrow(working)>=1L && nrow(working)<=10000L &&
       ncol(working)>=3L && ncol(working)<=512L &&
       nrow(working)*(ncol(working)-2L)<=2000000L, "invalid_matrix_shape")
  need(identical(names(working)[1:2], c("record_number", "protein_ids_raw")) &&
       !anyNA(names(working)) && all(nzchar(names(working))) &&
       !anyDuplicated(names(working)), "invalid_matrix_columns")
  rows <- working[1:2]
  rn <- rows$record_number
  need(is.numeric(rn) && !anyNA(rn) && all(is.finite(rn)) &&
       all(rn>=1 & rn<=10000 & rn==floor(rn)) &&
       !anyDuplicated(rn) && !is.unsorted(rn, strictly=TRUE) &&
       is.character(rows$protein_ids_raw) && !anyNA(rows$protein_ids_raw), "invalid_row_identity")
  sample_names <- names(working)[-(1:2)]
  need(all(vapply(working[-(1:2)], is.numeric, logical(1))), "invalid_matrix_values")
  x <- as.matrix(working[-(1:2)])
  need(!any(is.nan(x)) && all(is.na(x) | is.finite(x)), "nonfinite_matrix_values")
  expected_masks <- c("original_blank", "original_zero", "working_NA", "imputed")
  need(is.list(masks) && !anyDuplicated(names(masks)) &&
       setequal(names(masks), expected_masks), "invalid_mask_set")
  bits <- lapply(expected_masks, function(name) {
    m <- masks[[name]]
    need(is.data.frame(m) && identical(names(m), names(working)) &&
         nrow(m)==nrow(working) && identical(m[1:2], rows) &&
         all(vapply(m[-(1:2)], is.logical, logical(1))), "mask_identity_mismatch")
    value <- as.matrix(m[-(1:2)])
    need(!anyNA(value), "invalid_mask_values")
    value
  })
  names(bits) <- expected_masks
  need(!any(bits$imputed), "unsupported_imputed_input")
  need(!any(bits$original_blank & bits$original_zero) &&
       identical(unname(bits$working_NA), unname(is.na(x))) &&
       identical(unname(bits$working_NA), unname(bits$original_blank | bits$original_zero)),
       "mask_partition_mismatch")

  # This is the already-stored positive-log2/None parent, never a raw-scale input.
  need(is.list(parent) && all(c("processed_dataset_version_id", "project_id",
       "manifest_sha256", "artifacts", "parameters") %in% names(parent)), "invalid_parent_parent")
  uuid <- function(v) is.character(v) && length(v)==1L && !is.na(v) &&
    grepl("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",v)
  sha <- function(v) is.character(v) && !anyNA(v) && all(grepl("^[0-9a-f]{64}$",v))
  need(uuid(parent$processed_dataset_version_id) && uuid(parent$project_id) &&
       length(parent$manifest_sha256)==1L && sha(parent$manifest_sha256), "invalid_parent_parent")
  p <- parent$parameters
  need(is.list(p) && identical(p$output_scale,"log2") && identical(p$log2,"positive_only") &&
       identical(p$normalization,"none") && identical(p$imputation,"none") &&
       identical(p$zero_policy,"to_NA") && identical(p$blank_policy,"to_NA") &&
       is.numeric(p$pseudocount) && length(p$pseudocount)==1L && p$pseudocount==0,
       "unsupported_parent_strategy")
  pins <- parent$artifacts
  required_artifacts <- c("working-matrix.tsv", "original-blank-mask.tsv",
    "original-zero-mask.tsv", "working-NA-mask.tsv", "imputed-mask.tsv", "parameters.json")
  need(is.data.frame(pins) && all(c("name","bytes","sha256") %in% names(pins)) &&
       is.character(pins$name) && !anyNA(pins$name) && !anyDuplicated(pins$name) &&
       all(required_artifacts %in% pins$name) && is.numeric(pins$bytes) &&
       !anyNA(pins$bytes) && all(is.finite(pins$bytes) & pins$bytes>=1 & pins$bytes<=26214400 &
       pins$bytes==floor(pins$bytes)) && sha(pins$sha256), "invalid_parent_artifact_pins")


  need(requireNamespace("limma",quietly=TRUE) && as.character(utils::packageVersion("limma"))=="3.68.5",
    "unsupported_limma_version")
  need(requireNamespace("digest",quietly=TRUE),"digest_unavailable")
  counts <- base::colSums(!is.na(x))
  need(all(counts>=1L),"all_missing_sample")
  # Actual installed normalizeQuantiles interpolates observed ranks; requires2 points.
  need(all(counts>=2L),"insufficient_observed_for_quantile_interpolation")
  y <- withCallingHandlers(limma::normalizeBetweenArrays(x,method="quantile",ties=TRUE),
    warning=function(w) stop(paste0("native_quantile_warning:",conditionMessage(w)),call.=FALSE))
  need(is.matrix(y) && identical(dim(y),dim(x)) && identical(dimnames(y),dimnames(x)) &&
    identical(is.na(y),is.na(x)) && all(is.na(y) | is.finite(y)),"native_quantile_state_changed")
  output <- working
  for (j in seq_len(ncol(y))) output[[j+2L]] <- y[,j]
  need(identical(output[1:2],rows),"row_identity_changed")
  delta <- y-x
  factors <- data.frame(sample_ordinal=seq_len(ncol(x)),sample_name_raw=sample_names,
    observed_count=as.integer(counts),missing_count=as.integer(colSums(is.na(x))),
    imputed_count=as.integer(colSums(bits$imputed)),original_blank_count=as.integer(colSums(bits$original_blank)),
    original_zero_count=as.integer(colSums(bits$original_zero)),
    input_median_log2=apply(x,2,stats::median,na.rm=TRUE),output_median_log2=apply(y,2,stats::median,na.rm=TRUE),
    minimum_change_log2=apply(delta,2,min,na.rm=TRUE),maximum_change_log2=apply(delta,2,max,na.rm=TRUE),
    scalar_factor=rep(NA_real_,ncol(x)),factor_status=rep("not_scalar_rank_transform",ncol(x)),check.names=FALSE)
  warnings <- "quantile_changes_observed_distribution_global_shift_may_be_removed"
  if(length(unique(as.integer(counts)))>1L) warnings <- c(warnings,"unequal_missingness_interpolated_quantile_curves_no_cell_imputation")
  if(anyNA(x) || any(vapply(seq_len(ncol(x)),function(j) anyDuplicated(x[!is.na(x[,j]),j])>0L,logical(1))))
    warnings <- c(warnings,"missing_or_tied_values_no_exact_empirical_distribution_equality_claim")
  if(ncol(x)==1L) warnings <- c(warnings,"single_sample_native_identity_no_crosssample_normalization")
  list(working_matrix=output,masks=masks,factors=factors,warnings=warnings,
    parameters=list(contract="limma_quantile_saved_log2_no_imputation_v1",method="quantile",
      function_name="limma::normalizeBetweenArrays",matrix_delegate="limma::normalizeQuantiles",
      limma_version="3.68.5",ties=TRUE,input_scale="saved_positive_log2_none",output_scale="log2",
      imputation="none",minimum_observed_per_sample=2L,scalar_factors_available=FALSE,
      missing_strategy="native observed quantile-curve interpolation; NA cells never assigned",
      no_manual_imputation_or_NA_restore=TRUE),
    provenance=list(parent=parent,output_rows=nrow(output),output_samples=ncol(x),
      missing_before=sum(is.na(x)),missing_after=sum(is.na(y)),imputed_before=0L,imputed_after=0L,
      R_version=R.version.string,limma_version=as.character(utils::packageVersion("limma")),
      native_function_body_sha256=digest::digest(paste(deparse(body(limma::normalizeQuantiles)),collapse="\n"),algo="sha256",serialize=FALSE),
      byte_integrity_attested=FALSE,owner_authorized=FALSE,published=FALSE),analysis_ready=FALSE)
}
# Explicit standalone adapter only. All11 immutable parent bytes checked before/after.
quantile_saved_parent <- function(input_dir,parent,output_dir) {
  need <- function(ok,code) if (!isTRUE(ok)) stop(code,call.=FALSE)
  need(requireNamespace("digest",quietly=TRUE),"digest_unavailable")
  need(!file.exists(output_dir),"output_exists")
  names11 <- c("R-session-info.txt","filter-log.tsv","imputed-mask.tsv","original-blank-mask.tsv",
    "original-zero-mask.tsv","parameters.json","raw-retained.tsv","row-map.tsv","summary.tsv",
    "working-NA-mask.tsv","working-matrix.tsv")
  pins <- parent$artifacts
  need(is.data.frame(pins) && identical(as.character(pins$name),names11) &&
    all(c("name","bytes","sha256") %in% names(pins)),"invalid_artifact_manifest")
  verify <- function() for (j in seq_len(nrow(pins))) {
    f <- file.path(input_dir,pins$name[[j]])
    need(file.exists(f) && !isTRUE(file.info(f)$isdir) && file.info(f)$size==pins$bytes[[j]] &&
      digest::digest(file=f,algo="sha256",serialize=FALSE)==pins$sha256[[j]],"input_integrity_failed")
  }
  verify()
  read <- function(n) utils::read.delim(file.path(input_dir,n),check.names=FALSE,quote="\"",comment.char="",
    colClasses="character",na.strings=character(0),strip.white=FALSE,stringsAsFactors=FALSE)
  w <- read("working-matrix.tsv");raw <- read("raw-retained.tsv")
  need(identical(dim(w),dim(raw)) && identical(names(w),names(raw)) && identical(w[1:2],raw[1:2]),"raw_identity_mismatch")
  need(all(grepl("^[1-9][0-9]*$",w$record_number)),"invalid_record_token")
  decode <- function(tab,kind) {
    tab$record_number <- as.integer(tab$record_number)
    for (j in 3:ncol(tab)) {
      t <- tab[[j]];need(!anyNA(t),"unexpected_NA_token")
      if (kind=="mask") {need(all(t %in% c("TRUE","FALSE")),"invalid_mask_token");tab[[j]] <- t=="TRUE";next}
      absent <- t==if (kind=="raw") "" else "NA"
      regex <- if(kind=="raw") "^[+]?([0-9]+(\\.[0-9]*)?|\\.[0-9]+)([eE][+-]?[0-9]+)?$" else "^[+-]?([0-9]+(\\.[0-9]*)?|\\.[0-9]+)([eE][+-]?[0-9]+)?$"
      need(all(absent | grepl(regex,t)),"invalid_numeric_token")
      v <- rep(NA_real_,length(t));v[!absent] <- as.numeric(t[!absent])
      need(all(is.finite(v[!absent])),"nonfinite_numeric_token")
      if(kind=="raw") need(!any(!absent & v==0 & grepl("[1-9]",sub("[eE].*$","",t)),na.rm=TRUE),"raw_underflow")
      tab[[j]] <- v
    };tab
  }
  w <- decode(w,"working");raw <- decode(raw,"raw")
  masks <- list(original_blank=decode(read("original-blank-mask.tsv"),"mask"),
    original_zero=decode(read("original-zero-mask.tsv"),"mask"),working_NA=decode(read("working-NA-mask.tsv"),"mask"),
    imputed=decode(read("imputed-mask.tsv"),"mask"))
  result <- quantile_saved_log2(w,masks,parent)
  need(dir.create(output_dir,mode="0700"),"output_create_failed")
  options(digits=17)
  for (n in c("working_matrix","factors")) utils::write.table(result[[n]],file.path(output_dir,paste0(gsub("_","-",n),".tsv")),
    sep="\t",quote=TRUE,row.names=FALSE,na="NA",fileEncoding="UTF-8",eol="\n")
  for (n in c("original-blank-mask.tsv","original-zero-mask.tsv","working-NA-mask.tsv","imputed-mask.tsv"))
    need(file.copy(file.path(input_dir,n),file.path(output_dir,n),overwrite=FALSE),"mask_copy_failed")
  dput(result$parameters,file=file.path(output_dir,"parameters.R.txt"))
  dput(result$warnings,file=file.path(output_dir,"warnings.R.txt"))
  saveRDS(result$provenance,file.path(output_dir,"provenance.rds"),compress=FALSE)
  writeLines(capture.output(utils::sessionInfo()),file.path(output_dir,"R-session-info.txt"),useBytes=TRUE)
  verify();result
}
