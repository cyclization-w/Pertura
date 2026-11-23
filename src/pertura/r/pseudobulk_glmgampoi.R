#!/usr/bin/env Rscript
# pertura tool script: pseudobulk DE with glmGamPoi, following the conventions of pseudobulk_de.R:
# - one model per group against REF, paired by unit (~ mouse + group, at least 3 shared units), or against the
#   group's own reference REF_<group> where the sample sheet holds one (drawn within each condition);
# - genes kept by edgeR::filterByExpr on that subset;
# - glm_gp with default size factors and overdispersion shrinkage; quasi-likelihood test of the group coefficient
#   (test_de). The coefficient is renamed 'pert' so group names never need quoting.
# Input:  PERTURA_EXPR_DIR/pb/<stem>.counts.tsv.gz + .samples.tsv
# Output: r_results/<stem>__glmgampoi.tsv.gz with columns gene, logFC, p, group
# Usage:  Rscript pseudobulk_glmgampoi.R glmgampoi [max_round] [regex]
suppressMessages({library(glmGamPoi); library(edgeR); library(data.table)})
args <- commandArgs(trailingOnly = TRUE)
max_round <- if (length(args) > 1) as.integer(args[2]) else 4
base <- Sys.getenv("PERTURA_EXPR_DIR")
if (base == "") stop("set PERTURA_EXPR_DIR")
dir.create(file.path(base, "r_results"), showWarnings = FALSE)
stems <- sub("\\.counts\\.tsv\\.gz$", "", list.files(file.path(base, "pb"), pattern = "\\.counts\\.tsv\\.gz$"))
stems <- stems[as.integer(sub(".*__r([0-9]+)$", "\\1", stems)) <= max_round]
if (length(args) > 2) stems <- stems[grepl(args[3], stems)]
MIN_PAIRED_UNITS <- 3

fit_one <- function(m, smp, g) {
  ref <- if (paste0("REF_", g) %in% smp$group) paste0("REF_", g) else "REF"  # the group's own reference, where drawn
  units <- intersect(smp[group == g, mouse], smp[group == ref, mouse])
  if (length(units) < MIN_PAIRED_UNITS) return(NULL)
  s <- smp[group %in% c(ref, g) & mouse %in% units]
  s[, group := relevel(factor(as.character(group)), ref = ref)]; s[, mouse := factor(as.character(mouse))]
  x <- m[, s$sample, drop = FALSE]
  design <- model.matrix(~ mouse + group, data = s)
  colnames(design)[ncol(design)] <- "pert"
  keep <- filterByExpr(DGEList(x), group = s$group)
  x <- x[keep, , drop = FALSE]
  fit <- glm_gp(x, design = design, on_disk = FALSE)
  res <- test_de(fit, contrast = pert)
  data.table(gene = res$name, logFC = res$lfc, p = res$pval)
}

for (stem in stems) {
  out_f <- file.path(base, "r_results", paste0(stem, "__glmgampoi.tsv.gz"))
  if (file.exists(out_f)) next
  cnt <- fread(file.path(base, "pb", paste0(stem, ".counts.tsv.gz")))
  genes <- cnt[[1]]; m <- as.matrix(cnt[, -1]); rownames(m) <- genes
  smp <- fread(file.path(base, "pb", paste0(stem, ".samples.tsv")))
  groups <- grep("^REF(_|$)", unique(smp$group), value = TRUE, invert = TRUE)  # REF_<group>: a reference
  t0 <- Sys.time()
  res <- rbindlist(lapply(groups, function(g) {
    r <- try(fit_one(m, smp, g), silent = TRUE)
    if (inherits(r, "try-error") || is.null(r)) NULL else r[, group := g]
  }))
  fwrite(res, out_f, sep = "\t")
  cat(stem, "glmgampoi", uniqueN(res$group), "groups", nrow(res), "rows",
      round(as.numeric(difftime(Sys.time(), t0, units = "secs"))), "s\n")
}
