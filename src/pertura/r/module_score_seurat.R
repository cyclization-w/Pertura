#!/usr/bin/env Rscript
# Pertura's scores step, the seurat backend: Seurat's AddModuleScore on the raw counts, log-normalised by
# NormalizeData (LogNormalize, 10,000), with its defaults (nbin 24, ctrl 100, seed 1): each set's mean expression
# minus that of control genes drawn from the same expression bins.
# Input:  $PERTURA_SCORES_DIR/counts.mtx.gz (genes x cells), genes.tsv, cells.txt, sets.tsv (set, gene)
# Output: $PERTURA_SCORES_DIR/scores.tsv.gz (cell, then one column per set)
# Usage:  Rscript module_score_seurat.R <nbin> <ctrl> <seed>
suppressMessages({library(Seurat); library(Matrix); library(data.table)})
args <- commandArgs(trailingOnly = TRUE)
nbin <- if (length(args) >= 1) as.integer(args[1]) else 24L
ctrl <- if (length(args) >= 2) as.integer(args[2]) else 100L
seed <- if (length(args) >= 3) as.integer(args[3]) else 1L
base <- Sys.getenv("PERTURA_SCORES_DIR")
counts <- as(readMM(gzfile(file.path(base, "counts.mtx.gz"))), "CsparseMatrix")
rownames(counts) <- readLines(file.path(base, "genes.tsv"))
colnames(counts) <- readLines(file.path(base, "cells.txt"))
sets <- fread(file.path(base, "sets.tsv"), colClasses = "character")
obj <- CreateSeuratObject(counts = counts)
obj <- NormalizeData(obj, verbose = FALSE)
features <- lapply(split(sets$gene, sets$set), function(g) intersect(g, rownames(obj)))
obj <- AddModuleScore(obj, features = features, name = "pertura_set_", nbin = nbin, ctrl = ctrl, seed = seed)
scores <- obj@meta.data[, paste0("pertura_set_", seq_along(features)), drop = FALSE]
colnames(scores) <- names(features)
fwrite(data.table(cell = rownames(scores), scores), file.path(base, "scores.tsv.gz"), sep = "\t")
