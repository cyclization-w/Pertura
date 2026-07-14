suppressPackageStartupMessages({
  library(Seurat)
  library(SeuratData)
  library(Matrix)
  library(jsonlite)
  library(digest)
})

args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 2L) {
  stop("usage: Rscript export_papalexi_guide_assets.R OUTPUT_DIR SOURCE_PACKAGE.tar.gz")
}

output_dir <- normalizePath(args[[1]], winslash = "/", mustWork = FALSE)
source_package <- normalizePath(args[[2]], winslash = "/", mustWork = TRUE)
dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)

expected_source_md5 <- "4884b7c5175a9e88dfe0d16f17965d43"
expected_source_sha256 <- "ed137f933f93c416b4480e970bd6937505c20c7dccaee0244b3c94d2c8f0ba1e"
actual_source_md5 <- digest::digest(file = source_package, algo = "md5")
actual_source_sha256 <- digest::digest(file = source_package, algo = "sha256")
if (!identical(actual_source_md5, expected_source_md5)) {
  stop("Papalexi source package MD5 does not match the frozen manifest")
}
if (!identical(actual_source_sha256, expected_source_sha256)) {
  stop("Papalexi source package SHA-256 does not match the frozen manifest")
}

required_versions <- c(
  Seurat = "5.5.0",
  SeuratData = "0.2.2.9002"
)
for (package in names(required_versions)) {
  actual <- as.character(packageVersion(package))
  if (!identical(actual, required_versions[[package]])) {
    stop(package, " version drift: expected ", required_versions[[package]], ", found ", actual)
  }
}

seuratdata_sha <- packageDescription("SeuratData", fields = "RemoteSha")
expected_seuratdata_sha <- "3e51f44303069b64f5dc4d68e6a3d4a343f55c39"
if (!identical(unname(seuratdata_sha), expected_seuratdata_sha)) {
  stop("SeuratData source commit drift")
}

install.packages(source_package, repos = NULL, type = "source", quiet = TRUE)
if (!identical(as.character(packageVersion("thp1.eccite.SeuratData")), "3.1.5")) {
  stop("thp1.eccite.SeuratData version drift")
}

suppressMessages(SeuratData::LoadData(ds = "thp1.eccite", type = "thp1.eccite"))
object <- get("thp1.eccite", envir = .GlobalEnv)
if (!inherits(object, "Seurat")) {
  stop("Papalexi dataset package did not load a Seurat object")
}
if (!("RNA" %in% names(object@assays)) || !("GDO" %in% names(object@assays))) {
  stop("Papalexi object is missing the RNA or GDO assay")
}

rna_counts <- SeuratObject::LayerData(object = object[["RNA"]], layer = "counts")
guide_counts <- SeuratObject::LayerData(object = object[["GDO"]], layer = "counts")
if (!inherits(guide_counts, "sparseMatrix")) {
  guide_counts <- as(guide_counts, "dgCMatrix")
}
if (!identical(colnames(rna_counts), colnames(guide_counts))) {
  stop("RNA and GDO cell barcodes are not identically ordered")
}
if (any(guide_counts@x < 0) || any(abs(guide_counts@x - round(guide_counts@x)) > 1e-8)) {
  stop("GDO counts must be nonnegative integer-like values")
}

guide_dir <- file.path(output_dir, "guide_matrix")
dir.create(guide_dir, recursive = TRUE, showWarnings = FALSE)
matrix_path <- file.path(guide_dir, "matrix.mtx")
barcodes_path <- file.path(guide_dir, "barcodes.tsv")
features_path <- file.path(guide_dir, "features.tsv")
rna_barcodes_path <- file.path(output_dir, "rna_barcodes.tsv")
guide_map_path <- file.path(output_dir, "guide_map.tsv")
metadata_path <- file.path(output_dir, "cell_metadata.tsv")

Matrix::writeMM(guide_counts, matrix_path)
write.table(
  colnames(guide_counts),
  barcodes_path,
  sep = "\t",
  quote = FALSE,
  row.names = FALSE,
  col.names = FALSE
)
write.table(
  data.frame(
    feature_id = rownames(guide_counts),
    feature_name = rownames(guide_counts),
    feature_type = rep("CRISPR Guide Capture", nrow(guide_counts)),
    check.names = FALSE
  ),
  features_path,
  sep = "\t",
  quote = FALSE,
  row.names = FALSE,
  col.names = FALSE
)
write.table(
  data.frame(cell_id = colnames(rna_counts), check.names = FALSE),
  rna_barcodes_path,
  sep = "\t",
  quote = FALSE,
  row.names = FALSE,
  col.names = TRUE
)

metadata <- object[[]]
metadata$cell_id <- rownames(metadata)
metadata <- metadata[colnames(rna_counts), , drop = FALSE]
metadata <- metadata[, c("cell_id", setdiff(colnames(metadata), "cell_id")), drop = FALSE]
write.table(
  metadata,
  metadata_path,
  sep = "\t",
  quote = FALSE,
  row.names = FALSE,
  col.names = TRUE,
  na = ""
)

observed_map <- unique(data.frame(
  guide = as.character(metadata$guide_ID),
  target = as.character(metadata$gene),
  stringsAsFactors = FALSE
))
observed_map <- observed_map[
  !is.na(observed_map$guide) & observed_map$guide != "" &
    !is.na(observed_map$target) & observed_map$target != "",
  ,
  drop = FALSE
]
guide_target_counts <- table(observed_map$guide)
if (any(guide_target_counts != 1L)) {
  stop("at least one assigned guide maps to multiple targets")
}
observed_targets <- setNames(observed_map$target, observed_map$guide)
all_guides <- rownames(guide_counts)
mapping_source <- ifelse(all_guides %in% names(observed_targets), "observed_assignment", "feature_name_rule")
targets <- unname(observed_targets[all_guides])
missing <- is.na(targets) | targets == ""
targets[missing] <- sub("g[0-9]+$", "", all_guides[missing])
if (any(targets == "")) {
  stop("guide target inference produced an empty target")
}
guide_map <- data.frame(
  guide = all_guides,
  target = targets,
  mapping_source = mapping_source,
  stringsAsFactors = FALSE,
  check.names = FALSE
)
write.table(
  guide_map,
  guide_map_path,
  sep = "\t",
  quote = FALSE,
  row.names = FALSE,
  col.names = TRUE
)

relative_files <- c(
  "guide_matrix/matrix.mtx",
  "guide_matrix/barcodes.tsv",
  "guide_matrix/features.tsv",
  "rna_barcodes.tsv",
  "guide_map.tsv",
  "cell_metadata.tsv"
)
file_hashes <- setNames(
  lapply(relative_files, function(path) {
    paste0("sha256:", digest::digest(file = file.path(output_dir, path), algo = "sha256"))
  }),
  relative_files
)
manifest <- list(
  schema_version = "pertura-papalexi-guide-assets-v1",
  dataset_id = "papalexi_thp1_eccite",
  source = list(
    package = "thp1.eccite.SeuratData",
    version = "3.1.5",
    md5 = paste0("md5:", actual_source_md5),
    sha256 = paste0("sha256:", actual_source_sha256)
  ),
  dimensions = list(
    cells = ncol(guide_counts),
    guides = nrow(guide_counts),
    nonzero_guide_counts = length(guide_counts@x)
  ),
  guide_map = list(
    observed_assignment_count = sum(mapping_source == "observed_assignment"),
    feature_name_rule_count = sum(mapping_source == "feature_name_rule")
  ),
  files = file_hashes,
  package_versions = list(
    R = paste(R.version$major, R.version$minor, sep = "."),
    Seurat = as.character(packageVersion("Seurat")),
    SeuratData = as.character(packageVersion("SeuratData")),
    Matrix = as.character(packageVersion("Matrix")),
    thp1.eccite.SeuratData = as.character(packageVersion("thp1.eccite.SeuratData"))
  )
)
writeLines(
  jsonlite::toJSON(manifest, auto_unbox = TRUE, pretty = TRUE, null = "null"),
  file.path(output_dir, "guide_assets_manifest.json"),
  useBytes = TRUE
)
