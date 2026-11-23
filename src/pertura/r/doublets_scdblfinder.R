#!/usr/bin/env Rscript
# pertura script: scDblFinder doublet calls, per unit (samples = the dataset's unit column), on raw counts.
# Input:  $PERTURA_DBL_DIR/counts.mtx (cells x genes, Matrix Market), cells.tsv (cell, unit)
# Output: $PERTURA_DBL_DIR/calls.tsv (cell, score, class)
# Usage:  Rscript doublets_scdblfinder.R <seed>
suppressMessages({library(scDblFinder); library(SingleCellExperiment); library(Matrix); library(data.table)})
args <- commandArgs(trailingOnly = TRUE)
seed <- if (length(args)) as.integer(args[1]) else 0
base <- Sys.getenv("PERTURA_DBL_DIR")
m <- t(as(readMM(file.path(base, "counts.mtx")), "CsparseMatrix"))   # genes x cells
cells <- fread(file.path(base, "cells.tsv"), colClasses = "character")
colnames(m) <- cells$cell; rownames(m) <- paste0("g", seq_len(nrow(m)))
sce <- SingleCellExperiment(assays = list(counts = m))
set.seed(seed)
sce <- scDblFinder(sce, samples = cells$unit, BPPARAM = BiocParallel::SerialParam())
fwrite(data.table(cell = colnames(sce), score = sce$scDblFinder.score, class = as.character(sce$scDblFinder.class)),
       file.path(base, "calls.tsv"), sep = "\t")
cat("doublets:", sum(sce$scDblFinder.class == "doublet"), "of", ncol(sce), "\n")
