# Raw quantitative extraction only. All calculations use base R or stats functions.
args <- commandArgs(trailingOnly=TRUE)
fail <- function(code) stop(code, call.=FALSE)
if (length(args) != 3L) fail("invalid_arguments")
input <- args[[1L]]; family <- args[[2L]]; output <- args[[3L]]
if (!family %in% c("Intensity", "LFQ intensity")) fail("unsupported_family")
if (!file.exists(input) || file.info(input)$size < 1 || file.info(input)$size > 26214400) fail("input_size")
if (file.exists(output)) fail("output_already_exists")
# Explicit byte/UTF-8 gate before any text parser can warn and return partial data.
raw_input <- readBin(input, what="raw", n=file.info(input)$size)
if (length(raw_input) != file.info(input)$size || any(raw_input == as.raw(0L))) fail("input_byte_integrity")
decoded <- rawToChar(raw_input)
if (!validUTF8(decoded)) fail("invalid_utf8")
if (startsWith(decoded, "\ufeff")) decoded <- substring(decoded, 2L)
widths <- withCallingHandlers(utils::count.fields(input, sep="\t", quote="", comment.char="", blank.lines.skip=FALSE),
    warning=function(w) fail("input_read_warning"))
if (length(widths) < 2L || anyNA(widths) || any(widths != widths[[1L]])) fail("ragged_or_empty_table")
if (widths[[1L]] * (length(widths)-1L) > 2000000) fail("cell_limit")
tab <- withCallingHandlers(utils::read.delim(input, header=TRUE, sep="\t", quote="", comment.char="",
    check.names=FALSE, colClasses="character", na.strings=character(0), fill=FALSE,
    blank.lines.skip=FALSE, strip.white=FALSE, row.names=NULL, fileEncoding="UTF-8-BOM"), warning=function(w) {
    if (grepl("^incomplete final line found by readTableHeader", conditionMessage(w))) invokeRestart("muffleWarning")
    else fail("input_read_warning")
})
if (nrow(tab) != length(widths)-1L || ncol(tab) != widths[[1L]]) fail("input_dimension_integrity")
if (anyDuplicated(names(tab))) fail("duplicate_column_names")
if (sum(names(tab) == "Protein IDs") != 1L) fail("required_protein_ids")
indices <- which(startsWith(names(tab), paste0(family, " ")))
if (!length(indices) || any(nchar(substring(names(tab)[indices], nchar(family)+2L)) == 0L)) fail("missing_sample_columns")
raw <- as.matrix(tab[, indices, drop=FALSE]); blank <- raw == ""
valid <- grepl("^[+]?(?:[0-9]+(?:\\.[0-9]*)?|\\.[0-9]+)(?:[eE][+-]?[0-9]+)?$", raw, perl=TRUE)
dim(valid) <- dim(raw)
if (any(!blank & !valid)) fail("invalid_nonnegative_numeric_token")
numeric <- matrix(NA_real_, nrow(raw), ncol(raw), dimnames=dimnames(raw))
numeric[!blank] <- as.numeric(raw[!blank])
if (any(!is.finite(numeric[!blank]))) fail("numeric_out_of_range")
mantissa <- sub("[eE].*$", "", raw)
dim(mantissa) <- dim(raw)
if (any(!blank & numeric == 0 & grepl("[1-9]", mantissa), na.rm=TRUE)) fail("numeric_underflow")
zero <- !blank & numeric == 0; zero[is.na(zero)] <- FALSE
positive <- !blank & numeric > 0; positive[is.na(positive)] <- FALSE
qc <- data.frame(column_ordinal=indices-1L, name_raw=names(tab)[indices],
  sample_label_raw=substring(names(tab)[indices], nchar(family)+2L),
  records=nrow(raw), positive=colSums(positive), zero=colSums(zero), blank=colSums(blank),
  exact_zero_literal=colSums(raw == "0"), numeric_sum=colSums(numeric, na.rm=TRUE),
  positive_median=vapply(seq_along(indices), function(j) if(any(positive[,j])) stats::median(numeric[positive[,j],j]) else NA_real_, numeric(1)),
  check.names=FALSE, row.names=NULL)
if (any(!is.finite(qc$numeric_sum))) fail("numeric_sum_out_of_range")
if (any(qc$positive + qc$zero + qc$blank != nrow(raw))) fail("internal_partition")
summary <- data.frame(input_rows=nrow(tab), input_columns=ncol(tab), quant_family=family,
  selected_columns=ncol(raw), selected_cells=length(raw), positive=sum(positive), zero=sum(zero),
  blank=sum(blank), exact_zero_literal=sum(raw == "0"), original_zero_transformed=0L,
  imputed_values=0L, filtered_rows=0L, removed_samples=0L, numeric_representation="R_double_17_significant_digits",
  check.names=FALSE)
dir.create(output, recursive=FALSE)
prefix <- data.frame(record_number=seq_len(nrow(tab)), protein_ids_raw=tab[["Protein IDs"]], check.names=FALSE)
write <- function(x, name) utils::write.table(x, file=file.path(output, name), sep="\t", quote=TRUE,
    row.names=FALSE, col.names=TRUE, na="NA", qmethod="double", fileEncoding="UTF-8")
rendered <- matrix("NA", nrow(raw), ncol(raw), dimnames=dimnames(raw))
rendered[!blank] <- sprintf("%.17g", numeric[!blank])
write(cbind(prefix, as.data.frame(raw, check.names=FALSE)), "raw-matrix.tsv")
write(cbind(prefix, as.data.frame(rendered, check.names=FALSE)), "numeric-matrix.tsv")
write(cbind(prefix, as.data.frame(blank, check.names=FALSE)), "blank-mask.tsv")
write(cbind(prefix, as.data.frame(zero, check.names=FALSE)), "zero-mask.tsv")
write(qc, "sample-qc.tsv"); write(summary, "summary.tsv")
writeLines(capture.output(sessionInfo()), file.path(output, "R-session-info.txt"))
cat("RAW_QC_COMPLETE\n")
