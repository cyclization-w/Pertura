suppressPackageStartupMessages({
  library(Seurat)
  library(SeuratData)
  library(anndataR)
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

expected_native_writer <- c(anndataR = "1.0.2", rhdf5 = "2.54.1")
observed_native_writer <- c(
  anndataR = as.character(packageVersion("anndataR")),
  rhdf5 = as.character(packageVersion("rhdf5"))
)
if (any(observed_native_writer != expected_native_writer)) {
  stop(paste(
    "native H5AD writer version mismatch:",
    paste(names(observed_native_writer), observed_native_writer, collapse = ", ")
  ))
}

expected_commits <- c(
  SeuratData = "3e51f44303069b64f5dc4d68e6a3d4a343f55c39"
)
observed_commits <- c(
  SeuratData = packageDescription("SeuratData", fields = "RemoteSha")
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
required_metadata <- c("guide_ID", "gene", "con", "NT", "crispr", "replicate")
missing_metadata <- setdiff(required_metadata, colnames(object[[]]))
if (length(missing_metadata) > 0) {
  stop(paste(
    "Papalexi object is missing required cell metadata:",
    paste(missing_metadata, collapse = ", ")
  ))
}
rna_layers <- SeuratObject::Layers(object[["RNA"]])
if (!all(c("counts", "data") %in% rna_layers)) {
  stop("Papalexi RNA assay must contain counts and data layers")
}

# SeuratDisk still calls the defunct SeuratObject GetAssayData(slot=...)
# interface. anndataR writes Seurat v5 layers natively and avoids a Python or
# basilisk dependency. Raw RNA counts are the primary matrix; normalized RNA
# values are retained as a separate layer and all cell metadata are preserved.
anndataR::write_h5ad(
  object,
  output,
  assay_name = "RNA",
  x_mapping = "counts",
  layers_mapping = c(data = "data"),
  obs_mapping = TRUE,
  var_mapping = TRUE,
  obsm_mapping = FALSE,
  varm_mapping = FALSE,
  obsp_mapping = FALSE,
  varp_mapping = FALSE,
  uns_mapping = FALSE,
  compression = "gzip",
  mode = "w"
)
if (!file.exists(output)) {
  stop(paste("anndataR conversion did not create the expected output:", output))
}

manifest <- list(
  schema_version = "pertura-benchmark-conversion-sidecar-v1",
  dataset_id = "papalexi_thp1_eccite",
  output = output,
  sha256 = paste0("sha256:", digest(file = output, algo = "sha256")),
  writer = "anndataR::write_h5ad",
  mapping = list(
    assay = "RNA",
    X = "counts",
    layers = list(data = "data"),
    obs = "all cell metadata",
    var = "all RNA feature metadata"
  ),
  source_artifact = list(
    md5 = paste0("md5:", observed_source_md5),
    sha256 = paste0("sha256:", observed_source_sha256)
  ),
  source_commits = as.list(observed_commits),
  packages = list(
    R = paste(R.version$major, R.version$minor, sep = "."),
    Seurat = unname(observed[["Seurat"]]),
    SeuratData = unname(observed[["SeuratData"]]),
    thp1.eccite.SeuratData = as.character(packageVersion("thp1.eccite.SeuratData")),
    anndataR = unname(observed_native_writer[["anndataR"]]),
    rhdf5 = unname(observed_native_writer[["rhdf5"]])
  ),
  session_info = capture.output(sessionInfo())
)
write(toJSON(manifest, auto_unbox = TRUE, pretty = TRUE), paste0(output, ".manifest.json"))
