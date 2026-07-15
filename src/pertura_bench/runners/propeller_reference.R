args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 1) stop("expected reference config JSON path")

suppressPackageStartupMessages({
  library(jsonlite)
  library(limma)
  library(speckle)
})

cfg <- fromJSON(args[[1]], simplifyVector = TRUE)
metadata <- read.csv(cfg$metadata_path, check.names = FALSE, stringsAsFactors = FALSE)
required <- c(cfg$sample_column, cfg$state_column, cfg$condition_column)
if (!all(required %in% colnames(metadata))) stop("reference metadata columns are incomplete")

sample_id <- as.character(metadata[[cfg$sample_column]])
state <- as.character(metadata[[cfg$state_column]])
condition <- as.character(metadata[[cfg$condition_column]])
sample_design <- unique(data.frame(sample_id = sample_id, condition = condition))
if (any(duplicated(sample_design$sample_id))) stop("sample IDs map to multiple conditions")
sample_design <- sample_design[order(sample_design$sample_id), , drop = FALSE]

transformed <- getTransformedProps(clusters = state, sample = sample_id)
sample_order <- colnames(transformed$Proportions)
sample_design <- sample_design[match(sample_order, sample_design$sample_id), , drop = FALSE]
if (any(is.na(sample_design$sample_id))) stop("Propeller sample alignment failed")
contrast_levels <- as.character(cfg$contrast)
if (length(contrast_levels) != 2L || anyDuplicated(contrast_levels)) {
  stop("Propeller contrast must contain two distinct condition levels")
}
sample_design$condition <- factor(sample_design$condition, levels = contrast_levels)
if (any(is.na(sample_design$condition))) stop("unknown Propeller condition label")
design <- model.matrix(~0 + condition, data = sample_design)
rownames(design) <- sample_design$sample_id
colnames(design) <- sub("^condition", "", colnames(design))
if (qr(design)$rank < ncol(design)) stop("Propeller design is rank deficient")
group_columns <- match(contrast_levels, colnames(design))
if (any(is.na(group_columns))) stop("Propeller contrast columns are not estimable")
contrast <- numeric(ncol(design))
names(contrast) <- colnames(design)
contrast[group_columns[[1]]] <- -1
contrast[group_columns[[2]]] <- 1
result <- propeller.ttest(
  transformed,
  design = design,
  contrasts = contrast,
  robust = TRUE,
  trend = FALSE,
  sort = FALSE
)
if (!"cluster" %in% colnames(result)) result$cluster <- rownames(result)
result <- result[order(result$cluster), , drop = FALSE]
write.csv(result, cfg$result_path, row.names = FALSE, quote = FALSE)

proportions <- as.data.frame(t(transformed$Proportions), check.names = FALSE)
proportions$sample_id <- rownames(proportions)
proportions <- merge(
  proportions, sample_design, by = "sample_id", all.x = TRUE, sort = TRUE
)
write.csv(proportions, cfg$proportions_path, row.names = FALSE, quote = FALSE)
