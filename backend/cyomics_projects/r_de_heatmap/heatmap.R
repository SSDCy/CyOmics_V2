# Saved log2 LFQ only: pheatmap + grid; no fit/BH/classification or installation.
# Five staged paths confer no Owner/runtime authority. Pure validator binds JSON.
main <- function() {
  args <- commandArgs(trailingOnly = TRUE)
  need <- function(ok) if (!isTRUE(ok)) stop("heatmap_refused", call. = FALSE)
  need(length(args) == 5L && requireNamespace("pheatmap", quietly = TRUE) && requireNamespace("digest", quietly = TRUE))
  need(as.character(utils::packageVersion("pheatmap")) == "1.0.13" && isTRUE(capabilities("cairo")))
  cap <- 27262976L
  hash <- function(path) digest::digest(file = path, algo = "sha256", serialize = FALSE)
  read_raw <- function(path, limit = cap) {
    need(file.exists(path) && !isTRUE(file.info(path)$isdir))
    con <- file(path, "rb"); on.exit(close(con))
    raw <- readBin(con, "raw", n = limit + 1L)
    need(length(raw) > 0L && length(raw) <= limit && !any(raw %in% as.raw(c(0L, 13L))) &&
      !identical(head(raw, 3L), as.raw(c(239L, 187L, 191L))))
    value <- rawToChar(raw); Encoding(value) <- "UTF-8"; need(validUTF8(value))
    sha <- hash(path); need(sha == digest::digest(raw, algo = "sha256", serialize = FALSE))
    list(raw = raw, text = value, sha = sha)
  }
  read_tsv <- function(path, header, rows) {
    value <- read_raw(path); need(identical(tail(value$raw, 1L), as.raw(10L)))
    lines <- strsplit(value$text, "\n", fixed = TRUE)[[1L]]
    need(length(lines) >= 1L && length(lines) <= rows + 1L)
    fields <- lapply(lines, function(line) {
      x <- strsplit(paste0(line, "\tEND"), "\t", fixed = TRUE)[[1L]]; x[-length(x)]
    })
    need(identical(fields[[1L]], header) && all(lengths(fields) == length(header)))
    data <- if (length(lines) == 1L) matrix(character(), 0L, length(header)) else
      matrix(unlist(fields[-1L], use.names = FALSE), ncol = length(header), byrow = TRUE)
    colnames(data) <- header; c(value, list(lines = lines, data = data))
  }
  integer_token <- function(x) {
    need(all(grepl("^[1-9][0-9]*$", x)))
    out <- suppressWarnings(as.numeric(x)); need(all(is.finite(out) & out <= 9007199254740991)); out
  }
  number <- function(x) {
    need(all(x == "NA" | grepl("^[+-]?([0-9]+(\\.[0-9]*)?|\\.[0-9]+)([eE][+-]?[0-9]+)?$", x)))
    out <- rep(NA_real_, length(x)); out[x != "NA"] <- suppressWarnings(as.numeric(x[x != "NA"]))
    need(all(x == "NA" | is.finite(out))); out
  }
  fmt <- function(x) {
    need(all(is.na(x) | is.finite(x))); out <- rep("NA", length(x))
    out[!is.na(x)] <- sprintf("%.17g", x[!is.na(x)]); out[!is.na(x) & x == 0] <- "0"; out
  }
  nonempty <- function(x) need(all(nzchar(x) & !grepl("[[:cntrl:]]", x)))
  keys <- paste0("s", seq_len(6L))
  mh <- c("source_row_ordinal", "protein_ids_raw", keys)
  sh <- c("sample_key", "source_column_ordinal", "sample_name", "group_key", "group_label", "replicate_key", "replicate_type")
  dh <- c("source_row_ordinal", "protein_ids_raw", "contrast_id", "test_status", "reason", "log2FC", "FC", "FC_reason", "AveExpr", "moderated_t", "df_residual", "df_total", "P.Value", "adj.P.Val", "n_obs_num", "n_obs_den", "n_fit_num", "n_fit_den", "classification", "selected_DE")
  audit_keys <- c("schema_version", "output_status", "input_scale", "display_scale", "center", "scale", "normalization", "imputation", "pseudocount", "cluster_rows", "cluster_cols", "missing_policy", "invalid_scaling_policy", "row_order", "sample_order", "input_rows", "selected_rows", "samples", "observed_cells", "missing_cells", "plotted_observed_cells", "plotted_missing_cells", "scaled_rows", "zero_sd_rows", "all_missing_rows", "insufficient_observations_rows", "color_limit", "seed", "R_version", "pheatmap_version", "SVG_width_inches", "SVG_height_inches", "PNG_width_pixels", "PNG_height_pixels", "matrix_bytes", "matrix_sha256", "selected_DE_bytes", "selected_DE_sha256", "samples_bytes", "samples_sha256", "provenance_bytes", "provenance_sha256")
  outputs <- c("R-session.txt", "heatmap-audit.tsv", "heatmap-matrix.tsv", "heatmap-row-map.tsv", "heatmap-samples.tsv", "heatmap-zscore.tsv", "heatmap.png", "heatmap.svg")
  matrix_input <- read_tsv(args[1L], mh, 20000L)
  selected <- read_tsv(args[2L], dh, 200L)
  samples <- read_tsv(args[3L], sh, 64L)
  provenance <- read_raw(args[4L], 65536L) # Parsed/bound by pure validator, not an R JSON package.
  d <- matrix_input$data; sel <- selected$data; sm <- samples$data
  need(nrow(d) > 0L && nrow(sel) > 0L && nrow(sm) == 6L)
  ord <- integer_token(d[, 1L]); so <- integer_token(sel[, 1L])
  need(!anyDuplicated(ord) && !is.unsorted(ord, strictly = TRUE) && !anyDuplicated(so) && !is.unsorted(so, strictly = TRUE))
  nonempty(d[, 2L]); nonempty(sel[, 3L])
  need(all(sel[, "classification"] %in% c("Up", "Down")) && all(sel[, "selected_DE"] == "TRUE") &&
    all(sel[, "test_status"] == "testable") && length(unique(sel[, "contrast_id"])) == 1L)
  index <- match(sel[, 1L], d[, 1L]); need(!anyNA(index) && identical(d[index, 2L], sel[, 2L]))
  need(identical(ord[index], ord[ord %in% so]))
  all_x <- matrix(number(d[, keys, drop = FALSE]), ncol = 6L)
  x <- all_x[index, , drop = FALSE]
  need(identical(sm[, "sample_key"], keys) && !anyDuplicated(integer_token(sm[, "source_column_ordinal"])))
  nonempty(sm[, 3:7, drop = FALSE]); need(all(sm[, "replicate_type"] == "biological"))
  groups <- unique(sm[, "group_key"]); need(length(groups) == 2L)
  labels <- vapply(groups, function(k) {
    value <- unique(sm[sm[, "group_key"] == k, "group_label"]); need(length(value) == 1L); value
  }, character(1L)); need(!anyDuplicated(labels))
  need(!anyDuplicated(data.frame(group = sm[, "group_key"], replicate = sm[, "replicate_key"])))
  observed <- rowSums(!is.na(x))
  means <- apply(x, 1L, function(v) if (all(is.na(v))) NA_real_ else mean(v, na.rm = TRUE))
  sds <- apply(x, 1L, function(v) if (sum(!is.na(v)) < 2L) NA_real_ else stats::sd(v, na.rm = TRUE))
  need(all(is.na(means) | is.finite(means)) && all(is.na(sds) | is.finite(sds)))
  status <- ifelse(observed == 0L, "all_missing", ifelse(observed == 1L, "insufficient_observations", ifelse(sds == 0, "zero_sd", "scaled")))
  # Exactly base R's observed-row display transform; original x and token rows stay intact.
  z <- t(base::scale(t(x)))
  z[status != "scaled", ] <- NA_real_
  need(all(is.na(z) | is.finite(z)))
  color_limit <- max(c(1, abs(z[is.finite(z)])))
  breaks <- seq(-color_limit, color_limit, length.out = 201L)
  colors <- grDevices::colorRampPalette(c("#2166AC", "#F7F7F7", "#B2182B"))(200L)
  rownames(z) <- sel[, "protein_ids_raw"]; colnames(z) <- keys
  annotation <- data.frame(Group = factor(sm[, "group_label"], levels = labels), row.names = keys, check.names = FALSE)
  title <- paste0("Differential protein groups: ", labels[1L], " vs ", labels[2L], "; Row Z-score of saved log2 LFQ")
  row_font <- min(10, 420 / max(1, 0.65 * max(nchar(rownames(z), type = "width"))), 430 / nrow(z))
  need(dir.exists(args[5L]) && length(list.files(args[5L], all.files = TRUE, no.. = TRUE)) == 0L)
  out <- function(name) file.path(args[5L], name)
  on.exit(if (grDevices::dev.cur() > 1L) grDevices::dev.off(), add = TRUE)
  grDevices::svg(out("heatmap.svg"), width = 16, height = 9, family = "sans", bg = "white")
  graph <- pheatmap::pheatmap(z, scale = "none", cluster_rows = FALSE, cluster_cols = FALSE,
    annotation_col = annotation, annotation_colors = list(Group = stats::setNames(c("#4DAF4A", "#984EA3"), labels)),
    color = colors, breaks = breaks, na_col = "#BDBDBD",
    labels_row = sel[, "protein_ids_raw"], labels_col = sm[, "sample_name"], main = title,
    fontsize = 8, fontsize_row = row_font, fontsize_col = 8, angle_col = 45, border_color = NA, silent = TRUE)
  grid::grid.newpage(); grid::grid.draw(graph$gtable); grDevices::dev.off()
  grDevices::png(out("heatmap.png"), width = 2400L, height = 1350L, res = 150L, type = "cairo", family = "sans", bg = "white")
  grid::grid.newpage(); grid::grid.draw(graph$gtable); grDevices::dev.off()
  # Original matrix/sample bytes and original row tokens are never reformatted.
  writeLines(c(matrix_input$lines[1L], matrix_input$lines[index + 1L]), out("heatmap-matrix.tsv"), useBytes = TRUE)
  con <- file(out("heatmap-samples.tsv"), "wb"); writeBin(samples$raw, con); close(con)
  map_header <- "source_row_ordinal\tprotein_ids_raw\tclassification\tobserved_n\trow_mean\trow_sd\trow_scaling_status"
  map_rows <- paste(sel[, 1L], sel[, 2L], sel[, "classification"], observed, fmt(means), fmt(sds), status, sep = "\t")
  writeLines(c(map_header, map_rows), out("heatmap-row-map.tsv"), useBytes = TRUE)
  z_rows <- vapply(seq_len(nrow(z)), function(i) paste(c(sel[i, 1:2], fmt(z[i, ])), collapse = "\t"), character(1L))
  writeLines(c(paste(mh, collapse = "\t"), z_rows), out("heatmap-zscore.tsv"), useBytes = TRUE)
  audit <- c(schema_version = "r_heatmap_output_v0.1", output_status = "HEATMAP_RENDERED", input_scale = "log2", display_scale = "row_zscore",
    center = "observed_row_mean", scale = "observed_sample_sd", normalization = "none", imputation = "none", pseudocount = "0",
    cluster_rows = "FALSE", cluster_cols = "FALSE", missing_policy = "preserve_NA_grey", invalid_scaling_policy = "retain_row_display_NA_grey",
    row_order = "source_row_ordinal_ascending", sample_order = "input_header", input_rows = nrow(d), selected_rows = nrow(sel), samples = 6L,
    observed_cells = sum(!is.na(all_x)), missing_cells = sum(is.na(all_x)), plotted_observed_cells = sum(is.finite(z)), plotted_missing_cells = sum(is.na(z)),
    scaled_rows = sum(status == "scaled"), zero_sd_rows = sum(status == "zero_sd"), all_missing_rows = sum(status == "all_missing"),
    insufficient_observations_rows = sum(status == "insufficient_observations"), color_limit = fmt(color_limit), seed = "not_used_no_clustering",
    R_version = R.version.string, pheatmap_version = as.character(utils::packageVersion("pheatmap")),
    SVG_width_inches = "16", SVG_height_inches = "9", PNG_width_pixels = "2400", PNG_height_pixels = "1350",
    matrix_bytes = length(matrix_input$raw), matrix_sha256 = matrix_input$sha, selected_DE_bytes = length(selected$raw), selected_DE_sha256 = selected$sha,
    samples_bytes = length(samples$raw), samples_sha256 = samples$sha, provenance_bytes = length(provenance$raw), provenance_sha256 = provenance$sha)
  need(identical(names(audit), audit_keys))
  writeLines(c("key\tvalue", paste(names(audit), unname(audit), sep = "\t")), out("heatmap-audit.tsv"), useBytes = TRUE)
  writeLines(c(paste0("R_VERSION\t", R.version.string), paste0("PHEATMAP_VERSION\t", utils::packageVersion("pheatmap")),
    paste0("DIGEST_VERSION\t", utils::packageVersion("digest")), capture.output(utils::sessionInfo())), out("R-session.txt"), useBytes = TRUE)
  need(identical(sort(list.files(args[5L], all.files = TRUE, no.. = TRUE)), sort(outputs)))
  sizes <- file.info(file.path(args[5L], outputs))$size
  need(all(is.finite(sizes) & sizes > 0 & sizes <= cap) && sum(sizes) <= 134217728)
  need(identical(hash(args[1L]), matrix_input$sha) && identical(hash(args[2L]), selected$sha) && identical(hash(args[3L]), samples$sha) && identical(hash(args[4L]), provenance$sha))
  cat("R_HEATMAP_COMPLETE\n")
}
tryCatch(withCallingHandlers(main(), warning = function(w) stop("heatmap_warning", call. = FALSE)),
  error = function(e) { cat("R_HEATMAP_REFUSED\n", file = stderr()); quit(save = "no", status = 2L, runLast = FALSE) })
