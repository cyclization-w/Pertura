suppressPackageStartupMessages({
  library(Matrix)
  library(edgeR)
  library(jsonlite)
})

args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 5L) {
  stop("usage: generate_paper_task_trans_de.R counts.mtx genes.tsv samples.tsv eligibility.tsv output")
}

counts_path <- normalizePath(args[[1]], mustWork = TRUE)
genes_path <- normalizePath(args[[2]], mustWork = TRUE)
samples_path <- normalizePath(args[[3]], mustWork = TRUE)
eligibility_path <- normalizePath(args[[4]], mustWork = TRUE)
output <- normalizePath(args[[5]], mustWork = FALSE)
dir.create(output, recursive = TRUE, showWarnings = FALSE)

counts <- readMM(counts_path)
genes <- read.delim(genes_path, check.names = FALSE, stringsAsFactors = FALSE)
samples <- read.delim(samples_path, check.names = FALSE, stringsAsFactors = FALSE)
eligibility <- read.delim(eligibility_path, check.names = FALSE, stringsAsFactors = FALSE)

if (nrow(counts) != nrow(genes) || ncol(counts) != nrow(samples)) {
  stop("pseudobulk matrix dimensions disagree with genes or samples")
}
if (anyDuplicated(genes$gene) || anyDuplicated(samples$sample_id)) {
  stop("genes and sample identities must be unique")
}
if (any(counts@x < 0) || any(abs(counts@x - round(counts@x)) > 1e-7)) {
  stop("pseudobulk counts must be nonnegative integers")
}

rownames(counts) <- genes$gene
colnames(counts) <- samples$sample_id
eligible_targets <- eligibility$target_uid[tolower(as.character(eligibility$eligible)) == "true"]
all_results <- list()
all_designs <- list()

for (target_uid in eligible_targets) {
  target_samples <- samples[samples$target_uid == target_uid, , drop = FALSE]
  control_samples <- samples[samples$target_uid == "NTC", , drop = FALSE]
  paired <- sort(intersect(target_samples$replicate, control_samples$replicate))
  if (length(paired) < 2L) {
    stop(paste("eligible target lacks two paired replicates:", target_uid))
  }
  selected <- samples$sample_id[
    samples$replicate %in% paired & samples$target_uid %in% c("NTC", target_uid)
  ]
  metadata <- samples[match(selected, samples$sample_id), , drop = FALSE]
  metadata$replicate <- factor(metadata$replicate, levels = paired)
  metadata$condition <- factor(metadata$condition, levels = c("control", "target"))
  design <- model.matrix(~ replicate + condition, data = metadata)
  if (qr(design)$rank != ncol(design)) {
    stop(paste("rank-deficient trans-DE design for", target_uid))
  }
  target_counts <- counts[, selected, drop = FALSE]
  y <- DGEList(counts = target_counts, genes = genes)
  keep <- filterByExpr(y, design = design)
  if (sum(keep) < 2L) {
    stop(paste("filterByExpr retained fewer than two genes for", target_uid))
  }
  y <- y[keep, , keep.lib.sizes = FALSE]
  y <- calcNormFactors(y)
  y <- estimateDisp(y, design, robust = TRUE)
  fit <- glmQLFit(y, design, robust = TRUE)
  test <- glmQLFTest(fit, coef = "conditiontarget")
  table <- topTags(test, n = Inf, sort.by = "none")$table
  table$gene <- rownames(table)
  table$target_uid <- target_uid
  table$PValue[!is.finite(table$PValue)] <- 1
  table$FDR <- p.adjust(table$PValue, method = "BH")
  full_table <- data.frame(
    target_uid = target_uid,
    gene = genes$gene,
    logFC = 0,
    logCPM = NA_real_,
    F = 0,
    PValue = 1,
    FDR = 1,
    tested = FALSE,
    stringsAsFactors = FALSE
  )
  positions <- match(table$gene, full_table$gene)
  full_table$logFC[positions] <- table$logFC
  full_table$logCPM[positions] <- table$logCPM
  full_table$F[positions] <- table$F
  full_table$PValue[positions] <- table$PValue
  full_table$FDR[positions] <- table$FDR
  full_table$tested[positions] <- TRUE
  all_results[[target_uid]] <- full_table

  design_rows <- as.data.frame(design, check.names = FALSE)
  design_rows$target_uid <- target_uid
  design_rows$sample_id <- metadata$sample_id
  design_rows$replicate_label <- as.character(metadata$replicate)
  design_rows$condition_label <- as.character(metadata$condition)
  all_designs[[target_uid]] <- design_rows[, c(
    "target_uid", "sample_id", "replicate_label", "condition_label", colnames(design)
  )]
}

results <- do.call(rbind, all_results)
designs <- do.call(rbind, all_designs)
rownames(results) <- NULL
rownames(designs) <- NULL
write.table(
  results,
  file = file.path(output, "trans_de_reference.tsv"),
  sep = "\t", quote = FALSE, row.names = FALSE, na = ""
)
write.table(
  designs,
  file = file.path(output, "design_matrices.tsv"),
  sep = "\t", quote = FALSE, row.names = FALSE, na = ""
)
write_json(
  list(
    schema_version = "pertura-paper-trans-de-reference-v1",
    design = "~ replicate + condition",
    baseline = "NTC",
    cell_is_replicate = FALSE,
    guide_is_replicate = FALSE,
    minimum_paired_replicates = 2L,
    gene_filter = "edgeR::filterByExpr(y, design)",
    normalization = "edgeR::calcNormFactors",
    fit = "edgeR quasi-likelihood with robust=TRUE",
    targets = as.list(eligible_targets),
    versions = list(
      R = paste(R.version$major, R.version$minor, sep = "."),
      edgeR = as.character(packageVersion("edgeR")),
      Matrix = as.character(packageVersion("Matrix"))
    )
  ),
  path = file.path(output, "reference_design_manifest.json"),
  pretty = TRUE,
  auto_unbox = TRUE
)
