args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 1) stop("usage: propeller_composition.R <config.json>")

suppressPackageStartupMessages({
  library(jsonlite)
  library(limma)
  library(speckle)
})

cfg <- fromJSON(args[[1]], simplifyVector = TRUE)
dir.create(cfg$output_dir, recursive = TRUE, showWarnings = FALSE)
metadata_suffix <- tolower(tools::file_ext(cfg$metadata_path))
metadata_separator <- if (metadata_suffix %in% c("tsv", "txt")) "\t" else ","
metadata <- read.table(
  cfg$metadata_path,
  header = TRUE,
  sep = metadata_separator,
  quote = "\"",
  comment.char = "",
  check.names = FALSE,
  stringsAsFactors = FALSE
)
required <- c(cfg$sample_column, cfg$state_column, cfg$condition_column)
if (!all(required %in% colnames(metadata))) {
  stop("metadata is missing sample, state, or condition columns")
}

condition <- as.character(metadata[[cfg$condition_column]])
base_sample_id <- as.character(metadata[[cfg$sample_column]])
cluster <- as.character(metadata[[cfg$state_column]])
paired <- !is.null(cfg$pairing_column) && nzchar(cfg$pairing_column)
subject_id <- if (paired) as.character(metadata[[cfg$pairing_column]]) else base_sample_id
sample_id <- if (paired) paste(base_sample_id, condition, sep = "::") else base_sample_id
sample_table <- unique(data.frame(
  sample_id = sample_id,
  condition = condition,
  subject_id = subject_id
))
if (any(duplicated(sample_table$sample_id))) {
  stop("sample IDs map to multiple conditions")
}
sample_table <- sample_table[order(sample_table$sample_id), , drop = FALSE]
prop_list <- getTransformedProps(clusters = cluster, sample = sample_id)
sample_order <- colnames(prop_list$Proportions)
sample_table <- sample_table[match(sample_order, sample_table$sample_id), , drop = FALSE]
if (any(is.na(sample_table$sample_id))) stop("Propeller sample alignment failed")
contrast_levels <- as.character(cfg$contrast)
if (length(contrast_levels) != 2L || anyDuplicated(contrast_levels)) {
  stop("Propeller contrast must contain two distinct condition levels")
}
sample_table$condition <- factor(sample_table$condition, levels = contrast_levels)
if (any(is.na(sample_table$condition))) stop("unknown Propeller condition label")
if (paired) {
  sample_table$subject_id <- factor(sample_table$subject_id)
  design <- model.matrix(~ subject_id + condition, data = sample_table)
} else {
  design <- model.matrix(~0 + condition, data = sample_table)
  colnames(design) <- sub("^condition", "", colnames(design))
}
rownames(design) <- sample_table$sample_id
if (qr(design)$rank < ncol(design)) stop("Propeller design is rank deficient")
contrast <- numeric(ncol(design))
names(contrast) <- colnames(design)
if (paired) {
  target_column <- paste0("condition", contrast_levels[[2]])
  if (!target_column %in% colnames(design)) stop("paired Propeller contrast is not estimable")
  contrast[[target_column]] <- 1
} else {
  group_columns <- match(contrast_levels, colnames(design))
  if (any(is.na(group_columns))) stop("Propeller contrast columns are not estimable")
  contrast[group_columns[[1]]] <- -1
  contrast[group_columns[[2]]] <- 1
}
result <- propeller.ttest(
  prop_list,
  design = design,
  contrasts = contrast,
  robust = TRUE,
  trend = FALSE,
  sort = TRUE
)
if (!"cluster" %in% colnames(result)) {
  result$cluster <- rownames(result)
}
write.csv(result, file.path(cfg$output_dir, "propeller_results.csv"), row.names = FALSE)
proportions <- as.data.frame(t(prop_list$Proportions), check.names = FALSE)
proportions$sample_id <- rownames(proportions)
proportions <- merge(proportions, sample_table, by = "sample_id", all.x = TRUE, sort = FALSE)
write.csv(
  proportions,
  file.path(cfg$output_dir, "sample_state_proportions.csv"),
  row.names = FALSE
)
write_json(
  list(
    schema_version = "pertura-propeller-metadata-v1",
    speckle_version = as.character(packageVersion("speckle")),
    limma_version = as.character(packageVersion("limma")),
    contrast = cfg$contrast,
    robust = TRUE,
    trend = FALSE,
    paired = paired,
    n_samples = nrow(sample_table),
    n_states = nrow(result)
  ),
  file.path(cfg$output_dir, "propeller_metadata.json"),
  auto_unbox = TRUE,
  pretty = TRUE
)
