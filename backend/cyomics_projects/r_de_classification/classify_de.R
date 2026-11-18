# DE-CLASSIFICATION-CORE-001: threshold membership only, no statistical fitting.
# Five trusted Root/server paths: verified results/method/test-set, explicit
# parameters and a private empty staging directory. Paths confer no authority.
main <- function() {
  args <- commandArgs(trailingOnly = TRUE)
  if (length(args) != 5L) stop("invalid_arguments", call. = FALSE)
  need <- function(ok) {
    if (!isTRUE(ok)) stop("invalid_classification_input", call. = FALSE)
  }
  read_tsv <- function(path, header, cap, max_rows) {
    con <- file(path, open = "rb")
    on.exit(close(con))
    bytes <- readBin(con, what = "raw", n = cap + 1L)
    need(length(bytes) > 0L && length(bytes) <= cap &&
      identical(bytes[length(bytes)], as.raw(10L)) &&
      !any(bytes %in% as.raw(c(0L, 13L))) &&
      !identical(bytes[seq_len(min(3L, length(bytes)))], as.raw(c(239L, 187L, 191L))))
    text <- rawToChar(bytes)
    Encoding(text) <- "UTF-8"
    need(validUTF8(text))
    lines <- strsplit(text, "\n", fixed = TRUE)[[1L]]
    need(length(lines) >= 1L && length(lines) <= max_rows + 1L)
    # Sentinel preserves trailing empty fields; no quoting, comments, trimming
    # or automatic numeric/NA conversion can change original statistic tokens.
    fields <- lapply(lines, function(line) {
      x <- strsplit(paste0(line, "\tEND"), "\t", fixed = TRUE)[[1L]]
      x[-length(x)]
    })
    need(identical(fields[[1L]], header) && all(lengths(fields) == length(header)))
    data <- matrix(unlist(fields[-1L], use.names = FALSE),
      nrow = length(lines) - 1L, ncol = length(header), byrow = TRUE)
    colnames(data) <- header
    list(data = data, lines = lines, bytes = bytes)
  }
  number <- function(tokens) {
    need(all(grepl("^[+-]?([0-9]+(\\.[0-9]*)?|\\.[0-9]+)([eE][+-]?[0-9]+)?$", tokens)))
    values <- suppressWarnings(as.numeric(tokens))
    need(all(is.finite(values)))
    values
  }
  integer_token <- function(tokens) {
    need(all(grepl("^(0|[1-9][0-9]*)$", tokens)))
    values <- number(tokens)
    need(all(values <= 9007199254740991))
    values
  }
  result_header <- c("source_row_ordinal", "protein_ids_raw", "contrast_id",
    "test_status", "reason", "log2FC", "FC", "FC_reason", "AveExpr",
    "moderated_t", "df_residual", "df_total", "P.Value", "adj.P.Val",
    "n_obs_num", "n_obs_den", "n_fit_num", "n_fit_den")
  audit_keys <- c("schema_version", "output_status", "input_scale",
    "upstream_zero_blank", "pseudocount", "normalization", "imputation",
    "input_rows", "input_samples", "numerator", "denominator", "contrast_id",
    "eligible_before_fit", "bh_m", "bh_method", "bh_n", "bh_scope",
    "lmFit_method", "lmFit_ndups", "lmFit_spacing", "lmFit_weights", "lmFit_block",
    "lmFit_correlation", "contrasts_fit_coefficients", "eBayes_proportion",
    "eBayes_stdev_coef_lim", "eBayes_trend", "eBayes_span", "eBayes_robust",
    "eBayes_winsor_tail_p", "eBayes_legacy", "seed", "FC_definition",
    "warning_count", "warning_codes", "R_version", "limma_version", "statmod_version")
  parameters <- read_tsv(args[4L], c("key", "value"), 4096L, 4L)
  parameter_keys <- c("schema_version", "input_scale", "adjusted_p_max", "absolute_log2fc_min")
  need(identical(parameters$data[, "key"], parameter_keys))
  p <- parameters$data[, "value"]
  names(p) <- parameters$data[, "key"]
  need(p[["schema_version"]] == "de_classification_v0.1" && p[["input_scale"]] == "log2")
  adjusted_p_max <- number(p[["adjusted_p_max"]])
  absolute_log2fc_min <- number(p[["absolute_log2fc_min"]])
  need(adjusted_p_max > 0 && adjusted_p_max <= 1 && absolute_log2fc_min >= 0)

  source_method <- read_tsv(args[2L], c("key", "value"), 32768L, length(audit_keys))
  need(identical(source_method$data[, "key"], audit_keys))
  a <- source_method$data[, "value"]
  names(a) <- source_method$data[, "key"]
  need(a[["schema_version"]] == "limma_core_v0.1" && a[["input_scale"]] == "log2" &&
    a[["normalization"]] == "none" && a[["imputation"]] == "none" &&
    a[["numerator"]] == "g1" && a[["denominator"]] == "g2" &&
    a[["bh_method"]] == "BH" &&
    a[["bh_scope"]] == "all_final_testable_finite_P_for_one_contrast_before_DEP_thresholds")
  warnings <- integer_token(a[["warning_count"]])
  expected_warning_codes <- if (warnings > 0) "limma_warning_review_required" else ""
  need(a[["warning_codes"]] == expected_warning_codes)
  need(a[["output_status"]] %in% c("COMPUTED_CANDIDATE", "NO_TESTABLE_ROWS",
    "REVIEW_REQUIRED_LIMMA_WARNING"))

  source <- read_tsv(args[1L], result_header, 26214400L, 10000L)
  test_set <- read_tsv(args[3L], c("source_row_ordinal", "included_in_BH",
    "P.Value", "test_status", "reason"), 26214400L, 10000L)
  r <- source$data
  t <- test_set$data
  n <- nrow(r)
  need(n > 0L && nrow(t) == n && a[["input_rows"]] == as.character(n))
  ordinal <- integer_token(r[, "source_row_ordinal"])
  need(all(ordinal > 0) && !is.unsorted(ordinal, strictly = TRUE))
  need(all(r[, "contrast_id"] == a[["contrast_id"]]) &&
    grepl("^[A-Za-z0-9_-]{1,64}$", a[["contrast_id"]]) &&
    all(r[, "test_status"] %in% c("testable", "not_testable")))
  tested <- r[, "test_status"] == "testable"
  need(identical(t[, "source_row_ordinal"], r[, "source_row_ordinal"]) &&
    identical(t[, "P.Value"], r[, "P.Value"]) &&
    identical(t[, "test_status"], r[, "test_status"]) &&
    identical(t[, "reason"], r[, "reason"]) &&
    identical(t[, "included_in_BH"], ifelse(tested, "TRUE", "FALSE")))
  need(a[["bh_m"]] == as.character(sum(tested)) && a[["bh_n"]] == a[["bh_m"]])
  need(all(r[!tested, "log2FC"] == "NA") && all(r[!tested, "adj.P.Val"] == "NA") &&
    all(r[!tested, "P.Value"] == "NA") && all(nzchar(r[!tested, "reason"])) &&
    all(r[tested, "reason"] == ""))
  log2fc <- rep(NA_real_, n)
  adjusted_p <- rep(NA_real_, n)
  if (any(tested)) {
    log2fc[tested] <- number(r[tested, "log2FC"])
    adjusted_p[tested] <- number(r[tested, "adj.P.Val"])
    original_p <- number(r[tested, "P.Value"])
    need(all(adjusted_p[tested] >= 0 & adjusted_p[tested] <= 1) &&
      all(original_p >= 0 & original_p <= 1))
  }
  # Source status gates ordinary classification before any cutoff decision.
  expected_status <- if (!any(tested)) "NO_TESTABLE_ROWS" else if (warnings > 0)
    "REVIEW_REQUIRED_LIMMA_WARNING" else "COMPUTED_CANDIDATE"
  need(a[["output_status"]] == expected_status)
  evaluated <- expected_status == "COMPUTED_CANDIDATE" && warnings == 0
  classification <- ifelse(tested, "not_evaluated", "not_testable")
  selected <- rep(FALSE, n)
  selected_token <- ifelse(tested, "NOT_EVALUATED", "FALSE")
  if (evaluated) {
    classification[tested] <- "NS"
    # Use ORIGINAL log2FC, never FC or a re-log2 transform. Inclusivity and sign
    # are separate: exact0/-0 remains NS even when the absolute cutoff is0.
    qualifies <- tested & is.finite(log2fc) & is.finite(adjusted_p) &
      adjusted_p <= adjusted_p_max & abs(log2fc) >= absolute_log2fc_min
    up <- qualifies & log2fc > 0
    down <- qualifies & log2fc < 0
    classification[which(up)] <- "Up"
    classification[which(down)] <- "Down"
    selected <- classification %in% c("Up", "Down")
    selected_token <- ifelse(selected, "TRUE", "FALSE")
  }
  status <- if (evaluated) "CLASSIFIED" else if (expected_status == "NO_TESTABLE_ROWS")
    "BLOCKED_SOURCE_NO_TESTABLE_ROWS" else "BLOCKED_SOURCE_REVIEW_REQUIRED"
  count_if_evaluated <- function(label) {
    if (evaluated) as.character(sum(classification == label)) else "NA"
  }
  audit <- c(schema_version = "de_classification_v0.1", classification_status = status,
    DE_members_evaluated = if (evaluated) "TRUE" else "FALSE",
    source_contract = a[["schema_version"]], source_output_status = a[["output_status"]],
    source_warning_count = a[["warning_count"]], source_contrast_id = a[["contrast_id"]],
    source_numerator = a[["numerator"]], source_denominator = a[["denominator"]],
    contrast_direction = "numerator_minus_denominator", threshold_scale = "log2FC",
    adjusted_p_max = p[["adjusted_p_max"]], absolute_log2fc_min = p[["absolute_log2fc_min"]],
    boundary_rule = "adj.P.Val_le_max_and_abs_log2FC_ge_min_and_strict_direction_sign",
    zero_log2FC = "NS_if_evaluated", input_rows = as.character(n),
    source_testable_rows = as.character(sum(tested)),
    source_not_testable_rows = as.character(sum(!tested)),
    Up = count_if_evaluated("Up"), Down = count_if_evaluated("Down"), NS = count_if_evaluated("NS"),
    not_testable = as.character(sum(classification == "not_testable")),
    not_evaluated = as.character(sum(classification == "not_evaluated")),
    selected_DE_rows = if (evaluated) as.character(sum(selected)) else "NA",
    source_bh_method = a[["bh_method"]], source_bh_m = a[["bh_m"]],
    source_bh_n = a[["bh_n"]], source_bh_scope = a[["bh_scope"]],
    FC_column = "copied_original_not_used_for_threshold", statistical_recomputation = "none",
    row_identity = "source_row_ordinal_no_ProteinID_deduplication",
    row_order = "original", specific_detection = "not_implemented_D04_not_frozen")
  # Original row lines are copied as prefixes. No statistic token, raw ID,
  # duplicate, semicolon or whitespace is reformatted by numeric conversion.
  header <- paste(c(result_header, "classification", "selected_DE"), collapse = "\t")
  rows <- paste(source$lines[-1L], classification, selected_token, sep = "\t")
  need(dir.exists(args[5L]) && length(list.files(args[5L], all.files = TRUE, no.. = TRUE)) == 0L)
  write_lines <- function(lines, name) writeLines(lines, file.path(args[5L], name), useBytes = TRUE)
  write_lines(c(header, rows), "classification.tsv")
  write_lines(c(header, rows[selected]), "selected-DE.tsv")
  writeBin(parameters$bytes, file.path(args[5L], "classification-parameters.tsv"))
  write_lines(c("key\tvalue", paste(names(audit), unname(audit), sep = "\t")), "classification-audit.tsv")
  writeBin(source_method$bytes, file.path(args[5L], "source-method-audit.tsv"))
  writeBin(test_set$bytes, file.path(args[5L], "source-test-set.tsv"))
  if (!evaluated) return(if (expected_status == "NO_TESTABLE_ROWS") 3L else 4L)
  0L
}
status <- tryCatch(withCallingHandlers(main(), warning = function(w) {
  stop("classification_warning", call. = FALSE)
}), error = function(e) {
  cat("de_classification_failed\n", file = stderr())
  2L
})
quit(save = "no", status = status, runLast = FALSE)
