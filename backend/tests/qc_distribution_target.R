# One finite new actual-data target. No prior preprocessor/Median/DE invocation.
args <- commandArgs(trailingOnly=TRUE)
stopifnot(length(args)==5L,as.character(getRversion())=="4.6.1")
source(args[[1L]],local=TRUE)
parent <- dget(args[[3L]]);design <- dget(args[[4L]])
stopifnot(parent$processed_dataset_version_id=="2fc0a8e6-ef3d-4afe-a15c-f27043b00227",
  design$sample_design_version_id=="4cf9e303-46a1-4c04-969c-6400edc5490c")
before <- serialize(list(parent,design),NULL)
result <- withCallingHandlers(qc_saved_distributions(args[[2L]],parent,args[[5L]],design),
  warning=function(w)stop("qc_distribution_warning",call.=FALSE))
stopifnot(identical(before,serialize(list(parent,design),NULL)))
read <- function(path) utils::read.delim(path,check.names=FALSE,quote="\"",comment.char="",
  colClasses="character",na.strings=character(0),stringsAsFactors=FALSE)
original <- read(file.path(args[[2L]],"working-matrix.tsv"))
long <- read(file.path(args[[5L]],"plot-long-data.tsv"))
counts <- read(file.path(args[[5L]],"sample-counts.tsv"))
box <- read(file.path(args[[5L]],"boxplot-statistics.tsv"))
density <- read(file.path(args[[5L]],"density-statistics.tsv"))
n <- nrow(original);samples <- names(original)[-(1:2)];s <- length(samples)
stopifnot(n==2252L,s==21L,nrow(long)==n*s,
  identical(long$protein_ids_raw,rep(original$protein_ids_raw,s)),
  identical(long$record_number,rep(original$record_number,s)),
  identical(long$sample_name,rep(samples,each=n)),
  identical(long$working_NA,as.character(long$log2_intensity=="NA")),
  sum(as.integer(counts$observed_finite_n))+sum(as.integer(counts$NA_n))==n*s,
  all(as.integer(counts$imputed_n)==0L),nrow(box)==sum(as.integer(counts$observed_finite_n)>0L),
  nrow(density)==sum(counts$density_eligible=="TRUE")*512L)
for (j in seq_len(s)) {
  have <- long[long$sample_ordinal==as.character(j),,drop=FALSE]
  missing <- original[[j+2L]]=="NA"
  expected <- as.numeric(original[[j+2L]][!missing])
  stopifnot(identical(have$log2_intensity[missing],rep("NA",sum(missing))),
    identical(as.numeric(have$log2_intensity[!missing]),expected),
    as.integer(counts$observed_finite_n[[j]])==length(expected),
    as.integer(counts$NA_n[[j]])==sum(missing))
  b <- box[box$sample_ordinal==as.character(j),,drop=FALSE]
  if (length(expected)) stopifnot(abs(as.numeric(b$middle)-stats::median(expected))<1e-12)
  d <- density[density$sample_ordinal==as.character(j),,drop=FALSE]
  if (nrow(d)) stopifnot(all(as.integer(d$n)==length(expected)))
}
expected_outputs <- sort(c("plot-long-data.tsv","sample-counts.tsv","group-label-map.tsv",
  "boxplot-statistics.tsv","density-statistics.tsv","parameters.R.txt","full-provenance.rds",
  "boxplot.svg","density.svg","R-session-info.txt"))
stopifnot(identical(sort(list.files(args[[5L]])),expected_outputs))
for (name in c("boxplot.svg","density.svg")) {
  text <- paste(readLines(file.path(args[[5L]],name),warn=FALSE),collapse="\n")
  stopifnot(grepl("<svg",text,fixed=TRUE),grepl("</svg>",text,fixed=TRUE),
    !grepl("<image",text,fixed=TRUE),!grepl("<script",text,fixed=TRUE))
}
cat("QC_DISTRIBUTIONS_ACTUAL_TARGET_PASS\n")
cat("rows=",n," samples=",s," full_cells=",nrow(long)," finite=",result$plotted_finite_cells,
  " NA=",sum(as.integer(counts$NA_n))," density_eligible_samples=",result$density_eligible_samples,
  " box_stat_rows=",result$box_native_rows," density_stat_rows=",result$density_native_rows,"\n",sep="")
