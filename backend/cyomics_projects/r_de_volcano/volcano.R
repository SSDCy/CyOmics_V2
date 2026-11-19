# Standalone r_volcano_v0.1: original classification tokens + ggplot2 display only.
# Four staged paths confer no Owner/runtime authority. No fitting/BH/classifying.
main <- function() {
  args <- commandArgs(trailingOnly = TRUE)
  need <- function(ok) if (!isTRUE(ok)) stop("volcano_refused", call. = FALSE)
  need(length(args) == 4L)
  need(requireNamespace("ggplot2", quietly = TRUE) && requireNamespace("digest", quietly = TRUE))
  need(isTRUE(capabilities("cairo")))
  source_header <- c("source_row_ordinal", "protein_ids_raw", "contrast_id", "test_status", "reason", "log2FC", "FC", "FC_reason", "AveExpr", "moderated_t", "df_residual", "df_total", "P.Value", "adj.P.Val", "n_obs_num", "n_obs_den", "n_fit_num", "n_fit_den", "classification", "selected_DE")
  source_audit_keys <- c("schema_version", "classification_status", "DE_members_evaluated", "source_contract", "source_output_status", "source_warning_count", "source_contrast_id", "source_numerator", "source_denominator", "contrast_direction", "threshold_scale", "adjusted_p_max", "absolute_log2fc_min", "boundary_rule", "zero_log2FC", "input_rows", "source_testable_rows", "source_not_testable_rows", "Up", "Down", "NS", "not_testable", "not_evaluated", "selected_DE_rows", "source_bh_method", "source_bh_m", "source_bh_n", "source_bh_scope", "FC_column", "statistical_recomputation", "row_identity", "row_order", "specific_detection")
  audit_keys <- c("contract", "plot_status", "input_classification_sha256", "input_parameters_sha256", "input_audit_sha256", "source_classification_status", "source_contrast_id", "source_numerator", "source_denominator", "R_version", "ggplot2_version", "digest_version", "x_source", "axis", "y_transform", "adjusted_p_max", "absolute_log2fc_min", "boundary_rule", "threshold_x_negative", "threshold_x_positive", "threshold_y_true", "threshold_y_display", "threshold_y_floored", "q_display_floor", "q_zero_floor_rows", "q_small_floor_rows", "input_rows", "plotted_rows", "excluded_rows", "input_Up", "input_Down", "input_NS", "input_not_testable", "input_not_evaluated", "plotted_Up", "plotted_Down", "plotted_NS", "plotted_not_testable", "plotted_not_evaluated", "excluded_Up", "excluded_Down", "excluded_NS", "excluded_not_testable", "excluded_not_evaluated", "reason_plotted", "reason_plotted_q_floor", "reason_not_testable", "reason_not_evaluated", "reason_non_finite_coordinate", "statistical_recomputation", "row_identity", "row_order", "png_width_px", "png_height_px", "png_dpi", "svg_width_in", "svg_height_in", "rendering_package", "y_axis_upper", "image_download_format", "internal_preview_format")
  classes <- c("Up", "Down", "NS", "not_testable", "not_evaluated")
  reasons <- c("plotted", "plotted_q_floor", "not_testable", "not_evaluated", "non_finite_coordinate")
  parameter_keys <- c("schema_version", "input_scale", "adjusted_p_max", "absolute_log2fc_min")
  boundary <- "adj.P.Val_le_max_and_abs_log2FC_ge_min_and_strict_direction_sign"
  outputs <- c("R-session.txt", "volcano-audit.tsv", "volcano-points.tsv", "volcano.png", "volcano.svg")
  floor_q <- 1e-300
  hash_file <- function(path) digest::digest(file = path, algo = "sha256", serialize = FALSE)
  read_tsv <- function(path, header, cap, max_rows) {
    need(file.exists(path) && !isTRUE(file.info(path)$isdir))
    con <- file(path, open = "rb")
    on.exit(close(con))
    bytes <- readBin(con, what = "raw", n = cap + 1L)
    need(length(bytes) > 0L && length(bytes) <= cap && identical(tail(bytes, 1L), as.raw(10L)) &&
      !any(bytes %in% as.raw(c(0L, 13L))) &&
      !identical(bytes[seq_len(min(3L, length(bytes)))], as.raw(c(239L, 187L, 191L))))
    text <- rawToChar(bytes); Encoding(text) <- "UTF-8"; need(validUTF8(text))
    lines <- strsplit(text, "\n", fixed = TRUE)[[1L]]
    need(length(lines) >= 1L && length(lines) <= max_rows + 1L)
    fields <- lapply(lines, function(line) {
      x <- strsplit(paste0(line, "\tEND"), "\t", fixed = TRUE)[[1L]]
      x[-length(x)]
    })
    need(identical(fields[[1L]], header) && all(lengths(fields) == length(header)))
    n <- length(lines) - 1L
    data <- if (n == 0L) matrix(character(), nrow = 0L, ncol = length(header)) else
      matrix(unlist(fields[-1L], use.names = FALSE), nrow = n, ncol = length(header), byrow = TRUE)
    colnames(data) <- header
    sha <- hash_file(path)
    need(identical(sha, digest::digest(bytes, algo = "sha256", serialize = FALSE)))
    list(data = data, lines = lines, bytes = bytes, sha = sha)
  }
  key_values <- function(value, keys) {
    need(identical(value$data[, "key"], keys))
    x <- value$data[, "value"]; names(x) <- keys; x
  }
  number <- function(tokens) {
    special <- tokens %in% c("NA", "NaN", "Inf", "+Inf", "-Inf")
    need(all(special | grepl("^[+-]?([0-9]+(\\.[0-9]*)?|\\.[0-9]+)([eE][+-]?[0-9]+)?$", tokens)))
    out <- rep(NA_real_, length(tokens))
    out[!special] <- suppressWarnings(as.numeric(tokens[!special]))
    out[tokens == "NaN"] <- NaN
    out[tokens %in% c("Inf", "+Inf")] <- Inf; out[tokens == "-Inf"] <- -Inf
    out
  }
  integer_token <- function(tokens) {
    need(all(grepl("^(0|[1-9][0-9]*)$", tokens)))
    x <- number(tokens); need(all(is.finite(x) & x <= 9007199254740991)); x
  }
  fmt <- function(x) {
    need(all(is.finite(x)))
    out <- sprintf("%.17g", x); out[x == 0] <- "0"; out
  }
  source <- read_tsv(args[1L], source_header, 26L * 1024L * 1024L, 10000L)
  parameters <- read_tsv(args[2L], c("key", "value"), 4096L, 4L)
  original_audit <- read_tsv(args[3L], c("key", "value"), 32768L, length(source_audit_keys))
  p <- key_values(parameters, parameter_keys); a <- key_values(original_audit, source_audit_keys)
  q_limit <- number(p[["adjusted_p_max"]]); fc_limit <- number(p[["absolute_log2fc_min"]])
  need(p[["schema_version"]] == "de_classification_v0.1" && p[["input_scale"]] == "log2" &&
    is.finite(q_limit) && q_limit > 0 && q_limit <= 1 && is.finite(fc_limit) && fc_limit >= 0)
  need(a[["schema_version"]] == "de_classification_v0.1" && a[["classification_status"]] == "CLASSIFIED" &&
    a[["DE_members_evaluated"]] == "TRUE" && a[["source_contract"]] == "limma_core_v0.1" &&
    a[["source_output_status"]] == "COMPUTED_CANDIDATE" && a[["source_warning_count"]] == "0" &&
    a[["threshold_scale"]] == "log2FC" && a[["boundary_rule"]] == boundary &&
    a[["statistical_recomputation"]] == "none" && a[["row_order"]] == "original" &&
    a[["row_identity"]] == "source_row_ordinal_no_ProteinID_deduplication" &&
    a[["adjusted_p_max"]] == p[["adjusted_p_max"]] && a[["absolute_log2fc_min"]] == p[["absolute_log2fc_min"]])
  d <- source$data; n <- nrow(d); label <- d[, "classification"]
  ordinal <- integer_token(d[, "source_row_ordinal"])
  need(all(ordinal > 0) && !anyDuplicated(ordinal) && all(label %in% classes) &&
    all(d[, "contrast_id"] == a[["source_contrast_id"]]))
  need(all(d[, "test_status"] == ifelse(label == "not_testable", "not_testable", "testable")))
  selected <- ifelse(label %in% c("Up", "Down"), "TRUE", ifelse(label == "not_evaluated", "NOT_EVALUATED", "FALSE"))
  need(all(d[, "selected_DE"] == selected))
  x <- number(d[, "log2FC"]); q <- number(d[, "adj.P.Val"]); tested <- d[, "test_status"] == "testable"
  need(all(!tested | !is.finite(q) | (q >= 0 & q <= 1)))
  input_counts <- setNames(vapply(classes, function(k) sum(label == k), integer(1L)), classes)
  need(integer_token(a[["input_rows"]]) == n && integer_token(a[["source_testable_rows"]]) == sum(tested) &&
    integer_token(a[["source_not_testable_rows"]]) == sum(!tested) &&
    all(integer_token(a[classes]) == input_counts) &&
    integer_token(a[["selected_DE_rows"]]) == input_counts[["Up"]] + input_counts[["Down"]])
  reason <- rep("non_finite_coordinate", n)
  reason[label == "not_testable"] <- "not_testable"
  reason[label == "not_evaluated"] <- "not_evaluated"
  plotted <- label %in% c("Up", "Down", "NS") & is.finite(x) & is.finite(q)
  reason[plotted] <- ifelse(q[plotted] < floor_q, "plotted_q_floor", "plotted")
  plot_x <- plot_y <- rep("NA", n)
  y <- -log10(pmax(q[plotted], floor_q))
  need(all(is.finite(y) & y >= 0 & y <= 300))
  plot_x[plotted] <- fmt(x[plotted]); plot_y[plotted] <- fmt(y)
  true_line <- -log10(q_limit); display_line <- -log10(max(q_limit, floor_q))
  y_top <- min(300, 1.05 * max(c(1, display_line, y)))
  plotted_counts <- setNames(vapply(classes, function(k) sum(plotted & label == k), integer(1L)), classes)
  excluded_counts <- input_counts - plotted_counts
  reason_counts <- setNames(vapply(reasons, function(k) sum(reason == k), integer(1L)), reasons)
  frame <- data.frame(plot_x = x[plotted], plot_y = y,
    classification = factor(label[plotted], levels = c("Up", "Down", "NS")))
  graph <- ggplot2::ggplot(frame, ggplot2::aes(x = plot_x, y = plot_y, colour = classification)) +
    ggplot2::geom_point(stat = "identity", position = "identity", shape = 16L, size = 1.2, alpha = 0.75) +
    ggplot2::scale_colour_manual(values = c(Up = "#D73027", Down = "#4575B4", NS = "#9E9E9E"), drop = FALSE) +
    ggplot2::geom_vline(xintercept = c(-fc_limit, fc_limit), colour = "black", linetype = "dashed", linewidth = 0.35) +
    ggplot2::geom_hline(yintercept = display_line, colour = "black", linetype = "dashed", linewidth = 0.35) +
    ggplot2::coord_cartesian(ylim = c(0, y_top), expand = TRUE) +
    ggplot2::labs(x = "log2FC", y = "-log10(adj.P.Val); display q floor 1e-300",
      title = paste0(a[["source_numerator"]], " - ", a[["source_denominator"]]),
      subtitle = paste0("contrast_id: ", a[["source_contrast_id"]]), colour = "Original classification") +
    ggplot2::theme_bw(base_size = 11)
  if (!any(plotted)) graph <- graph + ggplot2::annotate("text", x = 0, y = y_top / 2,
    label = "No plottable rows", colour = "black", size = 4)
  need(dir.exists(args[4L]) && length(list.files(args[4L], all.files = TRUE, no.. = TRUE)) == 0L)
  out <- function(name) file.path(args[4L], name)
  ggplot2::ggsave(filename = out("volcano.png"), plot = graph, device = grDevices::png,
    type = "cairo", width = 8, height = 6, units = "in", dpi = 300, bg = "white", limitsize = TRUE)
  # Native R SVG: points, threshold lines and glyph paths remain scalable vectors.
  ggplot2::ggsave(filename = out("volcano.svg"), plot = graph, device = grDevices::svg,
    width = 8, height = 6, units = "in", bg = "white", limitsize = TRUE)
  header <- paste(c(source_header, "plot_x", "plot_y", "plotted", "plot_reason"), collapse = "\t")
  rows <- if (n == 0L) character() else paste(source$lines[-1L], plot_x, plot_y, ifelse(plotted, "TRUE", "FALSE"), reason, sep = "\t")
  writeLines(c(header, rows), out("volcano-points.tsv"), useBytes = TRUE)
  versions <- c(R_version = R.version.string, ggplot2_version = as.character(utils::packageVersion("ggplot2")),
    digest_version = as.character(utils::packageVersion("digest")))
  audit <- c(contract = "r_volcano_v0.1", plot_status = if (any(plotted)) "PLOTTED" else "EMPTY_NO_PLOTTABLE_ROWS",
    input_classification_sha256 = source$sha, input_parameters_sha256 = parameters$sha, input_audit_sha256 = original_audit$sha,
    source_classification_status = a[["classification_status"]], source_contrast_id = a[["source_contrast_id"]],
    source_numerator = a[["source_numerator"]], source_denominator = a[["source_denominator"]], versions,
    x_source = "log2FC", axis = "adj.P.Val", y_transform = "-log10(pmax(adj.P.Val,1e-300))",
    adjusted_p_max = p[["adjusted_p_max"]], absolute_log2fc_min = p[["absolute_log2fc_min"]], boundary_rule = a[["boundary_rule"]],
    threshold_x_negative = fmt(-fc_limit), threshold_x_positive = fmt(fc_limit),
    threshold_y_true = fmt(true_line), threshold_y_display = fmt(display_line),
    threshold_y_floored = if (q_limit < floor_q) "TRUE" else "FALSE", q_display_floor = "1e-300",
    q_zero_floor_rows = as.character(sum(plotted & is.finite(q) & q == 0)),
    q_small_floor_rows = as.character(sum(plotted & is.finite(q) & q > 0 & q < floor_q)),
    input_rows = as.character(n), plotted_rows = as.character(sum(plotted)), excluded_rows = as.character(sum(!plotted)))
  for (k in classes) audit[paste0("input_", k)] <- as.character(input_counts[[k]])
  for (k in classes) audit[paste0("plotted_", k)] <- as.character(plotted_counts[[k]])
  for (k in classes) audit[paste0("excluded_", k)] <- as.character(excluded_counts[[k]])
  for (k in reasons) audit[paste0("reason_", k)] <- as.character(reason_counts[[k]])
  audit <- c(audit, statistical_recomputation = "none", row_identity = a[["row_identity"]], row_order = "original",
    png_width_px = "2400", png_height_px = "1800", png_dpi = "300", svg_width_in = "8", svg_height_in = "6",
    rendering_package = "ggplot2", y_axis_upper = fmt(y_top), image_download_format = "svg", internal_preview_format = "png")
  need(identical(names(audit), audit_keys))
  writeLines(c("key\tvalue", paste(names(audit), unname(audit), sep = "\t")), out("volcano-audit.tsv"), useBytes = TRUE)
  writeLines(c(paste0("R_VERSION\t", versions[["R_version"]]), paste0("GGPLOT2_VERSION\t", versions[["ggplot2_version"]]),
    paste0("DIGEST_VERSION\t", versions[["digest_version"]]), capture.output(utils::sessionInfo())), out("R-session.txt"), useBytes = TRUE)
  need(identical(sort(list.files(args[4L], all.files = TRUE, no.. = TRUE)), sort(outputs)))
  sizes <- file.info(file.path(args[4L], outputs))$size
  caps <- ifelse(outputs %in% c("R-session.txt", "volcano-audit.tsv"), 65536, 32 * 1024 * 1024)
  need(all(is.finite(sizes) & sizes > 0 & sizes <= caps) && sum(sizes) <= 128 * 1024 * 1024)
  need(identical(hash_file(args[1L]), source$sha) && identical(hash_file(args[2L]), parameters$sha) &&
    identical(hash_file(args[3L]), original_audit$sha))
  cat("R_VOLCANO_COMPLETE\n")
}
tryCatch(withCallingHandlers(main(), warning = function(w) stop("volcano_warning", call. = FALSE)),
  error = function(e) { cat("R_VOLCANO_REFUSED\n", file = stderr()); quit(save = "no", status = 2L, runLast = FALSE) })
