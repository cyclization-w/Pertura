options(repos = c(CRAN = "https://cloud.r-project.org"))
options(timeout = 1200)

if (!requireNamespace("remotes", quietly = TRUE)) {
  install.packages("remotes")
}
remotes::install_github(
  "Katsevich-Lab/sceptre",
  ref = "0.99.0",
  upgrade = "never",
  dependencies = TRUE
)
if (as.character(packageVersion("sceptre")) != "0.99.0") {
  stop("sceptre 0.99.0 was not installed")
}
