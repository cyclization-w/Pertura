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
design <- model.matrix(~0 + condition, data = sample_design)
rownames(design) <- sample_design$sample_id
colnames(design) <- sub("^condition", "", colnames(design))
contrast <- makeContrasts(
  contrasts = paste0(make.names(cfg$contrast[[2]]), "-", make.names(cfg$contrast[[1]])),
  levels = design
)
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

proportions <- as.data.frame(t(transformed$proportions), check.names = FALSE)
proportions$sample_id <- rownames(proportions)
proportions <- merge(
  proportions, sample_design, by = "sample_id", all.x = TRUE, sort = TRUE
)
write.csv(proportions, cfg$proportions_path, row.names = FALSE, quote = FALSE)
