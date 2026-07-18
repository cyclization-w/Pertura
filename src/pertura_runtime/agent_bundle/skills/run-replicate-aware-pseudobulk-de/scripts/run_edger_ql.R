suppressPackageStartupMessages({
  library(Matrix)
  library(edgeR)
  library(jsonlite)
})

args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 1L) stop("usage: run_edger_ql.R CONFIG.json")
cfg <- fromJSON(args[[1]], simplifyVector = TRUE)
required <- c(
  "mode", "samples_tsv", "output_dir", "unit_column",
  "condition_column", "baseline", "robust"
)
if (!all(required %in% names(cfg))) stop("configuration is incomplete")
if (!is.logical(cfg$robust) || length(cfg$robust) != 1L || is.na(cfg$robust)) {
  stop("robust must be an explicit boolean from the frozen protocol")
}
dir.create(cfg$output_dir, recursive = TRUE, showWarnings = FALSE)

read_counts <- function() {
  if (!is.null(cfg$counts_tsv)) {
    table <- read.delim(cfg$counts_tsv, check.names = FALSE, stringsAsFactors = FALSE)
    genes <- as.character(table[[1]])
    counts <- as.matrix(table[, -1, drop = FALSE])
    storage.mode(counts) <- "numeric"
  } else if (!is.null(cfg$counts_mtx) && !is.null(cfg$genes_tsv)) {
    counts <- readMM(cfg$counts_mtx)
    gene_table <- read.delim(cfg$genes_tsv, check.names = FALSE, stringsAsFactors = FALSE)
    genes <- as.character(gene_table[[1]])
  } else {
    stop("provide counts_tsv or counts_mtx plus genes_tsv")
  }
  if (nrow(counts) != length(genes)) stop("gene/count dimension mismatch")
  if (anyDuplicated(genes)) stop("gene identities are duplicated")
  values <- if (inherits(counts, "sparseMatrix")) counts@x else counts
  if (any(!is.finite(values)) || any(values < 0) || any(abs(values - round(values)) > 1e-7)) {
    stop("counts must be finite nonnegative integers")
  }
  rownames(counts) <- genes
  list(counts = counts, genes = genes)
}

loaded <- read_counts()
counts <- loaded$counts
genes <- loaded$genes
samples <- read.delim(cfg$samples_tsv, check.names = FALSE, stringsAsFactors = FALSE)
needed <- c("sample_id", cfg$unit_column, cfg$condition_column)
if (!all(needed %in% colnames(samples))) stop("sample manifest is incomplete")
if (anyDuplicated(samples$sample_id)) stop("sample identities are duplicated")
if (ncol(counts) != nrow(samples)) stop("sample/count dimension mismatch")
if (is.null(colnames(counts))) colnames(counts) <- samples$sample_id
if (!setequal(colnames(counts), samples$sample_id)) stop("sample/count identity mismatch")
samples <- samples[match(colnames(counts), samples$sample_id), , drop = FALSE]
robust <- isTRUE(cfg$robust)

fit_one <- function(selected_counts, selected_samples, target_label) {
  selected_samples$unit__ <- factor(selected_samples[[cfg$unit_column]])
  selected_samples$condition__ <- factor(
    selected_samples[[cfg$condition_column]],
    levels = c(cfg$baseline, target_label)
  )
  if (any(is.na(selected_samples$condition__))) stop("unknown condition label")
  pairing <- table(selected_samples$unit__, selected_samples$condition__)
  if (nrow(pairing) < 2L || any(pairing != 1L)) {
    stop("each independent unit must have exactly one sample per condition")
  }
  design <- model.matrix(~ unit__ + condition__, data = selected_samples)
  if (qr(design)$rank != ncol(design)) stop("paired design is rank deficient")
  coefficient <- grep("^condition__", colnames(design))
  if (length(coefficient) != 1L) stop("condition coefficient is not unique")
  y <- DGEList(counts = selected_counts)
  keep <- filterByExpr(y, design = design)
  if (!any(keep)) stop("filterByExpr retained no genes")
  y <- y[keep, , keep.lib.sizes = FALSE]
  y <- calcNormFactors(y)
  y <- estimateDisp(y, design, robust = robust)
  fit <- glmQLFit(y, design, robust = robust)
  test <- glmQLFTest(fit, coef = coefficient)
  result <- topTags(test, n = Inf, sort.by = "none")$table
  result$gene <- rownames(result)
  result$PValue[!is.finite(result$PValue)] <- 1
  result$FDR <- p.adjust(result$PValue, method = "BH")
  list(result = result, design = design, samples = selected_samples, keep = keep)
}

write_tsv <- function(value, path) {
  write.table(value, path, sep = "\t", quote = FALSE, row.names = FALSE, na = "")
}

if (cfg$mode == "single") {
  if (is.null(cfg$target)) stop("single mode requires target")
  fit <- fit_one(counts, samples, cfg$target)
  result <- fit$result[, c("gene", "logFC", "F", "PValue", "FDR")]
  design <- data.frame(sample_id = fit$samples$sample_id, fit$design, check.names = FALSE)
  write_tsv(result, file.path(cfg$output_dir, "de_results.tsv"))
  write_tsv(design, file.path(cfg$output_dir, "design_matrix.tsv"))
  targets <- list(cfg$target)
} else if (cfg$mode == "per_target") {
  if (is.null(cfg$target_column) || is.null(cfg$control_label)) {
    stop("per_target mode requires target_column and control_label")
  }
  if (!cfg$target_column %in% colnames(samples)) stop("target column is absent")
  if (!is.null(cfg$eligibility_tsv)) {
    eligibility <- read.delim(cfg$eligibility_tsv, check.names = FALSE, stringsAsFactors = FALSE)
    if (!all(c(cfg$target_column, "eligible") %in% colnames(eligibility))) {
      stop("eligibility table is incomplete")
    }
    targets <- as.character(eligibility[[cfg$target_column]][tolower(as.character(eligibility$eligible)) == "true"])
  } else {
    targets <- setdiff(unique(as.character(samples[[cfg$target_column]])), cfg$control_label)
  }
  targets <- sort(unique(targets))
  if (!length(targets)) stop("no eligible targets")
  all_results <- list()
  all_designs <- list()
  for (target in targets) {
    selected <- samples[[cfg$target_column]] %in% c(cfg$control_label, target)
    current_samples <- samples[selected, , drop = FALSE]
    current_samples[[cfg$condition_column]] <- ifelse(
      current_samples[[cfg$target_column]] == cfg$control_label,
      cfg$baseline,
      "target"
    )
    fit <- fit_one(counts[, selected, drop = FALSE], current_samples, "target")
    table <- fit$result
    if (isTRUE(cfg$full_gene_output)) {
      full <- data.frame(
        gene = genes, logFC = 0, logCPM = NA_real_, F = 0,
        PValue = 1, FDR = 1, tested = FALSE,
        stringsAsFactors = FALSE
      )
      positions <- match(table$gene, full$gene)
      for (field in intersect(c("logFC", "logCPM", "F", "PValue", "FDR"), colnames(table))) {
        full[[field]][positions] <- table[[field]]
      }
      full$tested[positions] <- TRUE
      table <- full
    }
    table$target_uid <- target
    all_results[[target]] <- table[, c("target_uid", "gene", "logFC", "PValue", "FDR", intersect("tested", colnames(table)))]
    design <- as.data.frame(fit$design, check.names = FALSE)
    design_columns <- colnames(design)
    design_columns <- sub("^unit__", cfg$unit_column, design_columns)
    design_columns <- sub("^condition__", "condition", design_columns)
    colnames(design) <- design_columns
    design$target_uid <- target
    design$sample_id <- fit$samples$sample_id
    design$replicate_label <- as.character(fit$samples[[cfg$unit_column]])
    design$condition_label <- as.character(fit$samples[[cfg$condition_column]])
    all_designs[[target]] <- design[, c("target_uid", "sample_id", "replicate_label", "condition_label", design_columns)]
  }
  result <- do.call(rbind, all_results)
  design <- do.call(rbind, all_designs)
  rownames(result) <- NULL
  rownames(design) <- NULL
  write_tsv(result, file.path(cfg$output_dir, "trans_de_results.tsv"))
  write_tsv(design, file.path(cfg$output_dir, "trans_de_design_matrices.tsv"))
} else {
  stop("mode must be single or per_target")
}

manifest <- list(
  schema_version = "pertura-edger-ql-design-v1",
  formula = paste("~", cfg$unit_column, "+", cfg$condition_column),
  baseline = cfg$baseline,
  robust = robust,
  cell_is_replicate = FALSE,
  guide_is_replicate = FALSE,
  minimum_paired_replicates = 2L,
  targets = as.list(targets),
  versions = list(
    R = paste(R.version$major, R.version$minor, sep = "."),
    edgeR = as.character(packageVersion("edgeR")),
    Matrix = as.character(packageVersion("Matrix")),
    jsonlite = as.character(packageVersion("jsonlite"))
  )
)
summary <- list(
  schema_version = "pertura-edger-ql-summary-v1",
  eligible_targets = as.list(targets),
  target_count = length(targets),
  top_up = list(),
  top_down = list(),
  cautions = list("Cells and guides were not treated as biological replicates.")
)
if (cfg$mode == "single") {
  write_json(manifest, file.path(cfg$output_dir, "design_manifest.json"), pretty = TRUE, auto_unbox = TRUE)
  write_json(summary, file.path(cfg$output_dir, "de_summary.json"), pretty = TRUE, auto_unbox = TRUE)
} else {
  write_json(manifest, file.path(cfg$output_dir, "trans_de_design_manifest.json"), pretty = TRUE, auto_unbox = TRUE)
  write_json(summary, file.path(cfg$output_dir, "trans_de_summary.json"), pretty = TRUE, auto_unbox = TRUE)
}
