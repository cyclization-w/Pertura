args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 1) stop("usage: sceptre_association.R <config.json>")

suppressPackageStartupMessages({
  library(jsonlite)
  library(Matrix)
  library(sceptre)
})

cfg <- fromJSON(args[[1]], simplifyVector = TRUE)
`%||%` <- function(x, y) if (is.null(x)) y else x
set.seed(as.integer(cfg$seed))
dir.create(cfg$output_dir, recursive = TRUE, showWarnings = FALSE)

read_named_matrix <- function(path, row_ids_path = NULL, cell_ids_path = NULL) {
  if (grepl("\\.mtx(\\.gz)?$", path, ignore.case = TRUE)) {
    matrix <- as(readMM(path), "dgCMatrix")
    if (is.null(row_ids_path) || is.null(cell_ids_path)) {
      stop("Matrix Market input requires row IDs and cell IDs")
    }
    rownames(matrix) <- readLines(row_ids_path)
    colnames(matrix) <- readLines(cell_ids_path)
    return(matrix)
  }
  table <- read.csv(path, check.names = FALSE, stringsAsFactors = FALSE)
  ids <- table[[1]]
  matrix <- as.matrix(table[, -1, drop = FALSE])
  storage.mode(matrix) <- "numeric"
  rownames(matrix) <- ids
  matrix
}

response <- read_named_matrix(
  cfg$response_matrix_path,
  cfg$response_ids_path %||% NULL,
  cfg$cell_ids_path %||% NULL
)
grna <- read_named_matrix(
  cfg$guide_matrix_path,
  cfg$guide_ids_path %||% NULL,
  cfg$cell_ids_path %||% NULL
)
if (!identical(colnames(response), colnames(grna))) {
  stop("response and gRNA matrices must have identical cell columns")
}
retained_cells <- readLines(cfg$retained_cell_ids_path)
selected_cells <- intersect(colnames(response), retained_cells)
if (!length(selected_cells)) {
  stop("retained-cell manifest has no overlap with SCEPTRE matrices")
}
response <- response[, selected_cells, drop = FALSE]
grna <- grna[, selected_cells, drop = FALSE]
guide_map <- read.csv(cfg$guide_target_map_path, stringsAsFactors = FALSE)
if (!all(c("grna_id", "grna_target") %in% colnames(guide_map))) {
  stop("guide target map must contain grna_id and grna_target")
}
pairs <- read.csv(cfg$discovery_pairs_path, stringsAsFactors = FALSE)
if (!all(c("grna_target", "response_id") %in% colnames(pairs))) {
  stop("discovery pairs must contain grna_target and response_id")
}
extra_covariates <- data.frame()
if (!is.null(cfg$covariates_path)) {
  extra_covariates <- read.csv(cfg$covariates_path, row.names = 1, check.names = FALSE)
  extra_covariates <- extra_covariates[colnames(response), , drop = FALSE]
}

object <- import_data(
  response_matrix = response,
  grna_matrix = grna,
  grna_target_data_frame = guide_map,
  moi = "high",
  extra_covariates = extra_covariates
)
object <- set_analysis_parameters(
  object,
  discovery_pairs = pairs,
  side = cfg$side,
  grna_integration_strategy = cfg$grna_integration_strategy,
  multiple_testing_method = "BH",
  multiple_testing_alpha = cfg$multiple_testing_alpha
)
object <- assign_grnas(object, method = cfg$assignment_method, parallel = FALSE)
object <- run_qc(object)
object <- run_calibration_check(
  object,
  n_calibration_pairs = as.integer(cfg$n_calibration_pairs),
  calibration_group_size = as.integer(cfg$calibration_group_size),
  print_progress = FALSE,
  parallel = FALSE,
  output_amount = 1
)
calibration <- get_result(object, "run_calibration_check")
write.csv(calibration, file.path(cfg$output_dir, "sceptre_calibration.csv"), row.names = FALSE)
valid_p <- calibration$p_value[is.finite(calibration$p_value)]
type1_rate <- if (length(valid_p)) mean(valid_p <= 0.05) else 1
calibration_passed <- is.finite(type1_rate) && type1_rate <= cfg$calibration_type1_threshold

metadata <- list(
  schema_version = "pertura-sceptre-metadata-v1",
  sceptre_version = as.character(packageVersion("sceptre")),
  calibration_type1_rate = type1_rate,
  calibration_threshold = cfg$calibration_type1_threshold,
  calibration_passed = calibration_passed,
  discovery_executed = FALSE,
  parallel = FALSE,
  seed = as.integer(cfg$seed)
)
if (calibration_passed) {
  object <- run_discovery_analysis(
    object,
    output_amount = 1,
    print_progress = FALSE,
    parallel = FALSE
  )
  result <- get_result(object, "run_discovery_analysis")
  result$FDR <- p.adjust(result$p_value, method = "BH")
  write.csv(result, file.path(cfg$output_dir, "sceptre_results.csv"), row.names = FALSE)
  metadata$discovery_executed <- TRUE
}
write_json(
  metadata,
  file.path(cfg$output_dir, "sceptre_metadata.json"),
  auto_unbox = TRUE,
  pretty = TRUE
)
