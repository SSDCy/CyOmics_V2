# Independent source-only preprocessor: base/utils/stats only, no plots or jobs.
# Trusted future worker supplies fixed paths and explicit strategy literals.

safe_codes <- c("invalid_arguments", "invalid_boolean", "unsupported_family", "unsupported_strategy",
  "input_missing_or_size", "input_byte_integrity", "invalid_utf8", "input_read_warning",
  "ragged_or_empty_table", "input_dimension_integrity", "duplicate_column_names", "source_shape_limit",
  "required_protein_ids", "missing_sample_columns", "selected_schema", "selected_binding",
  "missing_enabled_flag", "invalid_flag_token", "invalid_nonnegative_numeric_token",
  "numeric_out_of_range", "numeric_underflow", "input_changed", "output_already_exists",
  "output_creation_failed", "unexpected_warning", "internal_partition", "preprocess_unconfirmed")
fail <- function(code) stop(code, call.=FALSE)

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
  if (!identical(args[7:10], c("preserve", "none", "none", "none"))) fail("unsupported_strategy")
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
  if (any(!blank & !valid)) fail("invalid_nonnegative_numeric_token")
  numeric <- matrix(NA_real_, nrow(raw), ncol(raw), dimnames=dimnames(raw))
  numeric[!blank] <- as.numeric(raw[!blank])
  if (any(!is.finite(numeric[!blank]))) fail("numeric_out_of_range")
  mantissa <- sub("[eE].*$", "", raw); dim(mantissa) <- dim(raw)
  if (any(!blank & numeric == 0 & grepl("[1-9]", mantissa), na.rm=TRUE)) fail("numeric_underflow")
  zero <- !blank & numeric == 0; zero[is.na(zero)] <- FALSE
  positive <- !blank & numeric > 0; positive[is.na(positive)] <- FALSE
  if (any(blank & zero) || any(blank & positive) || any(zero & positive) ||
      sum(blank)+sum(zero)+sum(positive) != length(raw)) fail("internal_partition")

  record_numbers <- seq_len(nrow(tab)); ids <- tab[["Protein IDs"]]
  row_ordinals <- rep(NA_integer_, nrow(tab)); row_ordinals[keep] <- seq_along(keep)
  log <- data.frame(record_number=record_numbers, protein_ids_raw=ids,
    excluded_reverse=causes[,1L], excluded_contaminant=causes[,2L], excluded_site_only=causes[,3L],
    excluded=excluded, reason_codes=reasons, check.names=FALSE)
  rowmap <- data.frame(source_record_number=record_numbers, protein_ids_raw=ids,
    kept=!excluded, work_row_ordinal=row_ordinals, check.names=FALSE)
  status <- if (length(keep)) "complete_identity_filtered" else "empty_not_analyzable"
  summary <- data.frame(status=status, input_rows=nrow(tab), input_columns=ncol(tab),
    quant_family=family, selected_columns=ncol(raw), input_selected_cells=length(raw),
    kept_rows=length(keep), excluded_rows=sum(excluded),
    excluded_reverse=sum(causes[,1L]), excluded_contaminant=sum(causes[,2L]),
    excluded_site_only=sum(causes[,3L]), overlap_excluded_rows=sum(rowSums(causes)>1L),
    source_blank_cells=sum(blank), source_zero_cells=sum(zero), source_positive_cells=sum(positive),
    retained_selected_cells=length(keep)*ncol(raw), retained_blank_cells=sum(blank[keep,,drop=FALSE]),
    retained_zero_cells=sum(zero[keep,,drop=FALSE]), retained_positive_cells=sum(positive[keep,,drop=FALSE]),
    working_NA_cells=sum(is.na(numeric[keep,,drop=FALSE])), imputed_cells=0L,
    reverse_enabled=enabled[[1L]], contaminant_enabled=enabled[[2L]], site_only_enabled=enabled[[3L]],
    zero_policy="preserve", log2="none", normalization="none", imputation="none", check.names=FALSE)
  verify_unchanged(input, original$snapshot); verify_unchanged(selected_path, descriptors$snapshot)
  if (!dir.create(output, recursive=FALSE, mode="0700")) fail("output_creation_failed")
  write <- function(value, name) utils::write.table(value, file=file.path(output,name), sep="\t",
    quote=TRUE, row.names=FALSE, col.names=TRUE, na="NA", qmethod="double", eol="\n", fileEncoding="UTF-8")
  write(log, "filter-log.tsv"); write(rowmap, "row-map.tsv"); write(summary, "summary.tsv")
  writeLines(utils::capture.output(utils::sessionInfo()), file.path(output,"R-session-info.txt"), useBytes=TRUE)
  if (length(keep)) {
    prefix <- data.frame(record_number=keep, protein_ids_raw=ids[keep], check.names=FALSE)
    retained_raw <- raw[keep,,drop=FALSE]; retained_numeric <- numeric[keep,,drop=FALSE]
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
    cat("PREPROCESS_FLAGS_EMPTY\n")
    return(3L)
  }
  cat("PREPROCESS_FLAGS_COMPLETE\n")
  0L
}

code <- tryCatch(withCallingHandlers(main(commandArgs(trailingOnly=TRUE)),
  warning=function(w) fail("unexpected_warning")), error=function(e) {
    message <- conditionMessage(e)
    if (!message %in% safe_codes) message <- "preprocess_unconfirmed"
    cat(paste0("PREPROCESS_FLAGS_ERROR:",message,"\n"),file=stderr())
    2L
  })
quit(status=code,save="no",runLast=FALSE)
