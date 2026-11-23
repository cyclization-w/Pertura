# Export a Seurat object's counts and cell metadata for pertura.cell_table.read_seurat: for each assay named, its
# counts layer (layers split by sample are joined first) as a Matrix Market file with its feature and barcode names,
# and the object's meta.data as CSV. Nothing is filtered or normalised.
#   Rscript seurat_export.R <object.rds> <out folder> <assay>[,<assay>...]
args <- commandArgs(trailingOnly = TRUE)
suppressPackageStartupMessages({
  library(SeuratObject)
  library(Matrix)
})
obj <- readRDS(args[1])
out <- args[2]
assays <- strsplit(args[3], ",", fixed = TRUE)[[1]]
dir.create(out, showWarnings = FALSE, recursive = TRUE)
have <- Assays(obj)
missing <- setdiff(assays, have)
if (length(missing)) stop(paste0("the object has no assay ", paste(missing, collapse = ", "),
                                 "; its assays: ", paste(have, collapse = ", ")))
for (a in assays) {
  x <- obj[[a]]
  if (inherits(x, "Assay5") && length(Layers(x, search = "counts")) > 1) x <- JoinLayers(x)
  m <- as(LayerData(x, layer = "counts"), "CsparseMatrix")
  if (!nrow(m) || !ncol(m)) stop(paste0("the assay ", a, " has no counts layer"))
  writeMM(m, file.path(out, paste0(a, "_matrix.mtx")))
  writeLines(rownames(m), file.path(out, paste0(a, "_features.txt")))
  writeLines(colnames(m), file.path(out, paste0(a, "_barcodes.txt")))
}
write.csv(obj@meta.data, file.path(out, "meta_data.csv"))
cat("exported", length(assays), "assays,", ncol(obj), "cells\n")
