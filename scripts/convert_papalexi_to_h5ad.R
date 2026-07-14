suppressPackageStartupMessages({
  library(Seurat)
  library(SeuratData)
  library(SeuratDisk)
  library(jsonlite)
  library(digest)
})

args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 1) stop("usage: Rscript convert_papalexi_to_h5ad.R OUTPUT.h5ad")
output <- normalizePath(args[[1]], mustWork = FALSE)
dir.create(dirname(output), recursive = TRUE, showWarnings = FALSE)

expected <- c(Seurat = "5.5.0", SeuratData = "0.2.2.9002")
observed <- c(
  Seurat = as.character(packageVersion("Seurat")),
  SeuratData = as.character(packageVersion("SeuratData"))
)
if (any(observed != expected)) {
  stop(paste("version mismatch:", paste(names(observed), observed, collapse = ", ")))
}

# Explicit networked dataset acquisition. This script is never called by an
# analysis capability and must be invoked by the benchmark maintainer.
InstallData(ds = "thp1.eccite")
if (as.character(packageVersion("thp1.eccite.SeuratData")) != "3.1.5") {
  stop("thp1.eccite.SeuratData version mismatch")
}
object <- LoadData(ds = "thp1.eccite")
temporary <- sub("\\.h5ad$", ".h5Seurat", output)
SaveH5Seurat(object, filename = temporary, overwrite = TRUE)
Convert(temporary, dest = "h5ad", overwrite = TRUE)
if (!file.exists(output)) {
  stop(paste("SeuratDisk conversion did not create the expected output:", output))
}

manifest <- list(
  schema_version = "pertura-benchmark-conversion-sidecar-v1",
  dataset_id = "papalexi_thp1_eccite",
  output = output,
  sha256 = paste0("sha256:", digest(file = output, algo = "sha256")),
  writer = "SeuratDisk::Convert",
  packages = as.list(c(
    observed,
    thp1.eccite.SeuratData = as.character(packageVersion("thp1.eccite.SeuratData")),
    SeuratDisk = as.character(packageVersion("SeuratDisk"))
  )),
  session_info = capture.output(sessionInfo())
)
write(toJSON(manifest, auto_unbox = TRUE, pretty = TRUE), paste0(output, ".manifest.json"))
