suppressPackageStartupMessages({
  library(edgeR)
  library(jsonlite)
  library(limma)
  library(speckle)
})

args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 1) stop("usage: generate_paper_ref05.R CONFIG.json")
cfg <- fromJSON(args[[1]], simplifyVector = TRUE)
dir.create(cfg$output_dir, recursive = TRUE, showWarnings = FALSE)

read_counts <- function(path) {
  table <- read.delim(path, check.names = FALSE, stringsAsFactors = FALSE)
  genes <- as.character(table[[1]])
  if (anyDuplicated(genes)) stop("pseudobulk gene identities are duplicated")
  matrix <- as.matrix(table[, -1, drop = FALSE])
  storage.mode(matrix) <- "integer"
  rownames(matrix) <- genes
  if (any(!is.finite(matrix)) || any(matrix < 0)) stop("invalid pseudobulk counts")
  matrix
}

read_samples <- function(path, counts) {
  samples <- read.delim(path, check.names = FALSE, stringsAsFactors = FALSE)
  required <- c("sample_id", "donor", "condition", "n_cells")
  if (!all(required %in% colnames(samples))) stop("sample metadata is incomplete")
  if (!setequal(samples$sample_id, colnames(counts))) stop("sample/count mismatch")
  samples <- samples[match(colnames(counts), samples$sample_id), , drop = FALSE]
  if (anyDuplicated(samples$sample_id)) stop("sample identities are duplicated")
  samples
}

paired_design <- function(samples, condition_column = "condition") {
  samples$donor <- factor(samples$donor)
  samples[[condition_column]] <- factor(
    samples[[condition_column]], levels = c(cfg$baseline, cfg$target)
  )
  if (any(is.na(samples[[condition_column]]))) stop("unknown condition label")
  tab <- table(samples$donor, samples[[condition_column]])
  if (any(tab != 1L)) stop("each donor must have exactly one sample per condition")
  design <- model.matrix(
    reformulate(c("donor", condition_column)),
    data = samples
  )
  if (qr(design)$rank < ncol(design)) stop("paired design is rank deficient")
  coefficient <- grep(paste0("^", condition_column), colnames(design))
  if (length(coefficient) != 1) stop("condition coefficient is not unique")
  list(samples = samples, design = design, coefficient = coefficient)
}

paired_propeller_design <- function(samples, condition_column = "condition") {
  samples$donor <- factor(samples$donor)
  samples[[condition_column]] <- factor(
    samples[[condition_column]], levels = c(cfg$baseline, cfg$target)
  )
  if (any(is.na(samples[[condition_column]]))) stop("unknown condition label")
  tab <- table(samples$donor, samples[[condition_column]])
  if (any(tab != 1L)) stop("each donor must have exactly one sample per condition")

  # propeller.ttest requires a no-intercept design with the two group-specific
  # columns first. Additional donor columns are accepted as confounders.
  design <- model.matrix(
    reformulate(c(condition_column, "donor"), intercept = FALSE),
    data = samples
  )
  if (qr(design)$rank < ncol(design)) stop("paired Propeller design is rank deficient")
  group_columns <- match(
    paste0(condition_column, c(cfg$baseline, cfg$target)),
    colnames(design)
  )
  if (any(is.na(group_columns))) stop("Propeller group columns are not uniquely estimable")

  # Speckle expects a vector whose length equals ncol(design), not a one-column
  # limma contrast matrix. The remaining zeroes retain donor adjustment.
  contrast <- numeric(ncol(design))
  names(contrast) <- colnames(design)
  contrast[group_columns[[1]]] <- -1
  contrast[group_columns[[2]]] <- 1
  list(samples = samples, design = design, contrast = contrast)
}

run_edger <- function(counts, samples, condition_column = "condition") {
  designed <- paired_design(samples, condition_column)
  y <- DGEList(counts = counts, samples = designed$samples)
  keep <- filterByExpr(y, design = designed$design)
  if (!any(keep)) stop("filterByExpr retained no genes")
  y <- y[keep, , keep.lib.sizes = FALSE]
  y <- calcNormFactors(y)
  y <- estimateDisp(y, designed$design)
  fit <- glmQLFit(y, designed$design)
  test <- glmQLFTest(fit, coef = designed$coefficient)
  result <- topTags(test, n = Inf, sort.by = "none")$table
  result$gene <- rownames(result)
  result <- result[, c("gene", "logFC", "F", "PValue", "FDR")]
  list(result = result, design = designed$design, samples = designed$samples)
}

write_tsv <- function(value, path) {
  write.table(value, path, sep = "\t", row.names = FALSE, quote = FALSE, na = "")
}

evaluation_counts <- read_counts(cfg$evaluation_counts)
evaluation_samples <- read_samples(cfg$evaluation_samples, evaluation_counts)
cat("REF-05-A: fitting donor-aware evaluation edgeR\n")
flush.console()
evaluation_fit <- run_edger(evaluation_counts, evaluation_samples)
write_tsv(evaluation_fit$result, file.path(cfg$output_dir, "edger_reference.tsv"))
edge_design <- data.frame(
  sample_id = evaluation_fit$samples$sample_id,
  evaluation_fit$design,
  check.names = FALSE
)
write_tsv(edge_design, file.path(cfg$output_dir, "edger_design_matrix.tsv"))
writeLines(capture.output(sessionInfo()), file.path(cfg$output_dir, "edger_session_info.txt"))

cells <- read.delim(cfg$evaluation_cells, check.names = FALSE, stringsAsFactors = FALSE)
required_cells <- c("sample_id", "donor", "condition", "state")
if (!all(required_cells %in% colnames(cells))) stop("cell metadata is incomplete")
sample_table <- unique(cells[, c("sample_id", "donor", "condition")])
if (anyDuplicated(sample_table$sample_id)) stop("sample IDs map to multiple designs")
cat("REF-05-B: fitting donor-aware evaluation Propeller\n")
flush.console()
prop_list <- getTransformedProps(
  clusters = as.character(cells$state),
  sample = as.character(cells$sample_id)
)
sample_order <- colnames(prop_list$Proportions)
sample_table <- sample_table[match(sample_order, sample_table$sample_id), , drop = FALSE]
if (any(is.na(sample_table$sample_id))) stop("Propeller sample alignment failed")
designed <- paired_propeller_design(sample_table)
rownames(designed$design) <- sample_table$sample_id
propeller <- propeller.ttest(
  prop_list,
  design = designed$design,
  contrasts = designed$contrast,
  robust = TRUE,
  trend = FALSE,
  sort = FALSE
)
if (!"cluster" %in% colnames(propeller)) propeller$cluster <- rownames(propeller)
propeller <- propeller[match(rownames(prop_list$Proportions), propeller$cluster), , drop = FALSE]
baseline_samples <- sample_table$condition == cfg$baseline
target_samples <- sample_table$condition == cfg$target
baseline_mean <- rowMeans(prop_list$Proportions[, baseline_samples, drop = FALSE])
target_mean <- rowMeans(prop_list$Proportions[, target_samples, drop = FALSE])
p_column <- intersect(c("P.Value", "PValue", "p.value", "pvalue"), colnames(propeller))
if (length(p_column) == 0) stop("Propeller result lacks a p-value column")
if (!"FDR" %in% colnames(propeller)) stop("Propeller result lacks FDR")
propeller_reference <- data.frame(
  cluster = as.character(propeller$cluster),
  baseline_proportion = as.numeric(baseline_mean[propeller$cluster]),
  target_proportion = as.numeric(target_mean[propeller$cluster]),
  effect = as.numeric(target_mean[propeller$cluster] - baseline_mean[propeller$cluster]),
  PValue = as.numeric(propeller[[p_column[[1]]]]),
  FDR = as.numeric(propeller$FDR),
  stringsAsFactors = FALSE
)
write_tsv(propeller_reference, file.path(cfg$output_dir, "propeller_reference.tsv"))
propeller_design <- data.frame(
  sample_id = sample_table$sample_id,
  designed$design,
  check.names = FALSE
)
write_tsv(propeller_design, file.path(cfg$output_dir, "propeller_design_matrix.tsv"))
writeLines(capture.output(sessionInfo()), file.path(cfg$output_dir, "propeller_session_info.txt"))

calibration_counts <- read_counts(cfg$calibration_counts)
calibration_samples <- read_samples(cfg$calibration_samples, calibration_counts)
donors <- sort(unique(as.character(calibration_samples$donor)))
if (length(donors) < 3L) stop("null calibration requires at least three donors")
masks <- 1:(2^length(donors) - 2L)
method_null <- list()
replicate_null <- list()
cat("REF-05-C: fitting ", length(masks), " mixed donor-pair null permutations\n", sep = "")
flush.console()
for (mask in masks) {
  permuted <- calibration_samples
  swapped <- donors[as.logical(intToBits(mask)[seq_along(donors)])]
  permuted$condition_perm <- as.character(permuted$condition)
  for (donor in swapped) {
    member <- permuted$donor == donor
    permuted$condition_perm[member] <- ifelse(
      permuted$condition_perm[member] == cfg$baseline,
      cfg$target,
      cfg$baseline
    )
  }
  fitted <- run_edger(calibration_counts, permuted, "condition_perm")
  table <- fitted$result
  table$permutation_id <- sprintf("swap_%0*d", length(donors), mask)
  table$swapped_donors <- paste(swapped, collapse = ",")
  method_null[[length(method_null) + 1L]] <- table[, c(
    "permutation_id", "swapped_donors", "gene", "logFC", "F", "PValue", "FDR"
  )]
  replicate_null[[length(replicate_null) + 1L]] <- data.frame(
    permutation_id = sprintf("swap_%0*d", length(donors), mask),
    swapped_donors = paste(swapped, collapse = ","),
    n_tested_genes = nrow(table),
    type1_rate = mean(table$PValue <= 0.05),
    null_effect_bias = median(table$logFC),
    median_absolute_effect = median(abs(table$logFC)),
    exchangeability_violation_count = 0L,
    cell_label_permutation = FALSE,
    stringsAsFactors = FALSE
  )
  cat("REF-05-C: completed donor-swap permutation ", mask, "/", max(masks), "\n", sep = "")
  flush.console()
}
method_null <- do.call(rbind, method_null)
replicate_null <- do.call(rbind, replicate_null)
write_tsv(method_null, file.path(cfg$output_dir, "method_null_reference.tsv"))
write_tsv(replicate_null, file.path(cfg$output_dir, "replicate_null_reference.tsv"))

write_json(
  list(
    R = paste(R.version$major, R.version$minor, sep = "."),
    edgeR = as.character(packageVersion("edgeR")),
    limma = as.character(packageVersion("limma")),
    speckle = as.character(packageVersion("speckle")),
    jsonlite = as.character(packageVersion("jsonlite"))
  ),
  file.path(cfg$output_dir, "r_environment.json"),
  auto_unbox = TRUE,
  pretty = TRUE
)

cat("REF-05-A: edgeR genes=", nrow(evaluation_fit$result), "\n", sep = "")
cat("REF-05-B: Propeller states=", nrow(propeller_reference), "\n", sep = "")
cat("REF-05-C: donor-swap permutations=", length(masks), "\n", sep = "")
