suppressPackageStartupMessages({
  library(muscData)
  library(zellkonverter)
  library(jsonlite)
  library(digest)
})

args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 1) stop("usage: Rscript convert_kang_to_h5ad.R OUTPUT.h5ad")
output <- normalizePath(args[[1]], mustWork = FALSE)
dir.create(dirname(output), recursive = TRUE, showWarnings = FALSE)
object <- muscData::Kang18_8vs8()
anndata_environment <- "0.11.4"
zellkonverter::writeH5AD(
  object,
  output,
  version = anndata_environment
)

manifest <- list(
  schema_version = "pertura-benchmark-conversion-sidecar-v1",
  dataset_id = "kang18_8vs8_pbmc",
  output = output,
  sha256 = paste0("sha256:", digest(file = output, algo = "sha256")),
  packages = c(
    R = paste(R.version$major, R.version$minor, sep = "."),
    muscData = as.character(packageVersion("muscData")),
    zellkonverter = as.character(packageVersion("zellkonverter")),
    anndata_environment = anndata_environment
  ),
  session_info = capture.output(sessionInfo())
)
write(toJSON(manifest, auto_unbox = TRUE, pretty = TRUE), paste0(output, ".manifest.json"))
