# Independent unregistered positive-log2 producer candidate: base/utils only; no plots/jobs.
# Trusted future worker supplies fixed paths and explicit strategy literals.

safe_codes <- c("invalid_arguments", "invalid_boolean", "unsupported_family", "unsupported_strategy",
  "input_missing_or_size", "input_byte_integrity", "invalid_utf8", "input_read_warning",
  "ragged_or_empty_table", "input_dimension_integrity", "duplicate_column_names", "source_shape_limit",
  "required_protein_ids", "missing_sample_columns", "selected_schema", "selected_binding",
  "missing_enabled_flag", "invalid_flag_token", "invalid_nonnegative_numeric_token",
  "numeric_out_of_range", "numeric_underflow", "input_changed", "output_already_exists",
  "output_creation_failed", "unexpected_warning", "internal_partition", "log2_nonfinite_cell", "preprocess_unconfirmed")
failure_location <- c(record_number=-1L, column_ordinal=-1L)
fail <- function(code) stop(code, call.=FALSE)
fail_cell <- function(mask, code, indices) {
  first <- which(mask, arr.ind=TRUE)[1L,,drop=TRUE]
  failure_location <<- c(record_number=as.integer(first[[1L]]),
    column_ordinal=as.integer(indices[[first[[2L]]]]-1L))
  fail(code)
}


snapshot_bytes <- function(path, cap) {
  info <- file.info(path)
  if (nrow(info) != 1L || is.na(info$size) || is.na(info$isdir) || info$isdir ||
      info$size < 1 || info$size > cap) fail("input_missing_or_size")
  value <- readBin(path, what="raw", n=as.integer(info$size)+1L)
  if (length(value) != info$size || any(value == as.raw(0L))) fail("input_byte_integrity")
  decoded <- rawToChar(value)
  if (!validUTF8(decoded)) fail("invalid_utf8")
  list(bytes=value, size=info$size)
}

verify_unchanged <- function(path, snapshot) {
  info <- file.info(path)
  if (nrow(info) != 1L || is.na(info$size) || is.na(info$isdir) || info$isdir ||
      info$size != snapshot$size) fail("input_changed")
  value <- readBin(path, what="raw", n=as.integer(snapshot$size)+1L)
  if (!identical(value, snapshot$bytes)) fail("input_changed")
  invisible(NULL)
}

read_exact_tsv <- function(path, cap) {
  snapshot <- snapshot_bytes(path, cap)
  widths <- withCallingHandlers(utils::count.fields(path, sep="\t", quote="", comment.char="",
      blank.lines.skip=FALSE), warning=function(w) fail("input_read_warning"))
  if (length(widths) < 2L || anyNA(widths) || any(widths != widths[[1L]])) fail("ragged_or_empty_table")
  if (widths[[1L]] < 2L || widths[[1L]] > 512L || length(widths)-1L > 10000L ||
      widths[[1L]]*(length(widths)-1L) > 2000000L) fail("source_shape_limit")
  tab <- withCallingHandlers(utils::read.delim(path, header=TRUE, sep="\t", quote="", comment.char="",
      check.names=FALSE, colClasses="character", na.strings=character(0), fill=FALSE,
      blank.lines.skip=FALSE, strip.white=FALSE, row.names=NULL, fileEncoding="UTF-8-BOM"),
    warning=function(w) {
      if (grepl("^incomplete final line found by readTableHeader", conditionMessage(w))) invokeRestart("muffleWarning")
      else fail("input_read_warning")
    })
  if (nrow(tab) != length(widths)-1L || ncol(tab) != widths[[1L]] || anyNA(tab)) fail("input_dimension_integrity")
  if (anyDuplicated(names(tab))) fail("duplicate_column_names")
  verify_unchanged(path, snapshot)
  list(table=tab, snapshot=snapshot)
}

main <- function(args) {
  if (length(args) != 11L) fail("invalid_arguments")
  input <- args[[1L]]; selected_path <- args[[2L]]; family <- args[[3L]]
  boolean_args <- args[4:6]
  if (any(!boolean_args %in% c("true", "false"))) fail("invalid_boolean")
  enabled <- boolean_args == "true"
  if (!family %in% c("Intensity", "LFQ intensity")) fail("unsupported_family")
  if (!identical(args[7:10], c("to_NA", "positive_only", "none", "none"))) fail("unsupported_strategy")
  output <- args[[11L]]
  if (file.exists(output) || dir.exists(output)) fail("output_already_exists")

  original <- read_exact_tsv(input, 26214400)
  descriptors <- read_exact_tsv(selected_path, 131072)
  tab <- original$table; selected <- descriptors$table
  if (nrow(tab) < 1L || nrow(tab) > 10000L || ncol(tab) < 2L || ncol(tab) > 512L ||
      nrow(tab)*ncol(tab) > 2000000L) fail("source_shape_limit")
  if (sum(names(tab) == "Protein IDs") != 1L) fail("required_protein_ids")
  indices <- which(startsWith(names(tab), paste0(family, " ")))
  if (!length(indices) || any(nchar(substring(names(tab)[indices], nchar(family)+2L)) == 0L)) fail("missing_sample_columns")
  if (!identical(names(selected), c("column_ordinal", "name_raw", "quant_family", "sample_label_raw")) ||
      nrow(selected) != length(indices)) fail("selected_schema")
  ordinal_text <- selected[["column_ordinal"]]
  if (any(nchar(ordinal_text) > 3L) || any(!grepl("^(0|[1-9][0-9]*)$", ordinal_text))) fail("selected_schema")
  ordinals <- as.integer(ordinal_text)
  if (!identical(ordinals, indices-1L) || !identical(selected[["name_raw"]], names(tab)[indices]) ||
      any(selected[["quant_family"]] != family) ||
      !identical(selected[["sample_label_raw"]], substring(names(tab)[indices], nchar(family)+2L))) fail("selected_binding")

  flag_names <- c("Reverse", "Potential contaminant", "Only identified by site")
  reason_names <- c("reverse", "potential_contaminant", "site_only")
  causes <- matrix(FALSE, nrow(tab), 3L, dimnames=list(NULL, reason_names))
  for (j in seq_along(flag_names)) {
    present <- flag_names[[j]] %in% names(tab)
    if (!present && enabled[[j]]) fail("missing_enabled_flag")
    if (present) {
      flag <- tab[[flag_names[[j]]]]
      # Validate even a present disabled flag: never silently reinterpret junk.
      if (any(!flag %in% c("", "+"))) fail("invalid_flag_token")
      causes[,j] <- enabled[[j]] & flag == "+"
    }
  }
  excluded <- rowSums(causes) > 0L
  keep <- which(!excluded)
  reasons <- vapply(seq_len(nrow(tab)), function(i) paste(reason_names[causes[i,]], collapse="|"), character(1))

  raw <- as.matrix(tab[,indices,drop=FALSE]); blank <- raw == ""
  valid <- grepl("^[+]?(?:[0-9]+(?:\\.[0-9]*)?|\\.[0-9]+)(?:[eE][+-]?[0-9]+)?$", raw, perl=TRUE)
  dim(valid) <- dim(raw)
  if (any(!blank & !valid)) fail_cell(!blank & !valid, "invalid_nonnegative_numeric_token", indices)
  numeric <- matrix(NA_real_, nrow(raw), ncol(raw), dimnames=dimnames(raw))
  numeric[!blank] <- as.numeric(raw[!blank])
  if (any(!is.finite(numeric[!blank]))) fail_cell(!blank & !is.finite(numeric), "numeric_out_of_range", indices)
  mantissa <- sub("[eE].*$", "", raw); dim(mantissa) <- dim(raw)
  underflow <- !blank & numeric == 0 & grepl("[1-9]", mantissa); underflow[is.na(underflow)] <- FALSE
  if (any(underflow)) fail_cell(underflow, "numeric_underflow", indices)
  zero <- !blank & numeric == 0; zero[is.na(zero)] <- FALSE
  positive <- !blank & numeric > 0; positive[is.na(positive)] <- FALSE
  if (any(blank & zero) || any(blank & positive) || any(zero & positive) ||
      sum(blank)+sum(zero)+sum(positive) != length(raw)) fail("internal_partition")

  # Keep original raw tokens/masks; only this separate derived matrix is log2.
  working <- matrix(NA_real_, nrow(raw), ncol(raw), dimnames=dimnames(raw))
  working[positive] <- base::log2(numeric[positive])
  if (any(!is.finite(working[positive]))) fail_cell(positive & !is.finite(working), "log2_nonfinite_cell", indices)

  record_numbers <- seq_len(nrow(tab)); ids <- tab[["Protein IDs"]]
  row_ordinals <- rep(NA_integer_, nrow(tab)); row_ordinals[keep] <- seq_along(keep)
  log <- data.frame(record_number=record_numbers, protein_ids_raw=ids,
    excluded_reverse=causes[,1L], excluded_contaminant=causes[,2L], excluded_site_only=causes[,3L],
    excluded=excluded, reason_codes=reasons, check.names=FALSE)
  rowmap <- data.frame(source_record_number=record_numbers, protein_ids_raw=ids,
    kept=!excluded, work_row_ordinal=row_ordinals, check.names=FALSE)
  status <- if (length(keep)) "complete_positive_log2" else "empty_not_analyzable"
  summary <- data.frame(status=status, input_rows=nrow(tab), input_columns=ncol(tab),
    quant_family=family, selected_columns=ncol(raw), input_selected_cells=length(raw),
    kept_rows=length(keep), excluded_rows=sum(excluded),
    excluded_reverse=sum(causes[,1L]), excluded_contaminant=sum(causes[,2L]),
    excluded_site_only=sum(causes[,3L]), overlap_excluded_rows=sum(rowSums(causes)>1L),
    source_blank_cells=sum(blank), source_zero_cells=sum(zero), source_positive_cells=sum(positive),
    retained_selected_cells=length(keep)*ncol(raw), retained_blank_cells=sum(blank[keep,,drop=FALSE]),
    retained_zero_cells=sum(zero[keep,,drop=FALSE]), retained_positive_cells=sum(positive[keep,,drop=FALSE]),
    working_NA_cells=sum(is.na(working[keep,,drop=FALSE])), imputed_cells=0L,
    reverse_enabled=enabled[[1L]], contaminant_enabled=enabled[[2L]], site_only_enabled=enabled[[3L]],
    zero_policy="to_NA", blank_policy="to_NA", log2="positive_only", pseudocount=0L,
    normalization="none", imputation="none", analysis_ready=FALSE, check.names=FALSE)
  # Exact canonical JSON map of the sealed quant-diff preset; no trailing LF.
  parameters <- paste0('{"blank_policy":"to_NA","filter_flags":{"contaminant":',boolean_args[[2L]],
    ',"reverse":',boolean_args[[1L]],',"site_only":',boolean_args[[3L]],
    '},"imputation":"none","input_scale":"raw_nonnegative","log2":"positive_only",',
    '"normalization":"none","output_scale":"log2","preset":"positive_log2_zero_na_no_impute_v1",',
    '"pseudocount":0,"seed":null,"steps":["flags_filter","zero_and_blank_to_NA","log2_positive",',
    '"normalization_none","imputation_none"],"zero_policy":"to_NA"}')
  verify_unchanged(input, original$snapshot); verify_unchanged(selected_path, descriptors$snapshot)
  if (!dir.create(output, recursive=FALSE, mode="0700")) fail("output_creation_failed")
  write <- function(value, name) utils::write.table(value, file=file.path(output,name), sep="\t",
    quote=TRUE, row.names=FALSE, col.names=TRUE, na="NA", qmethod="double", eol="\n", fileEncoding="UTF-8")
  write(log, "filter-log.tsv"); write(rowmap, "row-map.tsv"); write(summary, "summary.tsv")
  cat(parameters, file=file.path(output,"parameters.json"), sep="")
  writeLines(utils::capture.output(utils::sessionInfo()), file.path(output,"R-session-info.txt"), useBytes=TRUE)
  if (length(keep)) {
    prefix <- data.frame(record_number=keep, protein_ids_raw=ids[keep], check.names=FALSE)
    retained_raw <- raw[keep,,drop=FALSE]; retained_numeric <- working[keep,,drop=FALSE]
    rendered <- matrix("NA", nrow(retained_numeric), ncol(retained_numeric), dimnames=dimnames(retained_numeric))
    rendered[!is.na(retained_numeric)] <- sprintf("%.17g",retained_numeric[!is.na(retained_numeric)])
    write(cbind(prefix,as.data.frame(retained_raw,check.names=FALSE)),"raw-retained.tsv")
    write(cbind(prefix,as.data.frame(rendered,check.names=FALSE)),"working-matrix.tsv")
    write(cbind(prefix,as.data.frame(blank[keep,,drop=FALSE],check.names=FALSE)),"original-blank-mask.tsv")
    write(cbind(prefix,as.data.frame(zero[keep,,drop=FALSE],check.names=FALSE)),"original-zero-mask.tsv")
    write(cbind(prefix,as.data.frame(is.na(retained_numeric),check.names=FALSE)),"working-NA-mask.tsv")
    imputed <- matrix(FALSE,length(keep),ncol(raw),dimnames=dimnames(retained_raw))
    write(cbind(prefix,as.data.frame(imputed,check.names=FALSE)),"imputed-mask.tsv")
  }
  verify_unchanged(input, original$snapshot); verify_unchanged(selected_path, descriptors$snapshot)
  if (!length(keep)) {
    cat("LOG2_MATRIX_EMPTY\n")
    return(3L)
  }
  cat("LOG2_MATRIX_COMPLETE\n")
  0L
}

code <- tryCatch(withCallingHandlers(main(commandArgs(trailingOnly=TRUE)),
  warning=function(w) fail("unexpected_warning")), error=function(e) {
    message <- conditionMessage(e)
    if (!message %in% safe_codes) message <- "preprocess_unconfirmed"
    cat(paste0("LOG2_MATRIX_ERROR:",message,":record=",failure_location[["record_number"]],
      ":column=",failure_location[["column_ordinal"]],"\n"),file=stderr())
    2L
  })
quit(status=code,save="no",runLast=FALSE)
