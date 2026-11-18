# LIMMA-CORE-SOURCE-001: scientific source candidate, not a registered runner.
# Four trusted local paths: matrix.tsv design.tsv preset.tsv existing output dir.
# No formula construction, source(), eval(), system(), download or plotting.
main <- function() {
  args <- commandArgs(trailingOnly = TRUE)
  if (length(args) != 4L) stop("invalid_arguments", call. = FALSE)
  options(digits = 17, OutDec = ".")
  read_tsv <- function(path) {
    lines <- readLines(path, warn = FALSE, encoding = "UTF-8")
    if (!length(lines) || !all(validUTF8(lines)) || any(grepl("\r", lines, fixed = TRUE)))
      stop("invalid_tsv", call. = FALSE)
    fields <- lengths(strsplit(paste0(lines, "\tEND"), "\t", fixed = TRUE)) - 1L
    if (any(fields != fields[1L])) stop("invalid_tsv", call. = FALSE)
    text <- paste(lines, collapse = "\n")
    utils::read.delim(text = text, sep = "\t", header = TRUE, quote = "",
      comment.char = "", colClasses = "character", check.names = FALSE,
      na.strings = NULL, blank.lines.skip = FALSE, stringsAsFactors = FALSE,
      row.names = NULL, allowEscapes = FALSE, fill = FALSE)
  }
  exact_names <- function(x, expected) {
    if (!identical(names(x), expected)) stop("invalid_columns", call. = FALSE)
  }
  preset <- read_tsv(args[3L])
  exact_names(preset, c("key", "value"))
  expected_keys <- c("schema_version", "input_scale", "upstream_transform",
    "upstream_zero_blank", "pseudocount", "normalization", "imputation",
    "replicate_type", "design_confirmed", "contrast_id", "numerator", "denominator")
  if (!identical(preset$key, expected_keys)) stop("invalid_preset", call. = FALSE)
  p <- setNames(preset$value, preset$key)
  fixed <- c(schema_version = "limma_core_v0.1", input_scale = "log2",
    upstream_transform = "positive_log2", upstream_zero_blank = "NA",
    pseudocount = "0", normalization = "none", imputation = "none",
    replicate_type = "biological", design_confirmed = "true")
  if (!identical(unname(p[names(fixed)]), unname(fixed)) ||
      !grepl("^[A-Za-z0-9_-]{1,64}$", p[["contrast_id"]]) ||
      !setequal(c(p[["numerator"]], p[["denominator"]]), c("g1", "g2")))
    stop("invalid_preset", call. = FALSE)
  design_input <- read_tsv(args[2L])
  exact_names(design_input, c("sample_key", "group_key", "replicate_key", "replicate_type", "confirmed"))
  n <- nrow(design_input)
  # A candidate core input bound, not a platform sample quota or registered profile.
  if (n < 4L || n > 64L ||
      !identical(design_input$sample_key, paste0("s", seq_len(n))) ||
      !all(design_input$group_key %in% c("g1", "g2")) ||
      any(table(factor(design_input$group_key, levels = c("g1", "g2"))) < 2L) ||
      !all(grepl("^[A-Za-z0-9_-]{1,64}$", design_input$replicate_key)) ||
      anyDuplicated(paste(design_input$group_key, design_input$replicate_key, sep = ":")) ||
      !all(design_input$replicate_type == "biological") ||
      !all(design_input$confirmed == "true")) stop("invalid_design", call. = FALSE)
  input <- read_tsv(args[1L])
  exact_names(input, c("source_row_ordinal", "protein_ids_raw", design_input$sample_key))
  if (!nrow(input) || !all(grepl("^[1-9][0-9]*$", input$source_row_ordinal)))
    stop("invalid_rows", call. = FALSE)
  ordinal <- suppressWarnings(as.numeric(input$source_row_ordinal))
  if (any(!is.finite(ordinal)) || any(ordinal > 9007199254740991) ||
      !identical(sprintf("%.0f", ordinal), input$source_row_ordinal) ||
      is.unsorted(ordinal, strictly = TRUE)) stop("invalid_rows", call. = FALSE)
  tokens <- as.matrix(input[, design_input$sample_key, drop = FALSE])
  missing <- tokens == "NA"
  numeric_token <- grepl("^[+-]?([0-9]+(\\.[0-9]*)?|\\.[0-9]+)([eE][+-]?[0-9]+)?$", tokens)
  dim(numeric_token) <- dim(tokens)
  numbers <- suppressWarnings(as.numeric(tokens))
  dim(numbers) <- dim(tokens)
  # Log2 0 and negative finite values remain valid observations, never re-log2.
  invalid <- !missing & (!numeric_token | !is.finite(numbers))
  numbers[missing | invalid] <- NA_real_
  colnames(numbers) <- design_input$sample_key
  rownames(numbers) <- input$source_row_ordinal
  num_cols <- design_input$group_key == p[["numerator"]]
  den_cols <- design_input$group_key == p[["denominator"]]
  n_num <- rowSums(is.finite(numbers[, num_cols, drop = FALSE]))
  n_den <- rowSums(is.finite(numbers[, den_cols, drop = FALSE]))
  invalid_row <- rowSums(invalid) > 0L
  reason <- vapply(seq_len(nrow(input)), function(i) {
    r <- character()
    if (invalid_row[i]) r <- c(r, "invalid_numeric_token")
    if (n_num[i] < 2L) r <- c(r, "insufficient_numerator_observations")
    if (n_den[i] < 2L) r <- c(r, "insufficient_denominator_observations")
    paste(r, collapse = ";")
  }, character(1L))
  eligible <- reason == ""
  count <- nrow(input)
  results <- data.frame(source_row_ordinal = input$source_row_ordinal,
    protein_ids_raw = input$protein_ids_raw, contrast_id = rep(p[["contrast_id"]], count),
    test_status = rep("not_testable", count), reason = reason,
    log2FC = rep(NA_real_, count), FC = rep(NA_real_, count),
    FC_reason = rep("not_testable", count), AveExpr = rep(NA_real_, count),
    moderated_t = rep(NA_real_, count), df_residual = rep(NA_real_, count),
    df_total = rep(NA_real_, count), P.Value = rep(NA_real_, count),
    adj.P.Val = rep(NA_real_, count), n_obs_num = n_num, n_obs_den = n_den,
    n_fit_num = rep(0L, count), n_fit_den = rep(0L, count),
    check.names = FALSE, stringsAsFactors = FALSE)
  if (!identical(as.character(getRversion()), "4.6.1") ||
      !requireNamespace("limma", quietly = TRUE) ||
      !identical(as.character(utils::packageVersion("limma")), "3.68.5") ||
      !requireNamespace("statmod", quietly = TRUE) ||
      !identical(as.character(utils::packageVersion("statmod")), "1.5.2"))
    stop("unsupported_runtime", call. = FALSE)
  X <- cbind(g1 = as.numeric(design_input$group_key == "g1"),
             g2 = as.numeric(design_input$group_key == "g2"))
  rownames(X) <- design_input$sample_key
  if (qr(X)$rank != 2L) stop("design_not_estimable", call. = FALSE)
  contrast <- matrix(0, nrow = 2L, ncol = 1L,
    dimnames = list(c("g1", "g2"), "c1"))
  contrast[p[["numerator"]], 1L] <- 1
  contrast[p[["denominator"]], 1L] <- -1
  warning_codes <- character()
  capture_warning <- function(w) {
    # Preserve a fixed audit code; never persist raw warning text or identifiers.
    warning_codes <<- c(warning_codes, "limma_warning_review_required")
    invokeRestart("muffleWarning")
  }
  if (any(eligible)) {
    indices <- which(eligible)
    fit <- withCallingHandlers(limma::lmFit(numbers[indices, , drop = FALSE],
      design = X, ndups = 1L, spacing = 1L, method = "ls", weights = NULL,
      block = NULL, correlation = NULL), warning = capture_warning)
    results$n_fit_num[indices] <- n_num[indices]
    results$n_fit_den[indices] <- n_den[indices]
    fit_ok <- is.finite(fit$df.residual) & fit$df.residual > 0 &
      is.finite(fit$sigma) & fit$sigma >= 0 &
      rowSums(!is.finite(fit$coefficients)) == 0L &
      rowSums(!is.finite(fit$stdev.unscaled) | fit$stdev.unscaled <= 0) == 0L
    results$reason[indices[!fit_ok]] <- "invalid_fit_or_residual_df"
    if (any(fit_ok)) {
      fit <- fit[fit_ok, , drop = FALSE]
      indices <- indices[fit_ok]
      fit <- withCallingHandlers(limma::contrasts.fit(fit, contrasts = contrast,
        coefficients = NULL), warning = capture_warning)
      fit <- withCallingHandlers(limma::eBayes(fit, proportion = 0.01,
        stdev.coef.lim = c(0.1, 4), trend = FALSE, span = NULL, robust = FALSE,
        winsor.tail.p = c(0.05, 0.1), legacy = NULL), warning = capture_warning)
      values <- cbind(log2FC = fit$coefficients[, 1L], AveExpr = fit$Amean,
        moderated_t = fit$t[, 1L], df_residual = fit$df.residual,
        df_total = fit$df.total, P.Value = fit$p.value[, 1L])
      finite <- rowSums(!is.finite(values)) == 0L & values[, "df_residual"] > 0 &
        values[, "df_total"] > 0 & values[, "P.Value"] >= 0 & values[, "P.Value"] <= 1
      results$reason[indices[!finite]] <- "nonfinite_or_invalid_moderated_result"
      tested <- indices[finite]
      if (length(tested)) {
        results[tested, colnames(values)] <- values[finite, , drop = FALSE]
        results$test_status[tested] <- "testable"
        results$reason[tested] <- ""
        # One family: ALL final testable finite P, with no DEP/rank/topTable cutoff.
        m <- length(tested)
        results$adj.P.Val[tested] <- stats::p.adjust(results$P.Value[tested], method = "BH", n = m)
        fc <- 2^results$log2FC[tested]
        ok_fc <- is.finite(fc) & fc > 0
        results$FC[tested[ok_fc]] <- fc[ok_fc]
        results$FC_reason[tested] <- ifelse(is.infinite(fc), "overflow",
          ifelse(fc == 0, "underflow", ifelse(is.na(fc), "invalid", "")))
      }
    }
  }
  tested <- which(results$test_status == "testable")
  output_status <- if (!length(tested)) "NO_TESTABLE_ROWS" else if (length(warning_codes))
    "REVIEW_REQUIRED_LIMMA_WARNING" else "COMPUTED_CANDIDATE"
  audit <- c(schema_version = "limma_core_v0.1", output_status = output_status,
    input_scale = "log2", upstream_zero_blank = "NA", pseudocount = "0",
    normalization = "none", imputation = "none", input_rows = as.character(count),
    input_samples = as.character(n), numerator = p[["numerator"]], denominator = p[["denominator"]],
    contrast_id = p[["contrast_id"]], eligible_before_fit = as.character(sum(eligible)),
    bh_m = as.character(length(tested)), bh_method = "BH", bh_n = as.character(length(tested)),
    bh_scope = "all_final_testable_finite_P_for_one_contrast_before_DEP_thresholds",
    lmFit_method = "ls", lmFit_ndups = "1", lmFit_spacing = "1", lmFit_weights = "NULL",
    lmFit_block = "NULL", lmFit_correlation = "NULL", contrasts_fit_coefficients = "NULL",
    eBayes_proportion = "0.01", eBayes_stdev_coef_lim = "0.1,4", eBayes_trend = "FALSE",
    eBayes_span = "NULL", eBayes_robust = "FALSE", eBayes_winsor_tail_p = "0.05,0.1",
    eBayes_legacy = "NULL", seed = "not_used_no_stochastic_operation",
    FC_definition = "2^log2FC_geometric_scale_ratio_not_raw_arithmetic_mean_ratio",
    warning_count = as.character(length(warning_codes)),
    warning_codes = paste(unique(warning_codes), collapse = ";"),
    R_version = as.character(getRversion()), limma_version = as.character(utils::packageVersion("limma")),
    statmod_version = as.character(utils::packageVersion("statmod")))
  outputs <- c("results.tsv", "method-audit.tsv", "sample-audit.tsv", "test-set.tsv", "R-session.txt")
  if (!dir.exists(args[4L]) || any(file.exists(file.path(args[4L], outputs))))
    stop("invalid_output_destination", call. = FALSE)
  write_tsv <- function(x, name) utils::write.table(x, file = file.path(args[4L], name),
    sep = "\t", quote = FALSE, row.names = FALSE, col.names = TRUE, na = "NA", fileEncoding = "UTF-8")
  write_tsv(results, "results.tsv")
  write_tsv(data.frame(key = names(audit), value = unname(audit)), "method-audit.tsv")
  sample_audit <- data.frame(matrix_column_ordinal = seq_len(n), design_input,
    contrast_weight = contrast[design_input$group_key, 1L], check.names = FALSE)
  write_tsv(sample_audit, "sample-audit.tsv")
  write_tsv(data.frame(source_row_ordinal = input$source_row_ordinal,
    included_in_BH = results$test_status == "testable", P.Value = results$P.Value,
    test_status = results$test_status, reason = results$reason, check.names = FALSE), "test-set.tsv")
  writeLines(c("Scientific core candidate; package/runtime/session provenance follows.",
    paste0("UTC: ", format(Sys.time(), "%Y-%m-%dT%H:%M:%OS6Z", tz = "UTC")),
    capture.output(utils::sessionInfo())), file.path(args[4L], "R-session.txt"), useBytes = TRUE)
  if (!length(tested)) return(3L)
  if (length(warning_codes)) return(4L)
  0L
}
status <- tryCatch(withCallingHandlers(main(), warning = function(w) {
  # Non-limma warnings (for example path-bearing IO diagnostics) are fixed errors.
  stop("core_warning", call. = FALSE)
}), error = function(e) {
  cat("limma_core_failed\n", file = stderr())
  2L
})
quit(save = "no", status = status, runLast = FALSE)
