suppressPackageStartupMessages({
  library(edgeR)
  library(jsonlite)
})

args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 1) stop("expected reference config JSON path")
config <- fromJSON(args[[1]], simplifyVector = TRUE)

raw <- read.csv(config$counts_path, row.names = 1, check.names = FALSE)
metadata <- read.csv(config$metadata_path, check.names = FALSE, stringsAsFactors = FALSE)
raw <- as.matrix(raw)
storage.mode(raw) <- "integer"
if (any(raw < 0L)) stop("counts must be nonnegative")
if (!all(colnames(raw) %in% metadata[[config$cell_column]])) stop("reference metadata is incomplete")
metadata <- metadata[match(colnames(raw), metadata[[config$cell_column]]), , drop = FALSE]
selected <- metadata[[config$condition_column]] %in% c(config$baseline, config$target)
metadata <- metadata[selected, , drop = FALSE]
raw <- raw[, selected, drop = FALSE]
state_column <- unlist(config$state_column, use.names = FALSE)
covariates <- unlist(config$covariates, use.names = FALSE)
metadata$.state <- if (length(state_column) == 0 || !nzchar(state_column)) "all" else metadata[[state_column]]

group_columns <- c(config$replicate_column, config$condition_column, ".state", covariates)
group_key <- do.call(paste, c(metadata[group_columns], sep = "\x1f"))
keys <- sort(unique(group_key))
sample_rows <- list()
aggregates <- list()
for (key in keys) {
  members <- which(group_key == key)
  if (length(members) < config$minimum_cells) next
  first <- metadata[members[[1]], , drop = FALSE]
  row <- data.frame(
    sample_id = sprintf("pb_%04d", length(sample_rows) + 1),
    replicate = first[[config$replicate_column]],
    condition = first[[config$condition_column]],
    state = first$.state,
    n_cells = length(members),
    stringsAsFactors = FALSE
  )
  for (covariate in covariates) row[[covariate]] <- first[[covariate]]
  sample_rows[[length(sample_rows) + 1]] <- row
  aggregates[[length(aggregates) + 1]] <- rowSums(raw[, members, drop = FALSE])
}
samples <- do.call(rbind, sample_rows)
counts <- do.call(cbind, aggregates)
colnames(counts) <- samples$sample_id
rownames(counts) <- rownames(raw)

samples$condition <- factor(samples$condition, levels = c(config$baseline, config$target))
terms <- c()
if (isTRUE(config$paired)) terms <- c(terms, "replicate")
if (length(covariates) > 0) terms <- c(terms, covariates)
terms <- c(terms, "condition")
design <- model.matrix(reformulate(terms), data = samples)
if (qr(design)$rank < ncol(design)) stop("design matrix is not full rank")
coefficient <- grep("^condition", colnames(design))
if (length(coefficient) != 1) stop("condition contrast is not uniquely estimable")

y <- DGEList(counts = counts, samples = samples)
keep <- filterByExpr(y, design = design)
if (!any(keep)) stop("filterByExpr retained no genes")
y <- y[keep, , keep.lib.sizes = FALSE]
y <- calcNormFactors(y)
y <- estimateDisp(y, design)
fit <- glmQLFit(y, design)
test <- glmQLFTest(fit, coef = coefficient)
table <- topTags(test, n = Inf, sort.by = "none")$table
table$gene <- rownames(table)
table <- table[, c("gene", "logFC", "F", "PValue", "FDR")]
write.csv(table, config$result_path, row.names = FALSE, quote = FALSE)
write.csv(data.frame(sample_id = rownames(design), design, check.names = FALSE), config$design_path, row.names = FALSE, quote = FALSE)
write.csv(samples, config$samples_result_path, row.names = FALSE, quote = FALSE)
