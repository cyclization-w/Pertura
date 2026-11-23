# C.genes' cell-level tests (docs/build_goal_adr0022.md, step 3, item 2; docs/adr/0031, D1a): Seurat's FindMarkers on
# each comparison, with Seurat's defaults (LogNormalize to 10,000, logfc.threshold, min.pct). MAST and LR take the
# sample as a latent variable where a comparison's cells span two or more samples, and then compare the two sides
# within the samples both hold; MAST on one sample becomes the Wilcoxon test, LR on one sample has no latent variable.
#
# Usage: Rscript findmarkers_de.R <folder> [<part>]
# The folder holds counts.mtx.gz (genes x cells, raw counts), genes.tsv (one name a line), cells.tsv (cell, group,
# sample) and comparisons.tsv, or comparisons_<part>.tsv for one part of them run in its own process (name, ident1: a
# group, ident2: groups joined by ";", test: MAST, LR or wilcox).
# Writes findmarkers.tsv.gz, or findmarkers_<part>.tsv.gz (name, gene, p_val, avg_log2FC, pct.1, pct.2, p_val_adj,
# test, latent); findmarkers_status.tsv, or findmarkers_status_<part>.tsv (name, status: "ran" or the error, cells_1,
# cells_2, samples, test, latent); and versions.txt. After each comparison, a line in progress.txt, or
# progress_<part>.txt, for the job's progress.
suppressMessages({
  library(Matrix)
  library(Seurat)
})
args <- commandArgs(trailingOnly = TRUE)
d <- args[1]
part <- if (length(args) > 1) paste0("_", args[2]) else ""
m <- readMM(gzfile(file.path(d, "counts.mtx.gz")))
genes <- readLines(file.path(d, "genes.tsv"))
cells <- read.delim(file.path(d, "cells.tsv"), stringsAsFactors = FALSE, colClasses = "character")
rownames(m) <- make.unique(genes)
colnames(m) <- cells$cell
m <- as(m, "CsparseMatrix")
meta <- data.frame(row.names = cells$cell, group = cells$group, sample = cells$sample)
crossed <- "condition" %in% names(cells)  # each cell's condition, given where a sample holds several
# named cell_condition: Seurat's MAST names its own group column "condition" and would overwrite it
if (crossed) meta$cell_condition <- cells$condition
cycle <- all(c("S", "G2M") %in% names(cells))  # the cell-cycle scores, as latent variables beside the sample
if (cycle) {
  meta$S <- as.numeric(cells$S)
  meta$G2M <- as.numeric(cells$G2M)
}
obj <- CreateSeuratObject(counts = m, meta.data = meta, min.cells = 0, min.features = 0)
obj <- NormalizeData(obj, verbose = FALSE)
cmp <- read.delim(file.path(d, paste0("comparisons", part, ".tsv")), stringsAsFactors = FALSE,
                  colClasses = "character")
# Seurat's own cap on the cells of each side (max.cells.per.ident; its default, Inf, where none is given)
cap <- Sys.getenv("PERTURA_MAX_CELLS_PER_IDENT", "")
cap <- if (nzchar(cap)) as.numeric(cap) else Inf
# Seurat's own filters, as its defaults unless a call changes them
logfc <- as.numeric(Sys.getenv("PERTURA_LOGFC_THRESHOLD", "0.1"))
min.pct <- as.numeric(Sys.getenv("PERTURA_MIN_PCT", "0.01"))
out <- list()
status <- list()
for (i in seq_len(nrow(cmp))) {
  id2 <- strsplit(cmp$ident2[i], ";", fixed = TRUE)[[1]]
  side <- ifelse(obj$group == cmp$ident1[i], "a", ifelse(obj$group %in% id2, "b", "other"))
  test <- cmp$test[i]
  used <- c(which(side == "a"), which(side == "b"))
  latent <- NULL
  where <- if (crossed) paste(obj$sample, obj$cell_condition, sep = "|") else obj$sample
  if (test %in% c("MAST", "LR") && length(unique(where[used])) > 1) {
    # the sample as a covariate (and the condition, where a sample holds several): the two sides compared within
    # the samples (and conditions) both hold
    shared <- intersect(unique(where[side == "a"]), unique(where[side == "b"]))
    side[side != "other" & !(where %in% shared)] <- "other"
    used <- c(which(side == "a"), which(side == "b"))
    if (length(unique(obj$sample[used])) > 1) latent <- "sample"
    if (crossed && length(unique(obj$cell_condition[used])) > 1) latent <- c(latent, "cell_condition")
  }
  if (cycle && test %in% c("MAST", "LR")) latent <- c(latent, "S", "G2M")
  if (test == "MAST" && is.null(latent)) test <- "wilcox"  # one sample: no covariate to adjust for
  Idents(obj) <- factor(side, levels = c("a", "b", "other"))
  feats <- NULL
  if ("features" %in% names(cmp) && nzchar(cmp$features[i])) feats <- strsplit(cmp$features[i], ";", fixed = TRUE)[[1]]
  failure <- NULL
  r <- tryCatch(
    FindMarkers(obj, ident.1 = "a", ident.2 = "b", test.use = test, latent.vars = latent, features = feats,
                logfc.threshold = if (is.null(feats)) logfc else 0, min.pct = if (is.null(feats)) min.pct else 0,
                max.cells.per.ident = cap, verbose = FALSE),
    error = function(e) {
      message(cmp$name[i], ": ", conditionMessage(e))
      failure <<- gsub("[[:space:]]+", " ", conditionMessage(e))
      NULL
    })
  cat(cmp$name[i], "\n", sep = "", file = file.path(d, paste0("progress", part, ".txt")), append = TRUE)
  status[[length(status) + 1]] <- data.frame(
    name = cmp$name[i], status = if (is.null(failure)) "ran" else failure, cells_1 = sum(side == "a"),
    cells_2 = sum(side == "b"), samples = length(unique(obj$sample[used])), test = test,
    latent = if (is.null(latent)) "" else paste(latent, collapse = ";"))
  if (is.null(r) || nrow(r) == 0) next
  r$gene <- rownames(r)
  r$name <- cmp$name[i]
  r$test <- test
  r$latent <- if (is.null(latent)) "" else paste(latent, collapse = ";")
  out[[length(out) + 1]] <- r
}
cols <- c("name", "gene", "p_val", "avg_log2FC", "pct.1", "pct.2", "p_val_adj", "test", "latent")
res <- if (length(out)) do.call(rbind, out)[, cols] else setNames(data.frame(matrix(ncol = 9, nrow = 0)), cols)
write.table(res, gzfile(file.path(d, paste0("findmarkers", part, ".tsv.gz"))), sep = "\t", quote = FALSE,
            row.names = FALSE)
st_cols <- c("name", "status", "cells_1", "cells_2", "samples", "test", "latent")
st <- if (length(status)) do.call(rbind, status) else setNames(data.frame(matrix(ncol = 7, nrow = 0)), st_cols)
write.table(st, file.path(d, paste0("findmarkers_status", part, ".tsv")), sep = "\t", quote = FALSE,
            row.names = FALSE)
writeLines(c(paste("Seurat", as.character(packageVersion("Seurat"))),
             paste("MAST", as.character(packageVersion("MAST")))), file.path(d, "versions.txt"))
