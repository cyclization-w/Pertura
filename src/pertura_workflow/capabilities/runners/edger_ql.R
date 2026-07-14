suppressPackageStartupMessages({
  library(edgeR)
  library(jsonlite)
})

args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 1) stop("expected config JSON path")
config <- fromJSON(args[[1]], simplifyVector = TRUE)

counts <- read.csv(config$counts_path, row.names = 1, check.names = FALSE)
samples <- read.csv(config$samples_path, check.names = FALSE, stringsAsFactors = FALSE)
counts <- as.matrix(counts)
storage.mode(counts) <- "integer"
if (any(counts < 0L)) stop("counts must be nonnegative")
if (!identical(colnames(counts), samples$sample_id)) stop("sample manifest does not align with pseudobulk counts")
if (anyDuplicated(samples$sample_id)) stop("sample manifest contains duplicate sample IDs")
rownames(samples) <- samples$sample_id

samples$condition <- factor(samples$condition, levels = c(config$baseline, config$target))
terms <- c()
if (isTRUE(config$paired)) terms <- c(terms, "replicate")
if (length(config$covariates) > 0) terms <- c(terms, config$covariates)
terms <- c(terms, "condition")
design <- model.matrix(reformulate(terms), data = samples)
if (qr(design)$rank < ncol(design)) stop("design matrix is not full rank")
coefficient <- grep("^condition", colnames(design))
if (length(coefficient) != 1) stop("condition contrast is not uniquely estimable")

y <- DGEList(counts = counts, samples = samples)
keep <- filterByExpr(y, design = design)
if (!any(keep)) stop("filterByExpr retained no genes")
y <- y[keep, , keep.lib.sizes = FALSE]
y <- calcNormFactors(y)
y <- estimateDisp(y, design)
fit <- glmQLFit(y, design)
test <- glmQLFTest(fit, coef = coefficient)
table <- topTags(test, n = Inf, sort.by = "none")$table
table$gene <- rownames(table)
dispersion <- fit$dispersion
if (is.null(dispersion) || !length(dispersion)) {
  stop("glmQLFit did not expose the fitted NB dispersion")
}
if (length(dispersion) == 1L) {
  dispersion <- rep(dispersion, nrow(y))
}
if (length(dispersion) != nrow(y)) {
  stop("glmQLFit dispersion does not align with the filtered expression matrix")
}
names(dispersion) <- rownames(y)
table$dispersion <- unname(dispersion[rownames(table)])
if (any(!is.finite(table$dispersion)) || any(table$dispersion < 0)) {
  stop("glmQLFit produced invalid NB dispersion values")
}
table <- table[, c("gene", "logFC", "F", "PValue", "FDR", "dispersion")]
write.csv(table, config$result_path, row.names = FALSE, quote = FALSE)

write.csv(data.frame(sample_id = rownames(design), design, check.names = FALSE), config$design_path, row.names = FALSE, quote = FALSE)
mds <- plotMDS(y, plot = FALSE)
write.csv(data.frame(sample_id = colnames(y), leading_logFC_1 = mds$x, leading_logFC_2 = mds$y), config$mds_path, row.names = FALSE, quote = FALSE)

environment <- list(
  R = paste(R.version$major, R.version$minor, sep = "."),
  Bioconductor = as.character(BiocManager::version()),
  edgeR = as.character(packageVersion("edgeR")),
  limma = as.character(packageVersion("limma")),
  jsonlite = as.character(packageVersion("jsonlite")),
  # capture.output() may retain names on some R builds. jsonlite then emits an
  # object rather than the provider-neutral string array expected by Pertura.
  sessionInfo = unname(as.character(capture.output(sessionInfo())))
)
write(toJSON(environment, auto_unbox = TRUE, pretty = TRUE), config$environment_path)
