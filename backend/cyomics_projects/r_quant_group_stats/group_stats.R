# Inert on source. Full saved None data -> descriptive raw CV/group tables only.
quant_group_stats_cv <- function(input_dir,parent,design,output_dir) {
  need <- function(ok,code="group_stats_refused") if (!isTRUE(ok)) stop(code,call.=FALSE)
  need(requireNamespace("digest",quietly=TRUE) &&
    as.character(utils::packageVersion("digest"))=="0.6.39")
  need(!file.exists(output_dir) && !dir.exists(output_dir))
  p <- parent$parameters
  need(is.list(p) && identical(p$output_scale,"log2") && identical(p$normalization,"none") &&
    identical(p$log2,"positive_only") && identical(p$imputation,"none") &&
    identical(p$zero_policy,"to_NA") && identical(p$blank_policy,"to_NA") &&
    is.numeric(p$pseudocount) && length(p$pseudocount)==1L && p$pseudocount==0 &&
    parent$quant_family %in% c("LFQ intensity","Intensity"),"unsupported_saved_parent")
  artifact_names <- c("R-session-info.txt","filter-log.tsv","imputed-mask.tsv",
    "original-blank-mask.tsv","original-zero-mask.tsv","parameters.json","raw-retained.tsv",
    "row-map.tsv","summary.tsv","working-NA-mask.tsv","working-matrix.tsv")
  pins <- parent$artifacts
  need(is.data.frame(pins) && identical(as.character(pins$name),artifact_names))
  verify_inputs <- function() for (j in seq_len(nrow(pins))) {
    path <- file.path(input_dir,pins$name[[j]])
    need(file.exists(path) && !isTRUE(file.info(path)$isdir) && file.info(path)$size==pins$bytes[[j]] &&
      digest::digest(file=path,algo="sha256",serialize=FALSE)==pins$sha256[[j]],"source_byte_integrity_failed")
  }
  verify_inputs()
  read <- function(name) utils::read.delim(file.path(input_dir,name),check.names=FALSE,
    quote="\"",comment.char="",colClasses="character",na.strings=character(0),
    strip.white=FALSE,stringsAsFactors=FALSE)
  working <- read("working-matrix.tsv");raw_table <- read("raw-retained.tsv")
  need(nrow(working)>=1L && nrow(working)<=10000L && ncol(working)>=3L && ncol(working)<=514L &&
    nrow(working)*(ncol(working)-2L)<=2000000L && !anyNA(working) && !anyNA(raw_table))
  need(identical(names(working)[1:2],c("record_number","protein_ids_raw")) &&
    identical(names(working),names(raw_table)) && identical(dim(working),dim(raw_table)) &&
    identical(working[1:2],raw_table[1:2]) && !anyDuplicated(names(working)))
  record <- working$record_number
  need(all(grepl("^[1-9][0-9]*$",record)))
  record <- as.integer(record)
  need(!anyNA(record) && all(record<=10000L) && !anyDuplicated(record) && !is.unsorted(record,strictly=TRUE))
  names_raw <- names(working)[-(1:2)];n <- nrow(working);s <- length(names_raw)
  raw_tokens <- as.matrix(raw_table[-(1:2)]);blank <- raw_tokens==""
  need(all(blank | grepl("^[+]?([0-9]+(\\.[0-9]*)?|\\.[0-9]+)([eE][+-]?[0-9]+)?$",raw_tokens)),"invalid_raw_numeric_token")
  raw <- matrix(NA_real_,n,s,dimnames=dimnames(raw_tokens))
  raw[!blank] <- as.numeric(raw_tokens[!blank]);need(all(is.finite(raw[!blank])),"nonfinite_raw_input")
  mantissa <- sub("[eE].*$","",raw_tokens);dim(mantissa) <- dim(raw_tokens)
  underflow <- !blank & raw==0 & grepl("[1-9]",mantissa);underflow[is.na(underflow)] <- FALSE
  need(!any(underflow),"raw_numeric_underflow")
  zero <- !blank & raw==0;zero[is.na(zero)] <- FALSE
  missing <- blank | zero
  token <- as.matrix(working[-(1:2)])
  need(all(token=="NA" | grepl("^[+-]?([0-9]+(\\.[0-9]*)?|\\.[0-9]+)([eE][+-]?[0-9]+)?$",token)))
  log2_values <- matrix(NA_real_,n,s,dimnames=dimnames(token))
  log2_values[token!="NA"] <- as.numeric(token[token!="NA"])
  need(identical(unname(token=="NA"),unname(missing)) && all(is.finite(log2_values[!missing])),"raw_log2_missing_mismatch")
  # Integrity check only, not another preprocessing output or transform.
  expected_log2 <- base::log2(raw[!missing])
  need(all(abs(log2_values[!missing]-expected_log2)<=1e-12*pmax(1,abs(expected_log2))),"raw_log2_scale_mismatch")
  masks <- list()
  for (name in c("original-blank","original-zero","working-NA","imputed")) {
    tab <- read(paste0(name,"-mask.tsv"))
    need(identical(names(tab),names(working)) && identical(dim(tab),dim(working)) &&
      identical(tab[1:2],working[1:2]) && all(as.matrix(tab[-(1:2)]) %in% c("TRUE","FALSE")))
    masks[[name]] <- as.matrix(tab[-(1:2)])=="TRUE"
  }
  need(identical(unname(masks[["original-blank"]]),unname(blank)) &&
    identical(unname(masks[["original-zero"]]),unname(zero)) &&
    identical(unname(masks[["working-NA"]]),unname(missing)) && !any(masks$imputed),"source_mask_mismatch")
  need(is.list(design) && identical(design$project_id,parent$project_id) &&
    identical(design$dataset_version_id,parent$parent_dataset_version_id) &&
    identical(design$quant_family,parent$quant_family),"saved_design_binding_required")
  samples <- design$rows
  need(is.data.frame(samples) && nrow(samples)==s && identical(samples$name_raw,names_raw) &&
    all(samples$confirmed) && !anyNA(samples) && all(nzchar(trimws(samples$group))) &&
    all(samples$replicate_type %in% c("biological","technical","unknown")) &&
    !anyDuplicated(samples$column_ordinal) && !is.unsorted(samples$column_ordinal,strictly=TRUE),
    "invalid_saved_design")
  groups <- unique(samples$group);g <- length(groups)
  need(n*g<=100000L && n*g*512+g*sum(nchar(working$protein_ids_raw,type="bytes"))+
    n*sum(nchar(groups,type="bytes"))<=67108864,"group_table_resource_limit")
  group_indices <- lapply(groups,function(label)which(samples$group==label))
  summary_stats <- function(v) {
    count <- length(v)
    if (!count) return(list(mean=NA_real_,median=NA_real_,sd=NA_real_,status="all_missing"))
    average <- base::mean(v);middle <- stats::median(v)
    deviation <- if(count>=2L) stats::sd(v) else NA_real_
    status <- if(!is.finite(average)) "nonfinite_mean" else if(!is.finite(middle)) "nonfinite_median" else
      if(count<2L) "insufficient_valid_n" else if(!is.finite(deviation)) "nonfinite_sd" else "estimable"
    list(mean=if(is.finite(average))average else NA_real_,median=if(is.finite(middle))middle else NA_real_,
      sd=if(is.finite(deviation))deviation else NA_real_,status=status)
  }
  results <- vector("list",n*g);position <- 0L
  for (i in seq_len(n)) for (k in seq_along(groups)) {
    index <- group_indices[[k]];valid <- !missing[i,index];count <- sum(valid)
    raw_stats <- summary_stats(raw[i,index][valid]);log_stats <- summary_stats(log2_values[i,index][valid])
    cv <- rsd <- NA_real_;cv_status <- raw_stats$status
    if (cv_status=="estimable") {
      if(raw_stats$mean<=0) cv_status <- "nonpositive_mean" else {
        value <- raw_stats$sd/raw_stats$mean
        if(!is.finite(value)) cv_status <- "nonfinite_cv" else {
          cv <- value;percent <- 100*value
          if(is.finite(percent))rsd <- percent else cv_status <- "nonfinite_rsd"
        }
      }
    }
    biological <- all(samples$replicate_type[index]=="biological")
    repeat_status <- if(!biological) "non_biological_or_unknown_metadata" else
      if(length(index)<2L) "insufficient_group_replicates" else if(count<2L) "insufficient_observed_replicates" else
      "descriptive_only_consistency_not_assessed"
    position <- position+1L
    results[[position]] <- data.frame(record_number=record[[i]],protein_ids_raw=working$protein_ids_raw[[i]],
      group_ordinal=k,group_label_raw=groups[[k]],n_total=length(index),n_valid=count,n_missing=sum(!valid),
      original_zero_n=sum(zero[i,index]),original_blank_n=sum(blank[i,index]),imputed_n=sum(masks$imputed[i,index]),
      effective_n=count,raw_scale="positive_intensity",raw_mean=raw_stats$mean,raw_median=raw_stats$median,
      raw_sd=raw_stats$sd,raw_summary_status=raw_stats$status,cv_raw=cv,rsd_percent_raw=rsd,cv_status=cv_status,
      log2_scale="saved_log2",log2_mean=log_stats$mean,log2_median=log_stats$median,log2_sd=log_stats$sd,
      log2_summary_status=log_stats$status,replicate_types=paste(samples$replicate_type[index],collapse="|"),
      all_declared_biological=biological,repeat_assessment=repeat_status,biological_consistency_passed=FALSE,
      check.names=FALSE,stringsAsFactors=FALSE)
  }
  table <- do.call(rbind,results)
  group_table <- data.frame(group_ordinal=seq_along(groups),group_label_raw=groups,
    n_samples=lengths(group_indices),replicate_types=vapply(group_indices,function(ix)paste(samples$replicate_type[ix],collapse="|"),character(1)),
    stringsAsFactors=FALSE)
  parameters <- list(contract="saved_group_raw_cv_v1",source_version=parent$processed_dataset_version_id,
    source_manifest_sha256=parent$manifest_sha256,sample_design_version=design$sample_design_version_id,
    sample_design_sha256=design$design_sha256,normalization="none",imputation="none",
    cv_scale="original_raw_positive_intensity",cv_formula="stats::sd(raw_observed)/base::mean(raw_observed)",
    rsd_formula="100*cv_raw",SD_denominator="n_valid-1",effective_n="observed finite positive values after original zero/blank masks",
    log2_summary="separate saved-log2 mean/median/sample-SD columns; never a CV",minimum_SD_CV_n=2L,
    all_missing="mean/median/SD/CV/RSD NA, not zero",singleton="mean/median allowed; SD/CV/RSD NA",
    nonfinite="nonfinite statistics become NA with explicit status; rows/counts preserved",
    group_order="saved design first appearance",row_order="all original retained record_number order x all groups",
    group_source="saved metadata only, no name inference",consistency="descriptive only, no independence/threshold/significance assessment",
    R_version=R.version.string,digest_version=as.character(utils::packageVersion("digest")),
    published=FALSE,OwnerHTTP_verified=FALSE)
  need(dir.create(output_dir,recursive=FALSE,mode="0700"))
  write <- function(value,name) {
    for(j in seq_along(value)) if(is.numeric(value[[j]])) {
      x <- value[[j]];value[[j]] <- ifelse(is.na(x),"NA",sprintf("%.17g",x))
    }
    utils::write.table(value,file.path(output_dir,name),sep="\t",quote=TRUE,row.names=FALSE,na="NA",qmethod="double",eol="\n",fileEncoding="UTF-8")
  }
  write(table,"group-statistics.tsv");write(group_table,"groups.tsv");write(samples,"sample-design.tsv")
  dput(parameters,file=file.path(output_dir,"parameters.R.txt"))
  saveRDS(list(parent=parent,design=design,parameters=parameters),file.path(output_dir,"full-provenance.rds"),version=3)
  writeLines(utils::capture.output(utils::sessionInfo()),file.path(output_dir,"R-session-info.txt"))
  verify_inputs()
  sizes <- file.info(file.path(output_dir,list.files(output_dir)))$size
  need(all(is.finite(sizes)&sizes>0&sizes<=33554432)&sum(sizes)<=134217728,"output_byte_limit")
  invisible(list(rows=n,groups=g,result_rows=nrow(table),samples=s,
    observed_cells=sum(!missing),missing_cells=sum(missing),table=table,parameters=parameters))
}
