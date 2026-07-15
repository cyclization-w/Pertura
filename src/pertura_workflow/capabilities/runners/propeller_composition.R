args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 1) stop("usage: propeller_composition.R <config.json>")

suppressPackageStartupMessages({
  library(jsonlite)
  library(limma)
  library(speckle)
})

cfg <- fromJSON(args[[1]], simplifyVector = TRUE)
dir.create(cfg$output_dir, recursive = TRUE, showWarnings = FALSE)
metadata <- read.csv(cfg$metadata_path, check.names = FALSE, stringsAsFactors = FALSE)
required <- c(cfg$sample_column, cfg$state_column, cfg$condition_column)
if (!all(required %in% colnames(metadata))) {
  stop("metadata is missing sample, state, or condition columns")
}

sample_id <- as.character(metadata[[cfg$sample_column]])
cluster <- as.character(metadata[[cfg$state_column]])
condition <- as.character(metadata[[cfg$condition_column]])
sample_table <- unique(data.frame(sample_id = sample_id, condition = condition))
if (any(duplicated(sample_table$sample_id))) {
  stop("sample IDs map to multiple conditions")
}
sample_table <- sample_table[order(sample_table$sample_id), , drop = FALSE]
prop_list <- getTransformedProps(clusters = cluster, sample = sample_id)
design <- model.matrix(~0 + condition, data = sample_table)
rownames(design) <- sample_table$sample_id
colnames(design) <- sub("^condition", "", colnames(design))
contrast_text <- paste0(make.names(cfg$contrast[[2]]), "-", make.names(cfg$contrast[[1]]))
contrast <- makeContrasts(contrasts = contrast_text, levels = design)
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
    n_samples = nrow(sample_table),
    n_states = nrow(result)
  ),
  file.path(cfg$output_dir, "propeller_metadata.json"),
  auto_unbox = TRUE,
  pretty = TRUE
)
