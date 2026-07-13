# Packages are supplied by the pinned Micromamba/Bioconda environment.
# This script intentionally performs no network access or package installation.
suppressPackageStartupMessages({
  library(speckle)
  library(limma)
  library(edgeR)
})

observed <- c(
  Bioconductor = as.character(BiocManager::version()),
  speckle = as.character(packageVersion("speckle")),
  limma = as.character(packageVersion("limma")),
  edgeR = as.character(packageVersion("edgeR"))
)
expected <- c(
  Bioconductor = "3.22",
  speckle = "1.10.0",
  limma = "3.66.0",
  edgeR = "4.8.2"
)
if (any(observed != expected)) {
  stop(paste("pinned composition versions are unavailable:", paste(names(observed), observed, collapse = ", ")))
}
