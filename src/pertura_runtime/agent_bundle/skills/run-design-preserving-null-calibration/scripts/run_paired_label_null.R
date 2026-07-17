suppressPackageStartupMessages({
  library(edgeR)
  library(jsonlite)
})

args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 1L) stop("usage: run_paired_label_null.R CONFIG.json")
cfg <- fromJSON(args[[1]], simplifyVector = TRUE)
required <- c(
  "counts_tsv", "samples_tsv", "output_path", "unit_column",
  "condition_column", "baseline", "target"
)
if (!all(required %in% names(cfg))) stop("configuration is incomplete")

table <- read.delim(cfg$counts_tsv, check.names = FALSE, stringsAsFactors = FALSE)
genes <- as.character(table[[1]])
if (anyDuplicated(genes)) stop("gene identities are duplicated")
counts <- as.matrix(table[, -1, drop = FALSE])
storage.mode(counts) <- "numeric"
if (any(!is.finite(counts)) || any(counts < 0) || any(abs(counts - round(counts)) > 1e-7)) {
  stop("counts must be finite nonnegative integers")
}
rownames(counts) <- genes

samples <- read.delim(cfg$samples_tsv, check.names = FALSE, stringsAsFactors = FALSE)
needed <- c("sample_id", cfg$unit_column, cfg$condition_column)
if (!all(needed %in% colnames(samples))) stop("sample manifest is incomplete")
if (anyDuplicated(samples$sample_id)) stop("sample identities are duplicated")
if (!setequal(samples$sample_id, colnames(counts))) stop("sample/count identity mismatch")
samples <- samples[match(colnames(counts), samples$sample_id), , drop = FALSE]
units <- sort(unique(as.character(samples[[cfg$unit_column]])))
if (length(units) < 3L) stop("null calibration requires at least three paired units")
pairing <- table(samples[[cfg$unit_column]], samples[[cfg$condition_column]])
expected <- c(cfg$baseline, cfg$target)
if (!setequal(colnames(pairing), expected) || any(pairing[, expected, drop = FALSE] != 1L)) {
  stop("each independent unit must have exactly one sample per condition")
}
robust <- if (is.null(cfg$robust)) TRUE else isTRUE(cfg$robust)

fit_one <- function(condition) {
  design_data <- samples
  design_data$unit__ <- factor(design_data[[cfg$unit_column]], levels = units)
  design_data$condition__ <- factor(condition, levels = expected)
  design <- model.matrix(~ unit__ + condition__, data = design_data)
  if (qr(design)$rank != ncol(design)) stop("paired null design is rank deficient")
  coefficient <- grep("^condition__", colnames(design))
  if (length(coefficient) != 1L) stop("condition coefficient is not unique")
  y <- DGEList(counts = counts)
  keep <- filterByExpr(y, design = design)
  if (!any(keep)) stop("filterByExpr retained no genes")
  y <- y[keep, , keep.lib.sizes = FALSE]
  y <- calcNormFactors(y)
  y <- estimateDisp(y, design, robust = robust)
  fit <- glmQLFit(y, design, robust = robust)
  test <- glmQLFTest(fit, coef = coefficient)
  result <- topTags(test, n = Inf, sort.by = "none")$table
  result$PValue[!is.finite(result$PValue)] <- 1
  result
}

masks <- seq_len(2^length(units) - 2L)
rows <- list()
details <- list()
for (mask in masks) {
  bits <- as.logical(intToBits(mask)[seq_along(units)])
  swapped <- units[bits]
  condition <- as.character(samples[[cfg$condition_column]])
  for (unit in swapped) {
    member <- samples[[cfg$unit_column]] == unit
    condition[member] <- ifelse(
      condition[member] == cfg$baseline, cfg$target, cfg$baseline
    )
  }
  result <- fit_one(condition)
  permutation_id <- sprintf("swap_%0*d", length(units), mask)
  rows[[length(rows) + 1L]] <- data.frame(
    permutation_id = permutation_id,
    type1_rate = mean(result$PValue <= 0.05),
    null_effect_bias = median(result$logFC),
    exchangeability_violation_count = 0L,
    stringsAsFactors = FALSE
  )
  details[[length(details) + 1L]] <- data.frame(
    permutation_id = permutation_id,
    swapped_units = paste(swapped, collapse = ","),
    n_tested_genes = nrow(result),
    cell_label_permutation = FALSE,
    stringsAsFactors = FALSE
  )
}
output <- do.call(rbind, rows)
dir.create(dirname(cfg$output_path), recursive = TRUE, showWarnings = FALSE)
write.table(output, cfg$output_path, sep = "\t", quote = FALSE, row.names = FALSE)
if (!is.null(cfg$details_path)) {
  dir.create(dirname(cfg$details_path), recursive = TRUE, showWarnings = FALSE)
  write.table(do.call(rbind, details), cfg$details_path, sep = "\t", quote = FALSE, row.names = FALSE)
}
