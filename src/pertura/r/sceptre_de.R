#!/usr/bin/env Rscript
# Pertura's genes step, the sceptre backend: SCEPTRE (Barry, Katsevich et al.) on the screen's own cells and guide
# calls, at low MOI: each target's cells (its guides together, SCEPTRE's union) against the non-targeting cells,
# every gene of the response matrix as a trans pair. The cells are Pertura's (its QC already applied), so SCEPTRE's
# cell QC is off; its pair filters (7 non-zero cells on each side) are its defaults unless given. Each cell carries
# one guide (its strongest), so the guide-count covariates of SCEPTRE's default formula would be constant: the
# formula keeps its response covariates, with the sample where the cells span two or more, and the condition where
# cells.tsv gives it (a sample holding several conditions).
# Input:  $PERTURA_SCEPTRE_DIR/counts.mtx.gz (genes x cells), genes.tsv, cells.tsv (cell, guide, target, sample[,
#         condition])
# Output: $PERTURA_SCEPTRE_DIR/sceptre.tsv.gz (SCEPTRE's discovery result)
# Usage:  Rscript sceptre_de.R <n_nonzero_trt_thresh> <n_nonzero_cntrl_thresh> <side> <resampling_approximation>
suppressMessages({library(sceptre); library(Matrix); library(data.table)})
args <- commandArgs(trailingOnly = TRUE)
trt <- if (length(args) >= 1) as.integer(args[1]) else 7L
cntrl <- if (length(args) >= 2) as.integer(args[2]) else 7L
side <- if (length(args) >= 3) args[3] else "both"
approx <- if (length(args) >= 4) args[4] else "skew_normal"
base <- Sys.getenv("PERTURA_SCEPTRE_DIR")

counts <- as(readMM(gzfile(file.path(base, "counts.mtx.gz"))), "CsparseMatrix")
genes <- readLines(file.path(base, "genes.tsv"))
cells <- fread(file.path(base, "cells.tsv"), colClasses = "character")
rownames(counts) <- genes
colnames(counts) <- cells$cell
guides <- sort(unique(cells$guide))
grna <- sparseMatrix(i = match(cells$guide, guides), j = seq_len(nrow(cells)), x = 1,
                     dims = c(length(guides), nrow(cells)), dimnames = list(guides, cells$cell))
gtdf <- as.data.frame(unique(cells[, .(grna_id = guide, grna_target = target)]))
several <- length(unique(cells$sample)) > 1
# each cell's condition, given where a sample holds several: a covariate beside the sample
crossed <- "condition" %in% names(cells) && length(unique(cells$condition)) > 1
covs <- c(if (several) "sample", if (crossed) "condition")
extra <- if (length(covs)) as.data.frame(lapply(setNames(covs, covs), function(k) factor(cells[[k]]))) else
  data.frame()
obj <- import_data(response_matrix = counts, grna_matrix = grna, grna_target_data_frame = gtdf, moi = "low",
                   extra_covariates = extra)
form <- as.formula(paste0("~ log(response_n_umis) + log(response_n_nonzero)",
                          if (length(covs)) paste0(" + ", covs, collapse = "") else ""))
obj <- set_analysis_parameters(obj, discovery_pairs = construct_trans_pairs(obj), side = side,
                               formula_object = form, resampling_approximation = approx)
obj <- assign_grnas(obj, method = "thresholding", threshold = 1, print_progress = FALSE)
obj <- run_qc(obj, n_nonzero_trt_thresh = trt, n_nonzero_cntrl_thresh = cntrl, response_n_umis_range = c(0, 1),
              response_n_nonzero_range = c(0, 1), p_mito_threshold = 1)
obj <- run_discovery_analysis(obj, parallel = FALSE, print_progress = FALSE)
fwrite(as.data.table(get_result(obj, "run_discovery_analysis")), file.path(base, "sceptre.tsv.gz"), sep = "\t")
