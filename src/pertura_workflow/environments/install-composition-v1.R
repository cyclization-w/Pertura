options(repos = c(CRAN = "https://cloud.r-project.org"))
options(timeout = 1200)

if (!requireNamespace("BiocManager", quietly = TRUE)) {
  install.packages("BiocManager")
}
BiocManager::install(version = "3.22", ask = FALSE, update = FALSE)
BiocManager::install(c("speckle", "limma"), ask = FALSE, update = FALSE)

observed <- c(
  Bioconductor = as.character(BiocManager::version()),
  speckle = as.character(packageVersion("speckle")),
  limma = as.character(packageVersion("limma"))
)
expected <- c(Bioconductor = "3.22", speckle = "1.10.0", limma = "3.66.0")
if (any(observed != expected)) {
  stop(paste("pinned composition versions are unavailable:", paste(names(observed), observed, collapse = ", ")))
}
