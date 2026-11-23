#!/usr/bin/env Rscript
# The control cells' change between two conditions, tested on units (docs/adr/0045, D4). Called by
# pertura.question_modules.e.controls_by_unit with PERTURA_EXPR_DIR pointing at a work directory holding
# pb/conditions.counts.tsv.gz (genes x samples, each a unit's control cells in one condition, summed) and
# pb/conditions.samples.tsv (sample, mouse = unit, group FIRST or SECOND, n_cells, and pair where a batch holds units
# of both conditions). Genes kept by edgeR's filterByExpr; then calcNormFactors, estimateDisp, glmQLFit and glmQLFTest
# on the condition's coefficient, as pseudobulk_de.R fits a target: ~ pair + group where the samples carry a pair,
# else ~ group. Output: r_results/conditions__edger.tsv.gz (gene, logFC, p).
suppressMessages({library(edgeR); library(data.table)})
base <- Sys.getenv("PERTURA_EXPR_DIR")
dir.create(file.path(base, "r_results"), showWarnings = FALSE)
cnt <- fread(file.path(base, "pb", "conditions.counts.tsv.gz"))
genes <- cnt[[1]]; m <- as.matrix(cnt[, -1]); rownames(m) <- genes
s <- fread(file.path(base, "pb", "conditions.samples.tsv"))
s[, group := relevel(factor(as.character(group)), ref = "FIRST")]
if ("pair" %in% names(s)) {
  s[, pair := factor(as.character(pair))]
  design <- model.matrix(~ pair + group, data = s)
} else {
  design <- model.matrix(~ group, data = s)
}
x <- m[, s$sample, drop = FALSE]
keep <- filterByExpr(DGEList(x), group = s$group)
y <- calcNormFactors(DGEList(x[keep, , drop = FALSE])); y <- estimateDisp(y, design); fit <- glmQLFit(y, design)
tt <- topTags(glmQLFTest(fit, coef = tail(colnames(design), 1)), n = Inf, sort.by = "none")$table
fwrite(data.table(gene = rownames(tt), logFC = tt$logFC, p = tt$PValue),
       file.path(base, "r_results", "conditions__edger.tsv.gz"), sep = "\t")
