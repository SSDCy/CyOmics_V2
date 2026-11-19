# One new target: literal hand oracle, then one exact real saved-data evaluation.
args <- commandArgs(trailingOnly=TRUE)
stopifnot(length(args)==5L,as.character(getRversion())=="4.6.1")
source(args[[1L]],local=TRUE)
stopifnot(dir.create(args[[5L]],recursive=FALSE))
fixture <- file.path(args[[5L]],"hand-fixture");stopifnot(dir.create(fixture))
input <- file.path(fixture,"input");stopifnot(dir.create(input))
names_raw <- c("LFQ intensity WT-1","LFQ intensity 100-1","LFQ intensity WT-2","LFQ intensity 100-2","LFQ intensity WT-3")
raw <- matrix(c("10","20","+0.0e0","","5","0","30","4","4","0","","0","","0","","1e308","1e308","1e308","1e-300",""),nrow=4L,byrow=TRUE)
colnames(raw) <- names_raw
prefix <- data.frame(record_number=c(2L,5L,8L,9L),protein_ids_raw=c("dup;P2","dup;P2","all-missing","large"),stringsAsFactors=FALSE)
blank <- raw=="";number <- matrix(NA_real_,4L,5L);number[!blank] <- as.numeric(raw[!blank])
zero <- !blank & number==0;zero[is.na(zero)] <- FALSE;missing <- blank|zero
working <- matrix("NA",4L,5L,dimnames=dimnames(raw));working[!missing] <- sprintf("%.17g",log2(number[!missing]))
write <- function(value,name) utils::write.table(value,file.path(input,name),sep="\t",quote=TRUE,row.names=FALSE,na="NA",qmethod="double",eol="\n")
table <- function(x) {colnames(x)<-names_raw;cbind(prefix,as.data.frame(x,check.names=FALSE))}
write(table(raw),"raw-retained.tsv");write(table(working),"working-matrix.tsv")
write(table(blank),"original-blank-mask.tsv");write(table(zero),"original-zero-mask.tsv")
write(table(missing),"working-NA-mask.tsv");write(table(matrix(FALSE,4L,5L)),"imputed-mask.tsv")
for(name in c("R-session-info.txt","filter-log.tsv","row-map.tsv","summary.tsv"))
  writeLines("synthetic audit fixture only; not a published preprocessing version",file.path(input,name))
params_json <- '{"blank_policy":"to_NA","filter_flags":{"contaminant":false,"reverse":false,"site_only":false},"imputation":"none","input_scale":"raw_nonnegative","log2":"positive_only","normalization":"none","output_scale":"log2","preset":"positive_log2_zero_na_no_impute_v1","pseudocount":0,"seed":null,"steps":["flags_filter","zero_and_blank_to_NA","log2_positive","normalization_none","imputation_none"],"zero_policy":"to_NA"}'
cat(params_json,file=file.path(input,"parameters.json"))
artifact_names <- c("R-session-info.txt","filter-log.tsv","imputed-mask.tsv","original-blank-mask.tsv",
  "original-zero-mask.tsv","parameters.json","raw-retained.tsv","row-map.tsv","summary.tsv","working-NA-mask.tsv","working-matrix.tsv")
pins <- data.frame(name=artifact_names,bytes=as.numeric(file.info(file.path(input,artifact_names))$size),
  sha256=vapply(file.path(input,artifact_names),function(path)digest::digest(file=path,algo="sha256",serialize=FALSE),character(1)),stringsAsFactors=FALSE)
pins$media_type <- ifelse(grepl("json$",pins$name),"application/json",ifelse(grepl("txt$",pins$name),"text/plain; charset=utf-8","text/tab-separated-values; charset=utf-8"))
manifest <- paste0('[',paste(vapply(seq_len(nrow(pins)),function(i)sprintf('{"bytes":%d,"media_type":"%s","name":"%s","sha256":"%s"}',pins$bytes[[i]],pins$media_type[[i]],pins$name[[i]],pins$sha256[[i]]),character(1)),collapse=','),']')
parent <- list(project_id="00000000-0000-4000-8000-000000000001",parent_dataset_version_id="00000000-0000-4000-8000-000000000002",
  processed_dataset_version_id="00000000-0000-4000-8000-000000000003",quant_family="LFQ intensity",
  manifest_sha256=digest::digest(charToRaw(manifest),algo="sha256",serialize=FALSE),artifacts=pins,
  parameters=list(output_scale="log2",normalization="none",log2="positive_only",imputation="none",zero_policy="to_NA",blank_policy="to_NA",pseudocount=0))
design <- list(project_id=parent$project_id,dataset_version_id=parent$parent_dataset_version_id,
  quant_family="LFQ intensity",sample_design_version_id="00000000-0000-4000-8000-000000000004",design_sha256=paste(rep("a",64L),collapse=""),
  rows=data.frame(column_ordinal=1:5,name_raw=names_raw,sample_label_raw=c("WT-1","100-1","WT-2","100-2","WT-3"),
    group=c("Metadata Z","Metadata Z","Metadata A","Metadata A","Metadata Single"),condition="fixture",
    control="unknown",replicate=c("1","2","1","2","1"),replicate_type=c("biological","biological","technical","technical","unknown"),
    confirmed=TRUE,stringsAsFactors=FALSE))
small <- quant_group_stats_cv(input,parent,design,file.path(fixture,"output"))
d <- small$table
pick <- function(record,group)d[d$record_number==record & d$group_label_raw==group,,drop=FALSE]
a <- pick(2L,"Metadata Z")
stopifnot(nrow(d)==12L,identical(unique(d$record_number),c(2L,5L,8L,9L)),
  sum(d$protein_ids_raw=="dup;P2")==6L,a$n_total==2L,a$n_valid==2L,a$n_missing==0L,
  a$raw_mean==15,a$raw_median==15,abs(a$raw_sd-sqrt(50))<1e-12,
  abs(a$cv_raw-sqrt(2)/3)<1e-12,abs(a$rsd_percent_raw-100*sqrt(2)/3)<1e-12,
  abs(a$log2_sd-sqrt(0.5))<1e-12,abs(a$cv_raw-a$log2_sd/a$log2_mean)>0.1)
b <- pick(2L,"Metadata A");one <- pick(2L,"Metadata Single")
stopifnot(b$n_valid==0L,b$n_missing==2L,b$original_zero_n==1L,b$original_blank_n==1L,
  is.na(b$raw_mean),is.na(b$raw_median),is.na(b$raw_sd),is.na(b$cv_raw),b$cv_status=="all_missing",
  one$n_valid==1L,one$raw_mean==5,one$raw_median==5,is.na(one$raw_sd),is.na(one$cv_raw),
  one$cv_status=="insufficient_valid_n",!one$all_declared_biological)
z <- pick(5L,"Metadata Z");constant <- pick(5L,"Metadata A")
stopifnot(z$n_valid==1L,z$original_zero_n==1L,is.na(z$cv_raw),constant$raw_mean==4,
  constant$raw_sd==0,constant$cv_raw==0,constant$rsd_percent_raw==0,
  !constant$all_declared_biological,all(is.na(d$cv_raw[d$record_number==8L])),
  all(d$n_valid[d$record_number==8L]==0L),!any(d$biological_consistency_passed))
overflow <- pick(9L,"Metadata A")
stopifnot(overflow$n_valid==2L,is.na(overflow$raw_sd),is.na(overflow$cv_raw),overflow$cv_status=="nonfinite_sd")
cat("HAND_EXPECTATIONS_PASS raw10_20:mean15,SDsqrt50,CVsqrt2/3; n1/allNA/zero/blank/duplicates/metadata/overflow\n")

real_parent <- dget(args[[3L]]);real_design <- dget(args[[4L]])
stopifnot(real_parent$processed_dataset_version_id=="2fc0a8e6-ef3d-4afe-a15c-f27043b00227",
  real_design$sample_design_version_id=="4cf9e303-46a1-4c04-969c-6400edc5490c")
before <- serialize(list(real_parent,real_design),NULL)
actual <- quant_group_stats_cv(args[[2L]],real_parent,real_design,file.path(args[[5L]],"actual"))
stopifnot(identical(before,serialize(list(real_parent,real_design),NULL)),actual$rows==2252L,
  actual$samples==21L,actual$groups==7L,actual$result_rows==2252L*7L,
  sum(actual$table$n_valid)==actual$observed_cells,
  sum(actual$table$n_missing)==actual$missing_cells,
  all(actual$table$n_total==actual$table$n_valid+actual$table$n_missing),
  all(actual$table$original_zero_n+actual$table$original_blank_n==actual$table$n_missing),
  all(actual$table$effective_n==actual$table$n_valid),all(actual$table$imputed_n==0L),
  !any(actual$table$biological_consistency_passed))
cat("QUANT_GROUP_STATS_CV_ACTUAL_TARGET_PASS\n")
cat("rows=",actual$rows," samples=",actual$samples," groups=",actual$groups,
  " result_rows=",actual$result_rows," observed=",actual$observed_cells," missing=",actual$missing_cells,"\n",sep="")
