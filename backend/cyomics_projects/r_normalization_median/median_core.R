# Source-only scientific core. No filesystem, auth, publication, runtime or plots.
# A trusted future adapter verifies saved artifact bytes against parent pins first.

median_center_log2 <- function(working, masks, provenance, method="median") {
  need <- function(ok, code) if (!isTRUE(ok)) stop(code, call.=FALSE)
  need(is.character(method) && length(method)==1L && !is.na(method) &&
       method %in% c("none", "median"), "unsupported_normalization")
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
  need(is.list(provenance) && all(c("processed_dataset_version_id", "project_id",
       "manifest_sha256", "artifacts", "parameters") %in% names(provenance)), "invalid_parent_provenance")
  uuid <- function(v) is.character(v) && length(v)==1L && !is.na(v) &&
    grepl("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",v)
  sha <- function(v) is.character(v) && !anyNA(v) && all(grepl("^[0-9a-f]{64}$",v))
  need(uuid(provenance$processed_dataset_version_id) && uuid(provenance$project_id) &&
       length(provenance$manifest_sha256)==1L && sha(provenance$manifest_sha256), "invalid_parent_provenance")
  p <- provenance$parameters
  need(is.list(p) && identical(p$output_scale,"log2") && identical(p$log2,"positive_only") &&
       identical(p$normalization,"none") && identical(p$imputation,"none") &&
       identical(p$zero_policy,"to_NA") && identical(p$blank_policy,"to_NA") &&
       is.numeric(p$pseudocount) && length(p$pseudocount)==1L && p$pseudocount==0,
       "unsupported_parent_strategy")
  pins <- provenance$artifacts
  required_artifacts <- c("working-matrix.tsv", "original-blank-mask.tsv",
    "original-zero-mask.tsv", "working-NA-mask.tsv", "imputed-mask.tsv", "parameters.json")
  need(is.data.frame(pins) && all(c("name","bytes","sha256") %in% names(pins)) &&
       is.character(pins$name) && !anyNA(pins$name) && !anyDuplicated(pins$name) &&
       all(required_artifacts %in% pins$name) && is.numeric(pins$bytes) &&
       !anyNA(pins$bytes) && all(is.finite(pins$bytes) & pins$bytes>=1 & pins$bytes<=26214400 &
       pins$bytes==floor(pins$bytes)) && sha(pins$sha256), "invalid_parent_artifact_pins")

  observed <- !is.na(x) & !bits$imputed
  counts <- colSums(observed)
  medians <- vapply(seq_len(ncol(x)), function(j)
    if (counts[[j]]) stats::median(x[observed[,j],j]) else NA_real_, numeric(1))
  reference <- NA_real_
  offsets <- rep(0, ncol(x))
  output <- working
  if (method=="median") {
    need(all(counts>=1), "insufficient_observed_values")
    reference <- stats::median(medians) # equal weight per sample, not pooled cells
    offsets <- reference-medians
    need(is.finite(reference) && all(is.finite(offsets)), "nonfinite_normalization_factor")
    for (j in seq_len(ncol(x))) {
      output[[j+2L]] <- x[,j]+offsets[[j]]
      need(all(is.na(output[[j+2L]]) | is.finite(output[[j+2L]])), "nonfinite_normalized_values")
    }
  }
  y <- as.matrix(output[-(1:2)])
  need(identical(is.na(y), is.na(x)) && identical(output[1:2], rows), "normalization_state_changed")
  factors <- data.frame(sample_ordinal=seq_len(ncol(x)), sample_name_raw=sample_names,
    observed_count=as.integer(counts), missing_count=as.integer(colSums(is.na(x))),
    imputed_count=as.integer(colSums(bits$imputed)),
    original_blank_count=as.integer(colSums(bits$original_blank)),
    original_zero_count=as.integer(colSums(bits$original_zero)),
    input_median_log2=medians, reference_log2=rep(reference,ncol(x)),
    additive_offset_log2=offsets,
    output_median_log2=vapply(seq_len(ncol(y)), function(j)
      if (counts[[j]]) stats::median(y[observed[,j],j]) else NA_real_, numeric(1)),
    check.names=FALSE)
  list(working_matrix=output, masks=masks, factors=factors,
    parameters=list(contract="median_observed_log2_v1", method=method,
      input_scale="log2", output_scale="log2", imputation="none",
      formula=if (method=="median") "m_j=median(observed_finite_x_ij); r=median_j(m_j); y_ij=x_ij+(r-m_j)" else "y_ij=x_ij",
      reference_rule=if (method=="median") "equal_weight_median_of_sample_medians" else "not_applied",
      minimum_observed_per_sample=if (method=="median") 1L else 0L,
      operations=c("consume_complete_saved_positive_log2_none_parent", paste0("normalization_",method), "preserve_NA_no_imputation")),
    provenance=list(parent=provenance, output_rows=nrow(output), output_samples=ncol(x),
      row_identity=rows, sample_names_raw=sample_names,
      missing_before=sum(is.na(x)), missing_after=sum(is.na(y)),
      imputed_before=sum(bits$imputed), imputed_after=sum(bits$imputed),
      R_version=R.version.string, median_implementation="stats::median",
      stats_version=as.character(utils::packageVersion("stats")),
      byte_integrity_attested=FALSE, owner_authorized=FALSE, published=FALSE,
      output_artifact_pins=NULL),
    warnings=if (method=="median" && any(counts<2)) "sample_with_single_observation" else character(0),
    analysis_ready=FALSE)
}
