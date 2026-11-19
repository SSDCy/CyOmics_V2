# Inert source: standard base::colSums/stats::median/base::log2 on saved None data.
# A future trusted adapter verifies Owner/schema; this core never grants authority.
total_intensity_log2 <- function(raw, working, masks, parent) {
  need <- function(ok,code) if (!isTRUE(ok)) stop(code,call.=FALSE)
  need(is.data.frame(working) && nrow(working)>=1L && nrow(working)<=10000L &&
    ncol(working)>=3L && ncol(working)<=512L && nrow(working)*(ncol(working)-2L)<=2000000L,
    "invalid_shape")
  need(identical(names(working)[1:2],c("record_number","protein_ids_raw")) &&
    !anyNA(names(working)) && all(nzchar(names(working))) && !anyDuplicated(names(working)),"invalid_columns")
  ids <- working[1:2]; rn <- ids$record_number
  need(is.numeric(rn) && !anyNA(rn) && all(is.finite(rn) & rn>=1 & rn<=10000 & rn==floor(rn)) &&
    !anyDuplicated(rn) && !is.unsorted(rn,strictly=TRUE) && is.character(ids$protein_ids_raw) &&
    !anyNA(ids$protein_ids_raw),"invalid_rows")
  need(is.data.frame(raw) && identical(names(raw),names(working)) && identical(dim(raw),dim(working)) &&
    identical(raw[1:2],ids) && all(vapply(raw[-(1:2)],is.numeric,logical(1))) &&
    all(vapply(working[-(1:2)],is.numeric,logical(1))),"raw_identity_mismatch")
  x <- as.matrix(working[-(1:2)]); r <- as.matrix(raw[-(1:2)])
  need(!any(is.nan(r)) && !any(is.nan(x)) && all(is.na(r) | (is.finite(r) & r>=0)) &&
    all(is.na(x) | is.finite(x)),"invalid_numeric_values")
  expected <- c("original_blank","original_zero","working_NA","imputed")
  need(is.list(masks) && !anyDuplicated(names(masks)) && setequal(names(masks),expected),"invalid_masks")
  bits <- lapply(expected,function(n) {
    m <- masks[[n]]
    need(is.data.frame(m) && identical(names(m),names(working)) && identical(dim(m),dim(working)) &&
      identical(m[1:2],ids) && all(vapply(m[-(1:2)],is.logical,logical(1))),"mask_identity_mismatch")
    b <- as.matrix(m[-(1:2)]);need(!anyNA(b),"invalid_mask_values");b
  });names(bits) <- expected
  zero <- !is.na(r) & r==0
  need(!any(bits$imputed) && identical(unname(bits$original_blank),unname(is.na(r))) &&
    identical(unname(bits$original_zero),unname(zero)) &&
    identical(unname(bits$working_NA),unname(is.na(r) | zero)) &&
    identical(unname(bits$working_NA),unname(is.na(x))),"mask_partition_mismatch")
  observed <- !is.na(x)
  need(all(abs(x[observed]-base::log2(r[observed]))<=1e-12*pmax(1,abs(x[observed]))),"raw_log2_mismatch")
  p <- parent$parameters
  need(is.list(parent) && is.list(p) && identical(p$normalization,"none") && identical(p$imputation,"none") &&
    identical(p$output_scale,"log2") && identical(p$input_scale,"raw_nonnegative") &&
    identical(p$log2,"positive_only") && identical(p$zero_policy,"to_NA") && identical(p$blank_policy,"to_NA") &&
    is.numeric(p$pseudocount) && length(p$pseudocount)==1L && !is.na(p$pseudocount) && p$pseudocount==0,
    "unsupported_parent")
  uuid <- function(v) is.character(v) && length(v)==1L && !is.na(v) && grepl("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",v)
  need(uuid(parent$processed_dataset_version_id) && uuid(parent$project_id) &&
    is.character(parent$manifest_sha256) && length(parent$manifest_sha256)==1L && !is.na(parent$manifest_sha256) &&
    grepl("^[0-9a-f]{64}$",parent$manifest_sha256),"invalid_provenance")
  counts <- base::colSums(observed)
  need(all(counts>=1L),"insufficient_observed_values")
  positive <- r;positive[!observed] <- NA_real_
  totals <- base::colSums(positive,na.rm=TRUE)
  need(all(is.finite(totals) & totals>0),"invalid_sample_total")
  reference <- stats::median(totals)
  factors <- reference/totals;offsets <- base::log2(factors)
  need(is.finite(reference) && reference>0 && all(is.finite(factors) & factors>0) && all(is.finite(offsets)),
    "nonfinite_normalization_factor")
  output <- working
  for (j in seq_len(ncol(x))) output[[j+2L]] <- x[,j]+offsets[[j]]
  y <- as.matrix(output[-(1:2)])
  need(all(is.na(y) | is.finite(y)) && identical(is.na(y),is.na(x)) && identical(output[1:2],ids),"output_state_changed")
  summary <- data.frame(sample_ordinal=seq_len(ncol(x)),sample_name_raw=names(working)[-(1:2)],
    observed_count=as.integer(counts),missing_count=as.integer(colSums(is.na(x))),imputed_count=as.integer(colSums(bits$imputed)),
    original_blank_count=as.integer(colSums(bits$original_blank)),original_zero_count=as.integer(colSums(bits$original_zero)),
    input_total_raw=totals,reference_total_raw=rep(reference,ncol(x)),multiplicative_factor_raw=factors,
    additive_offset_log2=offsets,output_total_raw=totals*factors,check.names=FALSE)
  warnings <- character(0)
  if (length(unique(as.integer(counts)))>1L) warnings <- c(warnings,"unequal_observed_counts_missingness_bias_not_corrected")
  if (any(counts<2L)) warnings <- c(warnings,"sample_with_single_observation")
  list(working_matrix=output,masks=masks,factors=summary,warnings=warnings,
    parameters=list(contract="total_intensity_saved_raw_sum_v1",method="total",input_scale="saved_log2_none",
      output_scale="log2",imputation="none",reference_rule="median_of_sample_positive_observed_raw_totals",
      formula="T_j=colSums(raw_positive_observed,na.rm=TRUE); R=median(T_j); f_j=R/T_j; y_ij=x_ij+log2(f_j)",
      pseudocount=0,minimum_observed_per_sample=1L),
    provenance=list(parent=parent,output_rows=nrow(output),output_samples=ncol(x),
      missing_before=sum(is.na(x)),missing_after=sum(is.na(y)),imputed_before=0L,imputed_after=0L,
      R_version=R.version.string,stats_version=as.character(utils::packageVersion("stats")),
      implementations=c("base::colSums","stats::median","base::log2"),
      byte_integrity_attested=FALSE,owner_authorized=FALSE,published=FALSE),analysis_ready=FALSE)
}

# Explicit standalone adapter only. All11 immutable parent bytes checked before/after.
total_saved_parent <- function(input_dir,parent,output_dir) {
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
  result <- total_intensity_log2(raw,w,masks,parent)
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
