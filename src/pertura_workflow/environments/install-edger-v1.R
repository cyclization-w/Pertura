options(repos = c(CRAN = "https://cloud.r-project.org"))
options(timeout = 1200)

if (!requireNamespace("BiocManager", quietly = TRUE)) {
  install.packages("BiocManager")
}
BiocManager::install(version = "3.22", ask = FALSE, update = FALSE)
BiocManager::install(c("edgeR", "limma"), ask = FALSE, update = FALSE)

observed <- c(
  Bioconductor = as.character(BiocManager::version()),
  edgeR = as.character(packageVersion("edgeR")),
  limma = as.character(packageVersion("limma"))
)
expected <- c(Bioconductor = "3.22", edgeR = "4.8.2", limma = "3.66.0")
if (any(observed != expected)) {
  stop(paste("pinned Bioconductor package versions are unavailable:", paste(names(observed), observed, collapse = ", ")))
}
