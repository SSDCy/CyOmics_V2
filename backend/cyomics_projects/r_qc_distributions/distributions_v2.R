# Versioned saved-log2 QC v2: standard original ggplot2 statistics, SVG only.
# No normalization, group inference, imputation, fit, HTTP or publication.
qc_saved_distributions_v2 <- function(input_dir, parent, output_dir, design=NULL,
                                      snapshot_bytes=NULL) {
  need <- function(ok,code="qc_distribution_refused") if (!isTRUE(ok)) stop(code,call.=FALSE)
  need(requireNamespace("ggplot2",quietly=TRUE) && requireNamespace("digest",quietly=TRUE))
  need(as.character(utils::packageVersion("ggplot2"))=="4.0.3" &&
    as.character(utils::packageVersion("digest"))=="0.6.39" && capabilities("cairo"))
  need(!file.exists(output_dir) && !dir.exists(output_dir))
  p <- parent$parameters
  need(is.list(parent) && is.list(p) && identical(p$output_scale,"log2") &&
    is.character(p$normalization) && length(p$normalization)==1L &&
    p$normalization %in% c("none","median","total","quantile") && identical(p$imputation,"none") &&
    identical(p$log2,"positive_only") && identical(p$zero_policy,"to_NA") &&
    identical(p$blank_policy,"to_NA") && is.numeric(p$pseudocount) &&
    length(p$pseudocount)==1L && !is.na(p$pseudocount) && p$pseudocount==0 &&
    identical(parent$quant_family,"LFQ intensity"),"unsupported_parent_scale")
  saved_design_snapshot <- design
  # The CLI receives the exact full saved envelope; the original plotting policy
  # uses this explicit projection. Full envelope/raw snapshots remain in provenance.
  if (!is.null(design)) {
    need(is.list(design) && is.list(design$record) && is.list(design$record$design),
      "invalid_saved_design_envelope")
    envelope <- design; record_design <- envelope$record$design
    need(identical(envelope$project_id,parent$project_id) &&
      identical(envelope$dataset_version_id,parent$parent_dataset_version_id) &&
      identical(envelope$quant_family,parent$quant_family),"invalid_saved_design_binding")
    design <- list(project_id=envelope$project_id,dataset_version_id=envelope$dataset_version_id,
      quant_family=envelope$quant_family,sample_design_version_id=envelope$record$sample_design_version_id,
      design_sha256=envelope$record$design_sha256,rows=record_design$rows)
  }
  # Fixed original Python manifest order; never locale-sort this identity.
  names_expected <- c("R-session-info.txt","filter-log.tsv","imputed-mask.tsv",
    "original-blank-mask.tsv","original-zero-mask.tsv","parameters.json","raw-retained.tsv",
    "row-map.tsv","summary.tsv","working-NA-mask.tsv","working-matrix.tsv")
  pins <- parent$artifacts
  need(is.data.frame(pins) && identical(as.character(pins$name),names_expected) &&
    identical(sort(names(pins),method="radix"),sort(c("name","bytes","sha256","media_type"),method="radix")) &&
    is.numeric(pins$bytes) && all(is.finite(pins$bytes) & pins$bytes>=1 & pins$bytes<=26214400) &&
    sum(pins$bytes)<=134217728 && all(grepl("^[0-9a-f]{64}$",pins$sha256)))
  hash <- function(path) digest::digest(file=path,algo="sha256",serialize=FALSE)
  verify_inputs <- function() for (j in seq_len(nrow(pins))) {
    path <- file.path(input_dir,pins$name[[j]])
    need(file.exists(path) && !isTRUE(file.info(path)$isdir) &&
      file.info(path)$size==pins$bytes[[j]] && hash(path)==pins$sha256[[j]],"input_artifact_integrity_failed")
  }
  verify_inputs()
  read <- function(name) utils::read.delim(file.path(input_dir,name),check.names=FALSE,
    quote="\"",comment.char="",colClasses="character",na.strings=character(0),
    strip.white=FALSE,stringsAsFactors=FALSE)
  working <- read("working-matrix.tsv")
  need(nrow(working)>=1L && nrow(working)<=10000L && ncol(working)>=3L &&
    ncol(working)<=514L && nrow(working)*(ncol(working)-2L)<=2000000L)
  need(identical(names(working)[1:2],c("record_number","protein_ids_raw")) &&
    !anyNA(working) && !anyDuplicated(names(working)) && all(nzchar(names(working))))
  need(all(grepl("^[1-9][0-9]*$",working$record_number)))
  record <- as.integer(working$record_number)
  need(!anyNA(record) && all(record<=10000L) && !anyDuplicated(record) && !is.unsorted(record,strictly=TRUE))
  sample_names <- names(working)[-(1:2)]; n <- nrow(working); s <- length(sample_names)
  need(s<=64L,"too_many_samples_for_distribution_panel")
  token <- as.matrix(working[-(1:2)])
  missing <- token=="NA"
  need(all(missing | grepl("^[+-]?([0-9]+(\\.[0-9]*)?|\\.[0-9]+)([eE][+-]?[0-9]+)?$",token)))
  x <- matrix(NA_real_,n,s,dimnames=dimnames(token));x[!missing] <- as.numeric(token[!missing])
  need(all(is.finite(x[!missing])))
  masks <- list()
  for (name in c("original-blank","original-zero","working-NA","imputed")) {
    tab <- read(paste0(name,"-mask.tsv"))
    need(identical(names(tab),names(working)) && identical(dim(tab),dim(working)) &&
      identical(tab[1:2],working[1:2]) && all(as.matrix(tab[-(1:2)]) %in% c("TRUE","FALSE")))
    masks[[name]] <- as.matrix(tab[-(1:2)])=="TRUE"
  }
  need(!any(masks$imputed) && !any(masks[["original-blank"]]&masks[["original-zero"]]) &&
    identical(unname(missing),unname(masks[["working-NA"]])) &&
    identical(unname(missing),unname(masks[["original-blank"]]|masks[["original-zero"]])))
  group_labels <- rep("Ungrouped (no confirmed saved design)",s)
  source_ordinals <- rep(NA_integer_,s)
  grouping <- "ungrouped_no_confirmed_saved_design"
  if (!is.null(design)) {
    need(identical(design$project_id,parent$project_id) &&
      identical(design$dataset_version_id,parent$parent_dataset_version_id) &&
      identical(design$quant_family,parent$quant_family) &&
      is.character(design$sample_design_version_id) && length(design$sample_design_version_id)==1L &&
      is.character(design$design_sha256) && grepl("^[0-9a-f]{64}$",design$design_sha256))
    rows <- design$rows
    need(is.data.frame(rows) && nrow(rows)==s && identical(rows$name_raw,sample_names) &&
      is.logical(rows$confirmed) && all(rows$confirmed) && is.character(rows$group) &&
      !anyNA(rows$group) && all(nzchar(trimws(rows$group))) &&
      is.numeric(rows$column_ordinal) && !anyNA(rows$column_ordinal) &&
      !anyDuplicated(rows$column_ordinal) && !is.unsorted(rows$column_ordinal,strictly=TRUE),
      "invalid_saved_design_binding")
    group_labels <- rows$group;source_ordinals <- rows$column_ordinal
    grouping <- "confirmed_saved_design_only"
  }
  # ASCII display aliases never replace original saved group identity in data.
  group_levels <- unique(group_labels)
  display_groups <- if (is.null(design)) "Ungrouped" else paste("Group",seq_along(group_levels))
  group_mapping <- data.frame(plot_group=display_groups,group_label_raw=group_levels,stringsAsFactors=FALSE)
  plot_groups <- display_groups[match(group_labels,group_levels)]
  observed <- colSums(!missing)
  distinct <- vapply(seq_len(s),function(j) length(unique(x[!missing[,j],j])),integer(1))
  density_ok <- observed>=2L & distinct>=2L
  density_status <- ifelse(observed<2L,"insufficient_finite_n",ifelse(distinct<2L,"constant_finite_values","eligible"))
  need(any(observed>0L),"no_finite_observations")
  need(any(density_ok),"no_sample_density_estimable")
  counts <- data.frame(sample_ordinal=seq_len(s),source_column_ordinal=source_ordinals,
    sample_name_raw=sample_names,group_label=group_labels,plot_group=plot_groups,input_rows=n,
    observed_finite_n=as.integer(observed),NA_n=as.integer(colSums(missing)),
    original_blank_n=as.integer(colSums(masks[["original-blank"]])),
    original_zero_n=as.integer(colSums(masks[["original-zero"]])),
    imputed_n=as.integer(colSums(masks$imputed)),distinct_finite_n=distinct,
    density_eligible=density_ok,density_status=density_status,
    density_bandwidth_nrd0=vapply(seq_len(s),function(j) if (density_ok[[j]])
      stats::bw.nrd0(x[!missing[,j],j]) else NA_real_,numeric(1)),check.names=FALSE)
  need(n*s*256+s*sum(nchar(working$protein_ids_raw,type="bytes"))+
    n*sum(nchar(sample_names,type="bytes")+nchar(group_labels,type="bytes"))<=67108864,
    "long_data_resource_limit")
  long <- data.frame(record_number=rep(record,s),protein_ids_raw=rep(working$protein_ids_raw,s),
    sample_ordinal=rep(seq_len(s),each=n),source_column_ordinal=rep(source_ordinals,each=n),
    sample_name=rep(sample_names,each=n),group_label=rep(group_labels,each=n),plot_group=rep(plot_groups,each=n),
    log2_intensity=as.vector(x),working_NA=as.vector(missing),
    original_blank=as.vector(masks[["original-blank"]]),original_zero=as.vector(masks[["original-zero"]]),
    imputed=as.vector(masks$imputed),check.names=FALSE)
  long$sample_name <- factor(long$sample_name,levels=sample_names)
  long$group_label <- factor(long$group_label,levels=unique(group_labels))
  long$plot_group <- factor(long$plot_group,levels=display_groups)
  finite <- long[is.finite(long$log2_intensity),,drop=FALSE]
  density_data <- finite[density_ok[finite$sample_ordinal],,drop=FALSE]
  method_label <- c(none="None",median="Median",total="Total",quantile="Quantile")[[p$normalization]]
  subtitle <- paste0("Saved log2 LFQ; normalization=",method_label,"; finite n=",sum(observed),
    "; NA excluded from statistics=",sum(missing),"; imputation=None")
  box <- ggplot2::ggplot(finite,ggplot2::aes(x=sample_name,y=log2_intensity,fill=plot_group))+
    ggplot2::geom_boxplot(coef=1.5,outlier.size=0.55,outlier.alpha=0.35,width=0.65)+
    ggplot2::scale_x_discrete(limits=sample_names,drop=FALSE)+
    ggplot2::labs(title="Sample intensity boxplots",subtitle=subtitle,x="Original sample identity",y="Saved log2 intensity",fill="Saved group (see group-label-map.tsv)")+
    ggplot2::theme_bw(base_size=10)+ggplot2::theme(axis.text.x=ggplot2::element_text(angle=90,hjust=1,vjust=0.5,size=7),legend.position="bottom")
  density <- ggplot2::ggplot(density_data,ggplot2::aes(x=log2_intensity,colour=sample_name,group=sample_name))+
    ggplot2::geom_density(bw="nrd0",adjust=1,kernel="gaussian",n=512,trim=FALSE,linewidth=0.65)+
    ggplot2::labs(title="Sample intensity densities",subtitle=paste0(subtitle,"; eligible samples=",sum(density_ok),"/",s),
      x="Saved log2 intensity",y="Density (each sample integrates to 1)",colour="Original sample identity")+
    ggplot2::theme_bw(base_size=10)+ggplot2::theme(legend.position="bottom")+
    ggplot2::guides(colour=ggplot2::guide_legend(ncol=3))
  if (!is.null(design)) density <- density+ggplot2::facet_wrap(ggplot2::vars(plot_group),ncol=3)
  box_built <- ggplot2::ggplot_build(box)
  density_built <- ggplot2::ggplot_build(density)
  box_stats <- box_built$data[[1L]]
  box_stats$sample_ordinal <- as.integer(box_stats$x)
  box_stats$sample_name_raw <- sample_names[box_stats$sample_ordinal]
  box_stats$outliers <- vapply(box_stats$outliers,function(v)paste(sprintf("%.17g",v),collapse=";"),character(1))
  density_stats <- density_built$data[[1L]]
  colours <- density_built$plot$scales$get_scales("colour")$map(sample_names)
  density_stats$sample_ordinal <- match(density_stats$colour,colours)
  need(!anyNA(density_stats$sample_ordinal))
  density_stats$sample_name_raw <- sample_names[density_stats$sample_ordinal]
  parameters <- list(contract="saved_log2_qc_distributions_v2",input_scale="log2",display_scale="log2",
    normalization=p$normalization,source_parameters=p,imputation="none",pseudocount=0,source_version=parent$processed_dataset_version_id,
    source_manifest_sha256=parent$manifest_sha256,grouping=grouping,
    sample_design_version_id=if(is.null(design)) NULL else design$sample_design_version_id,
    sample_design_sha256=if(is.null(design)) NULL else design$design_sha256,
    group_display="Stable ASCII Group 1..k aliases; original labels preserved in mapping, counts, long data and full design",
    boxplot=list(implementation="ggplot2::geom_boxplot/stat_boxplot",coef=1.5,outliers="all retained in long data and native statistics"),
    density=list(implementation="ggplot2::geom_density/stat_density",bw="nrd0",adjust=1,kernel="gaussian",n=512,
      trim=FALSE,minimum_finite_n=2L,minimum_distinct_finite_n=2L),
    finite_only_for_statistics=TRUE,all_original_cells_exported=TRUE,
    SVG=list(width_inches=16,height_inches=10,device="grDevices::svg",family="sans"),
    R_version=R.version.string,ggplot2_version=as.character(utils::packageVersion("ggplot2")),
    digest_version=as.character(utils::packageVersion("digest")),published=FALSE,OwnerHTTP_verified=FALSE)
  need(dir.create(output_dir,recursive=FALSE,mode="0700"))
  write <- function(value,name) {
    for (j in seq_along(value)) if (is.numeric(value[[j]])) {
      v <- value[[j]];value[[j]] <- ifelse(is.na(v),"NA",sprintf("%.17g",v))
    }
    utils::write.table(value,file.path(output_dir,name),sep="\t",quote=TRUE,row.names=FALSE,
      na="NA",qmethod="double",eol="\n",fileEncoding="UTF-8")
  }
  render <- function(built,name) {
    grDevices::svg(file.path(output_dir,name),width=16,height=10,family="sans",bg="white")
    device <- grDevices::dev.cur()
    on.exit(if(device %in% grDevices::dev.list()) grDevices::dev.off(device),add=TRUE)
    grid::grid.draw(ggplot2::ggplot_gtable(built));grDevices::dev.off(device)
  }
  write(long,"plot-long-data.tsv");write(counts,"sample-counts.tsv");write(group_mapping,"group-label-map.tsv")
  write(box_stats,"boxplot-statistics.tsv");write(density_stats,"density-statistics.tsv")
  dput(parameters,file=file.path(output_dir,"parameters.R.txt"))
  saveRDS(list(parent=parent,design=saved_design_snapshot,input_snapshot_bytes=snapshot_bytes,parameters=parameters),file.path(output_dir,"full-provenance.rds"),version=3)
  render(box_built,"boxplot.svg");render(density_built,"density.svg")
  writeLines(utils::capture.output(utils::sessionInfo()),file.path(output_dir,"R-session-info.txt"))
  verify_inputs()
  output_names <- c("R-session-info.txt","boxplot-statistics.tsv","boxplot.svg",
    "density-statistics.tsv","density.svg","full-provenance.rds","group-label-map.tsv",
    "parameters.R.txt","plot-long-data.tsv","sample-counts.tsv")
  need(identical(sort(list.files(output_dir),method="radix"),output_names),"unexpected_output_files")
  sizes <- file.info(file.path(output_dir,output_names))$size
  limits <- ifelse(grepl("\\.svg$",output_names),4194304,16777216)
  need(all(is.finite(sizes)&sizes>0&sizes<=limits)&sum(sizes)<=33554432,"output_byte_limit")
  invisible(list(counts=counts,parameters=parameters,rows=n,samples=s,full_cells=nrow(long),
    plotted_finite_cells=nrow(finite),density_eligible_samples=sum(density_ok),
    box_native_rows=nrow(box_stats),density_native_rows=nrow(density_stats)))
}


# Fixed four-argument CLI. JSON is data, never R source. No dget/eval or data
# discovery; original Owner Binder must have validated the saved snapshots first.
qc_distributions_v2_cli <- function(args=commandArgs(trailingOnly=TRUE)) {
  need <- function(ok,code="qc_distribution_v2_cli_refused") if (!isTRUE(ok)) stop(code,call.=FALSE)
  need(length(args)==4L && all(nzchar(args)),"fixed_four_arguments_required")
  need(requireNamespace("jsonlite",quietly=TRUE) &&
    as.character(utils::packageVersion("jsonlite"))=="2.0.0","jsonlite_2_0_0_required")
  input_dir <- args[[1L]];parent_path <- args[[2L]];design_path <- args[[3L]];output_dir <- args[[4L]]
  need(dir.exists(input_dir) && basename(parent_path)=="parent.json" &&
    (design_path=="-" || basename(design_path)=="design.json"),"fixed_snapshot_names_required")
  need(!file.exists(output_dir) && !dir.exists(output_dir),"output_directory_must_not_exist")
  input_dir <- normalizePath(input_dir,mustWork=TRUE)
  read_snapshot <- function(path,cap) {
    need(file.exists(path) && !isTRUE(file.info(path)$isdir) &&
      is.finite(file.info(path)$size) && file.info(path)$size>0 && file.info(path)$size<=cap,
      "snapshot_byte_limit")
    con <- file(path,"rb");on.exit(close(con),add=TRUE)
    bytes <- readBin(con,what="raw",n=cap+1L)
    need(length(bytes)==file.info(path)$size && !any(bytes==as.raw(0)),"snapshot_changed")
    text <- rawToChar(bytes)
    need(!is.na(iconv(text,from="UTF-8",to="UTF-8",sub=NA)),"snapshot_utf8_required")
    # simplifyDataFrame=TRUE deliberately yields the native data.frames required
    # by the original artifacts and saved design rows interfaces.
    value <- jsonlite::fromJSON(text,simplifyVector=TRUE,simplifyDataFrame=TRUE,simplifyMatrix=FALSE)
    need(is.list(value) && !is.data.frame(value),"snapshot_object_required")
    list(path=normalizePath(path,mustWork=TRUE),bytes=bytes,value=value)
  }
  parent_snapshot <- read_snapshot(parent_path,65536L)
  design_snapshot <- if (design_path=="-") NULL else read_snapshot(design_path,262144L)
  parent <- parent_snapshot$value
  need(is.character(parent$processed_dataset_version_id) && length(parent$processed_dataset_version_id)==1L &&
    is.character(parent$project_id) && length(parent$project_id)==1L &&
    is.character(parent$manifest_sha256) && length(parent$manifest_sha256)==1L &&
    grepl("^[0-9a-f]{64}$",parent$manifest_sha256),"parent_identity_required")
  need(is.data.frame(parent$artifacts),"artifact_dataframe_required")
  if (!is.null(design_snapshot)) need(is.data.frame(design_snapshot$value$record$design$rows),"design_rows_dataframe_required")
  paths <- c(parent_snapshot$path,if(!is.null(design_snapshot)) design_snapshot$path)
  snapshot_pins <- lapply(seq_along(paths),function(i) {
    bytes <- if(i==1L) parent_snapshot$bytes else design_snapshot$bytes
    list(name=basename(paths[[i]]),bytes=length(bytes),sha256=digest::digest(bytes,algo="sha256",serialize=FALSE))
  })
  result <- qc_saved_distributions_v2(input_dir,parent,output_dir,
    design=if(is.null(design_snapshot)) NULL else design_snapshot$value,
    snapshot_bytes=list(parent_json=parent_snapshot$bytes,
      design_json=if(is.null(design_snapshot)) NULL else design_snapshot$bytes))
  # Preserve/fresh-check the exact immutable input snapshots, as for all11 artifacts.
  for (i in seq_along(paths)) {
    bytes <- if(i==1L) parent_snapshot$bytes else design_snapshot$bytes
    con <- file(paths[[i]],"rb");now <- tryCatch(readBin(con,what="raw",n=length(bytes)+1L),finally=close(con))
    need(identical(now,bytes),"snapshot_changed_after_render")
  }
  output_names <- c("R-session-info.txt","boxplot-statistics.tsv","boxplot.svg",
    "density-statistics.tsv","density.svg","full-provenance.rds","group-label-map.tsv",
    "parameters.R.txt","plot-long-data.tsv","sample-counts.tsv")
  artifacts <- lapply(output_names,function(name) {
    path <- file.path(output_dir,name)
    list(name=name,bytes=unname(file.info(path)$size),sha256=digest::digest(file=path,algo="sha256",serialize=FALSE))
  })
  descriptor <- list(contract="saved_log2_qc_distributions_outputs_v2",schemaVersion=2L,
    processed_dataset_version_id=parent$processed_dataset_version_id,project_id=parent$project_id,
    parent_manifest_sha256=parent$manifest_sha256,normalization=parent$parameters$normalization,
    input_snapshot_pins=snapshot_pins,input_manifest=parent$artifacts,artifacts=artifacts,
    shape=list(rows=result$rows,samples=result$samples,cells=result$full_cells),
    statistics=list(finite_cells=result$plotted_finite_cells,density_eligible_samples=result$density_eligible_samples,
      box_native_rows=result$box_native_rows,density_native_rows=result$density_native_rows),
    published=FALSE,OwnerHTTP_verified=FALSE)
  raw <- charToRaw(enc2utf8(as.character(jsonlite::toJSON(descriptor,auto_unbox=TRUE,null="null",na="null",digits=NA))))
  need(length(raw)>0L && length(raw)<=65536L,"descriptor_byte_limit")
  need(sum(file.info(file.path(output_dir,output_names))$size)+length(raw)<=33554432,
    "total_output_byte_limit")
  path <- file.path(output_dir,"output-manifest.json")
  con <- file(path,"wb");tryCatch(writeBin(raw,con),finally=close(con))
  need(sum(file.info(file.path(output_dir,c(output_names,"output-manifest.json")))$size)<=33554432,
    "total_output_byte_limit")
  invisible(result)
}

if (sys.nframe()==0L) {
  status <- tryCatch({qc_distributions_v2_cli();0L},error=function(error) {
    message("qc_distribution_v2_failed");1L
  })
  quit(save="no",status=status,runLast=FALSE)
}
