# The programs step's GeneNMF backends (docs/adr/0031, D1a): GeneNMF (Carmona lab) on Seurat objects, with its
# defaults.
# - run: runNMF on the fitted cells (log-normalised by Seurat's NormalizeData, GeneNMF's variable genes), each
#   program's gene loadings; every cell's usage by RcppML's project with the loadings fixed, rows summing to 1.
# - meta: multiNMF over each sample's fitted cells at each k of a range, then getMetaPrograms: the programs that recur
#   across samples and k, each a set of genes with their confidence (the cells' scores are made by the caller).
#
# Usage: Rscript genenmf_programs.R <folder>
# The folder holds counts.mtx.gz (genes x cells, raw counts), genes.tsv (one name a line), cells.tsv (cell, sample,
# fit: 1 for the cells the programs are found on) and params.json (mode, k, k_min, k_max, nfeatures, min_exp, max_exp,
# n_mp, specificity_weight, weight_explained, min_confidence, seed).
# Writes usage.tsv (run: cells by programs), programs.tsv (program, rank, gene, weight; meta: weight is the gene's
# confidence), metrics.tsv (meta: each meta-program's statistics), versions.txt.
suppressMessages({
  library(Matrix)
  library(Seurat)
  library(GeneNMF)
  library(RcppML)
  library(jsonlite)
})
d <- commandArgs(trailingOnly = TRUE)[1]
p <- fromJSON(file.path(d, "params.json"))
m <- readMM(gzfile(file.path(d, "counts.mtx.gz")))
genes <- readLines(file.path(d, "genes.tsv"))
cells <- read.delim(file.path(d, "cells.tsv"), stringsAsFactors = FALSE, colClasses = "character")
rownames(m) <- make.unique(genes)
colnames(m) <- cells$cell
m <- as(m, "CsparseMatrix")
meta <- data.frame(row.names = cells$cell, sample = cells$sample)
obj <- CreateSeuratObject(counts = m, meta.data = meta, min.cells = 0, min.features = 0)
obj <- NormalizeData(obj, verbose = FALSE)
fit <- cells$cell[cells$fit == "1"]
set.seed(p$seed)
if (p$mode == "run") {
  f <- subset(obj, cells = fit)
  # the variable genes as multiNMF chooses them (GeneNMF's filter on Seurat's variable features)
  f <- findVariableFeatures_wfilters(f, nfeatures = p$nfeatures, min.exp = p$min_exp, max.exp = p$max_exp)
  f <- runNMF(f, k = p$k, assay = "RNA", seed = p$seed)
  w <- Loadings(f, reduction = "NMF")  # genes x programs
  colnames(w) <- sprintf("P%02d", seq_len(ncol(w)))
  data <- GetAssayData(obj, assay = "RNA", layer = "data")[rownames(w), , drop = FALSE]
  h <- RcppML::project(data, w = w)  # programs x cells
  h <- as.matrix(h)
  usage <- t(h)
  usage <- usage / pmax(rowSums(usage), .Machine$double.eps)
  rownames(usage) <- colnames(obj)
  colnames(usage) <- colnames(w)
  write.table(data.frame(cell = rownames(usage), usage, check.names = FALSE), file.path(d, "usage.tsv"), sep = "\t",
              quote = FALSE, row.names = FALSE)
  rows <- list()
  for (j in colnames(w)) {
    o <- order(w[, j], decreasing = TRUE)[seq_len(min(50, nrow(w)))]
    rows[[j]] <- data.frame(program = j, rank = seq_along(o), gene = rownames(w)[o], weight = w[o, j])
  }
  write.table(do.call(rbind, rows), file.path(d, "programs.tsv"), sep = "\t", quote = FALSE, row.names = FALSE)
} else {
  f <- subset(obj, cells = fit)
  parts <- SplitObject(f, split.by = "sample")
  parts <- parts[sapply(parts, ncol) >= p$min_cells]
  res <- multiNMF(parts, assay = "RNA", k = p$k_min:p$k_max, nfeatures = p$nfeatures, min.exp = p$min_exp,
                  max.exp = p$max_exp, seed = p$seed)
  mp <- getMetaPrograms(res, nMP = p$n_mp, specificity.weight = p$specificity_weight,
                        weight.explained = p$weight_explained, min.confidence = p$min_confidence)
  rows <- list()
  for (j in names(mp$metaprograms.genes)) {
    g <- mp$metaprograms.genes[[j]]
    conf <- if (!is.null(mp$metaprograms.genes.weights) && j %in% names(mp$metaprograms.genes.weights))
      mp$metaprograms.genes.weights[[j]][g] else rep(NA_real_, length(g))
    rows[[j]] <- data.frame(program = j, rank = seq_along(g), gene = g, weight = as.numeric(conf))
  }
  write.table(do.call(rbind, rows), file.path(d, "programs.tsv"), sep = "\t", quote = FALSE, row.names = FALSE)
  metrics <- mp$metaprograms.metrics
  write.table(data.frame(program = rownames(metrics), metrics, check.names = FALSE), file.path(d, "metrics.tsv"),
              sep = "\t", quote = FALSE, row.names = FALSE)
}
writeLines(c(paste("GeneNMF", as.character(packageVersion("GeneNMF"))),
             paste("RcppML", as.character(packageVersion("RcppML"))),
             paste("Seurat", as.character(packageVersion("Seurat")))), file.path(d, "versions.txt"))
