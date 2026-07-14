args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 1) stop("expected reference config JSON path")

suppressPackageStartupMessages({
  library(jsonlite)
  library(Seurat)
})

cfg <- fromJSON(args[[1]], simplifyVector = TRUE)
set.seed(as.integer(cfg$seed))
object <- readRDS(cfg$seurat_rds_path)
if (!cfg$perturbation_column %in% colnames(object[[]])) {
  stop("Seurat reference lacks perturbation column")
}
object <- CalcPerturbSig(
  object = object,
  assay = cfg$assay,
  slot = cfg$slot,
  gd.class = cfg$perturbation_column,
  nt.cell.class = cfg$control,
  reduction = cfg$reduction,
  ndims = as.integer(cfg$n_dims),
  split.by = cfg$split_by,
  verbose = FALSE
)
object <- RunMixscape(
  object = object,
  assay = cfg$signature_assay,
  slot = "scale.data",
  labels = cfg$perturbation_column,
  nt.class.name = cfg$control,
  min.de.genes = as.integer(cfg$min_de_genes),
  logfc.threshold = cfg$logfc_threshold,
  iter.num = as.integer(cfg$iter_num),
  verbose = FALSE
)
metadata <- object[[]]
candidates <- grep("mixscape_class", colnames(metadata), value = TRUE)
if (!length(candidates)) stop("Seurat Mixscape did not produce a class column")
class_column <- candidates[[1]]
result <- data.frame(
  cell_id = rownames(metadata),
  perturbation = as.character(metadata[[cfg$perturbation_column]]),
  mixscape_class = as.character(metadata[[class_column]]),
  stringsAsFactors = FALSE
)
result <- result[order(result$cell_id), , drop = FALSE]
write.csv(result, cfg$result_path, row.names = FALSE, quote = FALSE)
