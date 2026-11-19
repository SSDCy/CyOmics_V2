# Inert on source: standard R PCA/Pearson diagnostics of saved None log2 data.
qc_pca_correlation_statistics <- function(x,record_number,protein_ids_raw,min_pairs=3L) {
  need <- function(ok) if(!isTRUE(ok))stop("pca_correlation_input_refused",call.=FALSE)
  need(is.matrix(x)&&is.numeric(x)&&nrow(x)>=1L&&ncol(x)>=1L&&
    !any(is.nan(x))&&all(is.na(x)|is.finite(x))&&length(record_number)==nrow(x)&&length(protein_ids_raw)==nrow(x))
  need(length(min_pairs)==1L&&is.finite(min_pairs)&&min_pairs>=3&&min_pairs==floor(min_pairs))
  observed <- !is.na(x);complete <- rowSums(observed)==ncol(x)
  row_sd <- vapply(seq_len(nrow(x)),function(i)if(complete[[i]]&&ncol(x)>=2L)stats::sd(x[i,])else NA_real_,numeric(1))
  reason <- ifelse(!complete,"missing_in_one_or_more_samples",ifelse(!is.finite(row_sd),"nonfinite_variance",ifelse(row_sd==0,"constant_across_samples","included")))
  included <- reason=="included"
  audit <- data.frame(record_number=record_number,protein_ids_raw=protein_ids_raw,
    finite_n=rowSums(observed),missing_n=rowSums(!observed),complete_case=complete,
    SD_across_samples=row_sd,eligible_PCA_row=included,included_PCA=included,PCA_reason=reason,stringsAsFactors=FALSE)
  pca_status <- if(ncol(x)<3L)"insufficient_samples" else if(any(complete&!is.finite(row_sd)))"nonfinite_feature_variance" else
    if(sum(included)<2L)"insufficient_eligible_proteins" else "estimated"
  fit <- NULL
  if(pca_status=="estimated") {
    matrix <- x[included,,drop=FALSE];rownames(matrix)<-paste0("protein_",record_number[included])
    fit <- tryCatch(stats::prcomp(t(matrix),center=TRUE,scale.=FALSE),error=function(e)NULL)
    if(is.null(fit)) pca_status<-"prcomp_not_estimable" else if(!all(is.finite(fit$x))||!all(is.finite(fit$rotation))||!all(is.finite(fit$sdev))) {
      pca_status<-"nonfinite_PCA_result";fit<-NULL
    } else if(!is.finite(sum(fit$sdev^2))||sum(fit$sdev^2)<=0) {pca_status<-"nonfinite_PCA_variance";fit<-NULL}
  }
  audit$included_PCA <- included & pca_status=="estimated"
  if(pca_status!="estimated")audit$PCA_reason[included] <- paste0("eligible_but_",pca_status)
  paired_n <- crossprod(1L*observed)
  correlation <- matrix(NA_real_,ncol(x),ncol(x),dimnames=list(colnames(x),colnames(x)))
  if(any(paired_n>=min_pairs)) {
    correlation <- withCallingHandlers(stats::cor(x,method="pearson",use="pairwise.complete.obs"),
      warning=function(w) {
        if(grepl("standard deviation is zero",conditionMessage(w),fixed=TRUE))invokeRestart("muffleWarning")
        else stop("unexpected_correlation_warning",call.=FALSE)
      })
  }
  reasons <- matrix("estimable",ncol(x),ncol(x));pairs <- vector("list",ncol(x)^2);k<-0L
  for(i in seq_len(ncol(x)))for(j in seq_len(ncol(x))) {
    valid <- observed[,i]&observed[,j];count<-sum(valid)
    sd_a<-sd_b<-NA_real_
    state<-"estimable"
    if(count<min_pairs)state<-"insufficient_paired_n" else {
      sd_a<-stats::sd(x[valid,i]);sd_b<-stats::sd(x[valid,j])
      if(!is.finite(sd_a)||!is.finite(sd_b))state<-"nonfinite_paired_variance" else
        if(sd_a==0||sd_b==0)state<-"constant_paired_sample" else
          if(!is.finite(correlation[i,j]))state<-"nonfinite_correlation"
    }
    if(state!="estimable")correlation[i,j]<-NA_real_
    reasons[i,j]<-state;k<-k+1L
    pairs[[k]]<-data.frame(sample_a_ordinal=i,sample_b_ordinal=j,paired_n=count,
      paired_sd_a=sd_a,paired_sd_b=sd_b,pearson_r=correlation[i,j],reason=state,stringsAsFactors=FALSE)
  }
  off_diagonal <- row(correlation)!=col(correlation)
  correlation_status <- if(!any(is.finite(correlation[off_diagonal])))"no_estimable_sample_pairs" else
    if(anyNA(correlation))"estimated_with_NA_pairs" else "estimated"
  list(pca_status=pca_status,fit=fit,row_audit=audit,included=included,paired_n=paired_n,
    correlation=correlation,pair_reasons=reasons,pair_statistics=do.call(rbind,pairs),correlation_status=correlation_status)
}

qc_saved_pca_correlation <- function(input_dir,parent,design,output_dir) {
  need <- function(ok,code="saved_pca_correlation_refused")if(!isTRUE(ok))stop(code,call.=FALSE)
  need(requireNamespace("digest",quietly=TRUE)&&requireNamespace("ggplot2",quietly=TRUE)&&requireNamespace("pheatmap",quietly=TRUE))
  need(as.character(utils::packageVersion("digest"))=="0.6.39"&&as.character(utils::packageVersion("ggplot2"))=="4.0.3"&&
    as.character(utils::packageVersion("pheatmap"))=="1.0.13"&&capabilities("cairo"))
  need(!dir.exists(output_dir)&&!file.exists(output_dir))
  p<-parent$parameters
  need(identical(p$output_scale,"log2")&&identical(p$normalization,"none")&&identical(p$imputation,"none")&&
    identical(p$log2,"positive_only")&&identical(p$zero_policy,"to_NA")&&identical(p$blank_policy,"to_NA")&&
    is.numeric(p$pseudocount)&&p$pseudocount==0&&identical(parent$quant_family,"LFQ intensity"),"unsupported_saved_parent")
  required<-c("R-session-info.txt","filter-log.tsv","imputed-mask.tsv","original-blank-mask.tsv","original-zero-mask.tsv",
    "parameters.json","raw-retained.tsv","row-map.tsv","summary.tsv","working-NA-mask.tsv","working-matrix.tsv")
  pins<-parent$artifacts;need(is.data.frame(pins)&&identical(as.character(pins$name),required))
  verify<-function()for(j in seq_len(nrow(pins))) {
    path<-file.path(input_dir,pins$name[[j]])
    need(file.exists(path)&&!isTRUE(file.info(path)$isdir)&&file.info(path)$size==pins$bytes[[j]]&&
      digest::digest(file=path,algo="sha256",serialize=FALSE)==pins$sha256[[j]],"source_byte_integrity_failed")
  }
  verify()
  read<-function(name)utils::read.delim(file.path(input_dir,name),check.names=FALSE,quote="\"",comment.char="",
    colClasses="character",na.strings=character(0),strip.white=FALSE,stringsAsFactors=FALSE)
  working<-read("working-matrix.tsv")
  need(nrow(working)>=1L&&nrow(working)<=10000L&&ncol(working)>=3L&&ncol(working)<=66L&&
    nrow(working)*(ncol(working)-2L)<=2000000L&&!anyNA(working)&&!anyDuplicated(names(working))&&
    identical(names(working)[1:2],c("record_number","protein_ids_raw")))
  need(all(grepl("^[1-9][0-9]*$",working$record_number)))
  record<-as.integer(working$record_number)
  need(!anyNA(record)&&all(record<=10000L)&&!anyDuplicated(record)&&!is.unsorted(record,strictly=TRUE))
  sample_names<-names(working)[-(1:2)];s<-length(sample_names)
  token<-as.matrix(working[-(1:2)]);missing<-token=="NA"
  need(all(missing|grepl("^[+-]?([0-9]+(\\.[0-9]*)?|\\.[0-9]+)([eE][+-]?[0-9]+)?$",token)))
  x<-matrix(NA_real_,nrow(token),ncol(token),dimnames=dimnames(token));x[!missing]<-as.numeric(token[!missing])
  need(all(is.finite(x[!missing])))
  masks<-list()
  for(name in c("original-blank","original-zero","working-NA","imputed")) {
    tab<-read(paste0(name,"-mask.tsv"))
    need(identical(names(tab),names(working))&&identical(dim(tab),dim(working))&&identical(tab[1:2],working[1:2])&&
      all(as.matrix(tab[-(1:2)])%in%c("TRUE","FALSE")))
    masks[[name]]<-as.matrix(tab[-(1:2)])=="TRUE"
  }
  need(!any(masks$imputed)&&!any(masks[["original-blank"]]&masks[["original-zero"]])&&
    identical(unname(missing),unname(masks[["working-NA"]]))&&
    identical(unname(missing),unname(masks[["original-blank"]]|masks[["original-zero"]])))
  samples<-design$rows
  need(identical(design$project_id,parent$project_id)&&identical(design$dataset_version_id,parent$parent_dataset_version_id)&&
    identical(design$quant_family,parent$quant_family)&&is.data.frame(samples)&&nrow(samples)==s&&
    identical(samples$name_raw,sample_names)&&all(samples$confirmed)&&!anyNA(samples)&&all(nzchar(trimws(samples$group)))&&
    !anyDuplicated(samples$column_ordinal)&&!is.unsorted(samples$column_ordinal,strictly=TRUE),"saved_design_binding_required")
  sample_keys<-sprintf("S%02d",seq_len(s));group_names<-unique(samples$group);group_keys<-sprintf("G%02d",seq_along(group_names))
  map<-data.frame(sample_key=sample_keys,sample_ordinal=seq_len(s),source_column_ordinal=samples$column_ordinal,
    sample_name_raw=sample_names,group_key=group_keys[match(samples$group,group_names)],group_label_raw=samples$group,
    replicate_type=samples$replicate_type,stringsAsFactors=FALSE)
  statistics<-qc_pca_correlation_statistics(x,record,working$protein_ids_raw,3L)
  need(dir.create(output_dir,mode="0700",recursive=FALSE))
  write<-function(value,name) {
    for(j in seq_along(value))if(is.numeric(value[[j]])){v<-value[[j]];value[[j]]<-ifelse(is.na(v),"NA",sprintf("%.17g",v))}
    utils::write.table(value,file.path(output_dir,name),sep="\t",quote=TRUE,row.names=FALSE,na="NA",qmethod="double",eol="\n",fileEncoding="UTF-8")
  }
  write(map,"sample-map.tsv");write(samples,"sample-design.tsv");write(statistics$row_audit,"PCA-row-audit.tsv")
  write(working[statistics$included,,drop=FALSE],"PCA-selected-source.tsv")
  write(working,"full-saved-matrix.tsv") # original token values, all rows/samples/NA
  pair_table<-statistics$pair_statistics
  pair_table$sample_a_key<-sample_keys[pair_table$sample_a_ordinal];pair_table$sample_b_key<-sample_keys[pair_table$sample_b_ordinal]
  pair_table$sample_a_name_raw<-sample_names[pair_table$sample_a_ordinal];pair_table$sample_b_name_raw<-sample_names[pair_table$sample_b_ordinal]
  write(pair_table,"correlation-pairs.tsv")
  wide<-function(matrix){colnames(matrix)<-sample_keys;data.frame(sample_key=sample_keys,sample_name_raw=sample_names,matrix,check.names=FALSE)}
  write(wide(statistics$correlation),"correlation-matrix.tsv");write(wide(statistics$paired_n),"correlation-paired-n.tsv")
  write(wide(statistics$pair_reasons),"correlation-reasons.tsv")
  render<-function(draw,name) {
    grDevices::svg(file.path(output_dir,name),width=13,height=10,family="sans",bg="white")
    device<-grDevices::dev.cur();on.exit(if(device%in%grDevices::dev.list())grDevices::dev.off(device),add=TRUE)
    draw();grDevices::dev.off(device)
  }
  explained<-numeric(0)
  if(statistics$pca_status=="estimated") {
    fit<-statistics$fit;variance<-fit$sdev^2;explained<-variance/sum(variance)
    scores<-cbind(map,as.data.frame(fit$x,check.names=FALSE))
    loading_prefix<-data.frame(feature_key=rownames(fit$rotation),record_number=record[statistics$included],
      protein_ids_raw=working$protein_ids_raw[statistics$included],stringsAsFactors=FALSE)
    write(scores,"PCA-scores.tsv");write(cbind(loading_prefix,as.data.frame(fit$rotation,check.names=FALSE)),"PCA-loadings.tsv")
    write(data.frame(component=paste0("PC",seq_along(fit$sdev)),sdev=fit$sdev,variance=variance,
      explained_fraction=explained,cumulative_fraction=cumsum(explained)),"PCA-variance.tsv")
    write(cbind(loading_prefix,center=as.numeric(fit$center)),"PCA-center.tsv")
    plot<-ggplot2::ggplot(scores,ggplot2::aes(x=PC1,y=PC2,colour=group_key))+
      ggplot2::geom_point(size=2.8)+ggplot2::geom_text(ggplot2::aes(label=sample_key),nudge_y=0.12,size=3,show.legend=FALSE,check_overlap=FALSE)+
      ggplot2::scale_colour_manual(values=stats::setNames(grDevices::hcl.colors(length(group_keys),"Dark3"),group_keys))+
      ggplot2::theme_bw(base_size=11)+ggplot2::labs(title="Sample PCA of saved log2 LFQ",subtitle=paste0("Complete-case variable protein rows: ",sum(statistics$included),"; centered, not variance-scaled; no imputation"),
        x=sprintf("PC1 (%.2f%%)",100*explained[[1L]]),y=sprintf("PC2 (%.2f%%)",100*explained[[2L]]),
        colour="Saved group alias (see sample-map.tsv)")+ggplot2::theme(legend.position="bottom")
    render(function()print(plot),"PCA.svg")
  }
  if(statistics$correlation_status!="no_estimable_sample_pairs") {
    matrix<-statistics$correlation;dimnames(matrix)<-list(sample_keys,sample_keys)
    annotation<-data.frame(SavedGroup=factor(map$group_key,levels=group_keys),row.names=sample_keys)
    render(function(){
    graph<-pheatmap::pheatmap(matrix,cluster_rows=FALSE,cluster_cols=FALSE,scale="none",
      color=grDevices::colorRampPalette(c("#2166AC","#F7F7F7","#B2182B"))(200L),breaks=seq(-1,1,length.out=201L),
      na_col="#BDBDBD",annotation_col=annotation,annotation_row=annotation,
      annotation_colors=list(SavedGroup=stats::setNames(grDevices::hcl.colors(length(group_keys),"Dark3"),group_keys)),
      labels_row=sample_keys,labels_col=sample_keys,main="Sample Pearson correlation; pairwise finite n >= 3",
      border_color=NA,fontsize=9,silent=TRUE)
    grid::grid.newpage();grid::grid.draw(graph$gtable)},"correlation.svg")
  }
  parameters<-list(contract="saved_log2_PCA_pairwise_Pearson_v1",source_version=parent$processed_dataset_version_id,
    manifest_sha256=parent$manifest_sha256,sample_design_version=design$sample_design_version_id,design_sha256=design$design_sha256,
    input_scale="saved_log2",normalization="none",imputation="none",
    PCA=list(implementation="stats::prcomp",observations="all samples",variables="all-sample finite complete-case nonzero-SD protein rows",
      center=TRUE,scale.=FALSE,minimum_samples=3L,minimum_eligible_proteins=2L,plot="PC1/PC2 only",sign="arbitrary; no fixed-sign acceptance",ellipses=FALSE),
    correlation=list(implementation="stats::cor",method="pearson",use="pairwise.complete.obs",minimum_paired_n=3L,
      constant_or_nonfinite="NA with explicit reason, including diagonal",heatmap="pheatmap",breaks=c(-1,1),NA_colour="#BDBDBD",
      cluster_rows=FALSE,cluster_cols=FALSE,sample_order="original saved source order"),
    labels="ASCII S01/G01 aliases with complete original names/design mapping; no name-based group inference",
    R_version=R.version.string,ggplot2_version=as.character(utils::packageVersion("ggplot2")),
    pheatmap_version=as.character(utils::packageVersion("pheatmap")),digest_version=as.character(utils::packageVersion("digest")),
    SVG_only=TRUE,published=FALSE,OwnerHTTP_verified=FALSE)
  summary<-data.frame(PCA_status=statistics$pca_status,correlation_status=statistics$correlation_status,
    source_rows=nrow(x),samples=s,PCA_eligible_rows=sum(statistics$included),PCA_included_rows=sum(statistics$row_audit$included_PCA),
    missing_excluded_rows=sum(!statistics$row_audit$complete_case),constant_excluded_rows=sum(statistics$row_audit$PCA_reason=="constant_across_samples"),
    observed_cells=sum(!missing),missing_cells=sum(missing),estimable_ordered_pairs=sum(statistics$pair_reasons=="estimable"),
    nonestimable_ordered_pairs=sum(statistics$pair_reasons!="estimable"),analysis_ready=FALSE,check.names=FALSE)
  write(summary,"summary.tsv");dput(parameters,file=file.path(output_dir,"parameters.R.txt"))
  saveRDS(list(parent=parent,design=design,parameters=parameters,statistics=statistics),file.path(output_dir,"full-provenance.rds"),version=3)
  writeLines(utils::capture.output(utils::sessionInfo()),file.path(output_dir,"R-session-info.txt"))
  verify();sizes<-file.info(file.path(output_dir,list.files(output_dir)))$size
  need(all(is.finite(sizes)&sizes>0&sizes<=33554432)&sum(sizes)<=134217728,"output_byte_limit")
  invisible(list(statistics=statistics,summary=summary,explained=explained))
}
