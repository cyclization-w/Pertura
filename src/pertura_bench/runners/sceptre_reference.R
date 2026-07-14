args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 1) stop("expected reference config JSON path")

suppressPackageStartupMessages({
  library(jsonlite)
  library(Matrix)
  library(sceptre)
})

cfg <- fromJSON(args[[1]], simplifyVector = TRUE)
set.seed(as.integer(cfg$seed))
read_matrix <- function(path) {
  object <- readRDS(path)
  if (!inherits(object, "Matrix") && !is.matrix(object)) {
    stop("reference matrix RDS must contain a matrix")
  }
  as(object, "dgCMatrix")
}
response <- read_matrix(cfg$response_matrix_rds)
grna <- read_matrix(cfg$guide_matrix_rds)
if (!identical(colnames(response), colnames(grna))) {
  stop("response and gRNA matrices must have identical cell columns")
}
retained <- readLines(cfg$retained_cell_ids_path)
cells <- intersect(colnames(response), retained)
if (!length(cells)) stop("retained-cell manifest has no overlap")
response <- response[, cells, drop = FALSE]
grna <- grna[, cells, drop = FALSE]
guide_map <- read.csv(cfg$guide_target_map_path, stringsAsFactors = FALSE)
pairs <- read.csv(cfg$discovery_pairs_path, stringsAsFactors = FALSE)

object <- import_data(
  response_matrix = response,
  grna_matrix = grna,
  grna_target_data_frame = guide_map,
  moi = "high",
  extra_covariates = data.frame()
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
  n_processors = 1,
  output_amount = 1
)
calibration <- get_result(object, "run_calibration_check")
write.csv(calibration, cfg$calibration_result_path, row.names = FALSE, quote = FALSE)
p <- calibration$p_value[is.finite(calibration$p_value)]
type1 <- if (length(p)) mean(p <= 0.05) else 1
if (!is.finite(type1) || type1 > cfg$calibration_type1_threshold) {
  stop("independent SCEPTRE calibration check failed")
}
object <- run_discovery_analysis(
  object,
  output_amount = 1,
  print_progress = FALSE,
  parallel = FALSE,
  n_processors = 1
)
result <- get_result(object, "run_discovery_analysis")
result$FDR <- p.adjust(result$p_value, method = "BH")
write.csv(result, cfg$result_path, row.names = FALSE, quote = FALSE)
