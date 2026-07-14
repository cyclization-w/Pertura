suppressPackageStartupMessages({
  library(Seurat)
  library(SeuratData)
  library(SeuratDisk)
  library(jsonlite)
  library(digest)
})

args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 2) {
  stop("usage: Rscript convert_papalexi_to_h5ad.R OUTPUT.h5ad SOURCE_PACKAGE.tar.gz")
}
output <- normalizePath(args[[1]], mustWork = FALSE)
source_package <- normalizePath(args[[2]], mustWork = TRUE)
dir.create(dirname(output), recursive = TRUE, showWarnings = FALSE)

expected_source_md5 <- "4884b7c5175a9e88dfe0d16f17965d43"
expected_source_sha256 <- "ed137f933f93c416b4480e970bd6937505c20c7dccaee0244b3c94d2c8f0ba1e"
observed_source_md5 <- digest(file = source_package, algo = "md5")
observed_source_sha256 <- digest(file = source_package, algo = "sha256")
if (
  observed_source_md5 != expected_source_md5 ||
  observed_source_sha256 != expected_source_sha256
) {
  stop("frozen thp1.eccite.SeuratData source package checksum mismatch")
}

expected <- c(Seurat = "5.5.0", SeuratData = "0.2.2.9002")
observed <- c(
  Seurat = as.character(packageVersion("Seurat")),
  SeuratData = as.character(packageVersion("SeuratData"))
)
if (any(observed != expected)) {
  stop(paste("version mismatch:", paste(names(observed), observed, collapse = ", ")))
}

expected_commits <- c(
  SeuratData = "3e51f44303069b64f5dc4d68e6a3d4a343f55c39",
  SeuratDisk = "877d4e18ab38c686f5db54f8cd290274ccdbe295"
)
observed_commits <- c(
  SeuratData = packageDescription("SeuratData", fields = "RemoteSha"),
  SeuratDisk = packageDescription("SeuratDisk", fields = "RemoteSha")
)
if (any(is.na(observed_commits)) || any(observed_commits != expected_commits)) {
  stop(paste(
    "Git source commit mismatch:",
    paste(names(observed_commits), observed_commits, collapse = ", ")
  ))
}

# Reinstall from the checksum-verified local source package. Conversion must
# never resolve or download a moving SeuratData package from the network.
install.packages(source_package, repos = NULL, type = "source")
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
  source_artifact = list(
    md5 = paste0("md5:", observed_source_md5),
    sha256 = paste0("sha256:", observed_source_sha256)
  ),
  source_commits = as.list(observed_commits),
  packages = as.list(c(
    R = paste(R.version$major, R.version$minor, sep = "."),
    observed,
    thp1.eccite.SeuratData = as.character(packageVersion("thp1.eccite.SeuratData")),
    SeuratDisk = as.character(packageVersion("SeuratDisk"))
  )),
  session_info = capture.output(sessionInfo())
)
write(toJSON(manifest, auto_unbox = TRUE, pretty = TRUE), paste0(output, ".manifest.json"))
