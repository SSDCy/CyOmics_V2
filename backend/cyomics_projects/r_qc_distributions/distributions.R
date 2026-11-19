# Standalone saved-log2/None QC: standard ggplot2 statistics, SVG only.
# No normalization, group inference, imputation, fit, HTTP or publication.
qc_saved_distributions <- function(input_dir, parent, output_dir, design=NULL) {
  need <- function(ok,code="qc_distribution_refused") if (!isTRUE(ok)) stop(code,call.=FALSE)
  need(requireNamespace("ggplot2",quietly=TRUE) && requireNamespace("digest",quietly=TRUE))
  need(as.character(utils::packageVersion("ggplot2"))=="4.0.3" &&
    as.character(utils::packageVersion("digest"))=="0.6.39" && capabilities("cairo"))
  need(!file.exists(output_dir) && !dir.exists(output_dir))
  p <- parent$parameters
  need(is.list(parent) && is.list(p) && identical(p$output_scale,"log2") &&
    identical(p$normalization,"none") && identical(p$imputation,"none") &&
    identical(p$log2,"positive_only") && identical(p$zero_policy,"to_NA") &&
    identical(p$blank_policy,"to_NA") && identical(p$pseudocount,0) &&
    identical(parent$quant_family,"LFQ intensity"),"unsupported_parent_scale")
  # Fixed original Python manifest order; never locale-sort this identity.
  names_expected <- c("R-session-info.txt","filter-log.tsv","imputed-mask.tsv",
    "original-blank-mask.tsv","original-zero-mask.tsv","parameters.json","raw-retained.tsv",
    "row-map.tsv","summary.tsv","working-NA-mask.tsv","working-matrix.tsv")
  pins <- parent$artifacts
  need(is.data.frame(pins) && identical(as.character(pins$name),names_expected) &&
    all(c("bytes","sha256") %in% names(pins)))
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
  subtitle <- paste0("Saved log2 LFQ; normalization=None; finite n=",sum(observed),
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
  parameters <- list(contract="saved_log2_qc_distributions_v1",input_scale="log2",display_scale="log2",
    normalization="none",imputation="none",pseudocount=0,source_version=parent$processed_dataset_version_id,
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
  saveRDS(list(parent=parent,design=design,parameters=parameters),file.path(output_dir,"full-provenance.rds"),version=3)
  render(box_built,"boxplot.svg");render(density_built,"density.svg")
  writeLines(utils::capture.output(utils::sessionInfo()),file.path(output_dir,"R-session-info.txt"))
  verify_inputs()
  sizes <- file.info(file.path(output_dir,list.files(output_dir)))$size
  need(all(is.finite(sizes)&sizes>0&sizes<=33554432)&sum(sizes)<=134217728,"output_byte_limit")
  invisible(list(counts=counts,parameters=parameters,rows=n,samples=s,full_cells=nrow(long),
    plotted_finite_cells=nrow(finite),density_eligible_samples=sum(density_ok),
    box_native_rows=nrow(box_stats),density_native_rows=nrow(density_stats)))
}
