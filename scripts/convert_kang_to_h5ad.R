suppressPackageStartupMessages({
  library(muscData)
  library(anndataR)
  library(jsonlite)
  library(digest)
})

args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 1) stop("usage: Rscript convert_kang_to_h5ad.R OUTPUT.h5ad")
output <- normalizePath(args[[1]], mustWork = FALSE)
dir.create(dirname(output), recursive = TRUE, showWarnings = FALSE)
object <- muscData::Kang18_8vs8()
if (!"counts" %in% SummarizedExperiment::assayNames(object)) {
  stop("Kang18_8vs8 is missing the required counts assay")
}
anndataR::write_h5ad(
  object,
  output,
  compression = "gzip",
  mode = "w",
  x_mapping = "counts",
  layers_mapping = FALSE
)

manifest <- list(
  schema_version = "pertura-benchmark-conversion-sidecar-v1",
  dataset_id = "kang18_8vs8_pbmc",
  output = output,
  sha256 = paste0("sha256:", digest(file = output, algo = "sha256")),
  writer = "anndataR::write_h5ad",
  packages = list(
    R = paste(R.version$major, R.version$minor, sep = "."),
    muscData = as.character(packageVersion("muscData")),
    anndataR = as.character(packageVersion("anndataR")),
    rhdf5 = as.character(packageVersion("rhdf5"))
  ),
  session_info = capture.output(sessionInfo())
)
write(toJSON(manifest, auto_unbox = TRUE, pretty = TRUE), paste0(output, ".manifest.json"))
