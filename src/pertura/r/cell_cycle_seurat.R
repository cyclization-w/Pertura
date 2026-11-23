# The cell-cycle step's Seurat backend (docs/adr/0031, D1a): Seurat's CellCycleScoring on log-normalised counts
# (NormalizeData, Seurat's defaults), with the S and G2/M genes given (Seurat's cc.genes.updated.2019, Tirosh et al.
# 2016).
#
# Usage: Rscript cell_cycle_seurat.R <folder>
# The folder holds counts.mtx.gz (genes x cells, raw counts), genes.tsv (one name a line), cells.tsv (cell) and
# params.json (s_genes, g2m_genes). Writes phases.tsv (cell, S_score, G2M_score, phase) and versions.txt.
suppressMessages({
  library(Matrix)
  library(Seurat)
  library(jsonlite)
})
d <- commandArgs(trailingOnly = TRUE)[1]
p <- fromJSON(file.path(d, "params.json"))
m <- readMM(gzfile(file.path(d, "counts.mtx.gz")))
genes <- readLines(file.path(d, "genes.tsv"))
cells <- read.delim(file.path(d, "cells.tsv"), stringsAsFactors = FALSE, colClasses = "character")
rownames(m) <- make.unique(genes)
colnames(m) <- cells$cell
obj <- CreateSeuratObject(counts = as(m, "CsparseMatrix"), min.cells = 0, min.features = 0)
obj <- NormalizeData(obj, verbose = FALSE)
obj <- CellCycleScoring(obj, s.features = p$s_genes, g2m.features = p$g2m_genes, set.ident = FALSE)
out <- data.frame(cell = colnames(obj), S_score = obj$S.Score, G2M_score = obj$G2M.Score, phase = obj$Phase)
write.table(out, file.path(d, "phases.tsv"), sep = "\t", quote = FALSE, row.names = FALSE)
writeLines(paste("Seurat", as.character(packageVersion("Seurat"))), file.path(d, "versions.txt"))
