#!/usr/bin/env Rscript
# pertura executor script: unchanged copy of legacy/tool_search/s2_expression_tools.R (the September pipeline), so
# pseudobulk results match the earlier analyses exactly. One change since: a group is fitted against its own
# reference REF_<group> where the sample sheet holds one (its control cells drawn within each condition, where a
# sample holds several); REF otherwise, as before. Called by pertura.executors.pseudobulk with PERTURA_EXPR_DIR
# pointing at a work directory holding pb/<level>__r0.counts.tsv.gz + .samples.tsv.
# Step 6 (R tools) of the screen-2 vertical slice: pseudobulk DE of every group (genes and decoys) vs REF.
# One model per group: samples of that group and of REF from the mice that have both (paired design ~ mouse + group),
# genes kept by edgeR::filterByExpr on that subset. (A first version fitted all groups in one ~75-coefficient model;
# refitting it per coefficient took > 40 CPU-minutes per table, so it was replaced by per-group paired models.)
#   edger   calcNormFactors, estimateDisp, glmQLFit, glmQLFTest on the group coefficient (QL F-test)
#   limma   voom, lmFit, eBayes on the same design
#   deseq2  DESeq(~ mouse + group), Wald test on the group coefficient
# Input: expression/pb/<variant>__r<k>.counts.tsv.gz + .samples.tsv   Output: expression/r_results/<stem>__<tool>.tsv.gz
# Usage: Rscript s2_expression_tools.R <tool>[,<tool>] [max_round] [regex]
suppressMessages({library(edgeR); library(limma); library(data.table)})
args <- commandArgs(trailingOnly = TRUE)
tools <- strsplit(if (length(args)) args[1] else "edger,limma", ",")[[1]]
max_round <- if (length(args) > 1) as.integer(args[2]) else 4
base <- Sys.getenv("PERTURA_EXPR_DIR", "expression")
dir.create(file.path(base, "r_results"), showWarnings = FALSE)
stems <- sub("\\.counts\\.tsv\\.gz$", "", list.files(file.path(base, "pb"), pattern = "\\.counts\\.tsv\\.gz$"))
stems <- stems[as.integer(sub(".*__r([0-9]+)$", "\\1", stems)) <= max_round]
if (length(args) > 2) stems <- stems[grepl(args[3], stems)]
if ("deseq2" %in% tools) suppressMessages(library(DESeq2))
MIN_PAIRED_MICE <- as.integer(Sys.getenv("PERTURA_MIN_REPLICATES", "3"))  # design_fit.MIN_REPLICATES

fit_one <- function(tool, m, smp, g) {
  ref <- if (paste0("REF_", g) %in% smp$group) paste0("REF_", g) else "REF"  # the group's own reference, where drawn
  mice <- intersect(smp[group == g, mouse], smp[group == ref, mouse])
  if (length(mice) < MIN_PAIRED_MICE) return(NULL)
  s <- smp[group %in% c(ref, g) & mouse %in% mice]
  s[, group := relevel(factor(as.character(group)), ref = ref)]; s[, mouse := factor(as.character(mouse))]
  x <- m[, s$sample, drop = FALSE]
  design <- model.matrix(~ mouse + group, data = s)
  cn <- tail(colnames(design), 1)
  keep <- filterByExpr(DGEList(x), group = s$group)
  x <- x[keep, , drop = FALSE]
  if (tool == "edger") {
    y <- calcNormFactors(DGEList(x)); y <- estimateDisp(y, design); fit <- glmQLFit(y, design)
    tt <- topTags(glmQLFTest(fit, coef = cn), n = Inf, sort.by = "none")$table
    data.table(gene = rownames(tt), logFC = tt$logFC, p = tt$PValue)
  } else if (tool == "limma") {
    v <- voom(calcNormFactors(DGEList(x)), design); fit <- eBayes(lmFit(v, design))
    tt <- topTable(fit, coef = cn, number = Inf, sort.by = "none")
    data.table(gene = rownames(tt), logFC = tt$logFC, p = tt$P.Value)
  } else {
    dds <- DESeq(DESeqDataSetFromMatrix(x, as.data.frame(s), design = ~ mouse + group), quiet = TRUE)
    r <- results(dds, name = resultsNames(dds)[length(resultsNames(dds))])
    data.table(gene = rownames(r), logFC = r$log2FoldChange, p = r$pvalue)
  }
}

for (stem in stems) {
  cnt <- fread(file.path(base, "pb", paste0(stem, ".counts.tsv.gz")))
  genes <- cnt[[1]]; m <- as.matrix(cnt[, -1]); rownames(m) <- genes
  smp <- fread(file.path(base, "pb", paste0(stem, ".samples.tsv")))
  groups <- grep("^REF(_|$)", unique(smp$group), value = TRUE, invert = TRUE)  # REF_<group>: a reference
  for (tool in tools) {
    out_f <- file.path(base, "r_results", paste0(stem, "__", tool, ".tsv.gz"))
    if (file.exists(out_f)) next
    t0 <- Sys.time()
    res <- rbindlist(lapply(groups, function(g) {
      r <- try(fit_one(tool, m, smp, g), silent = TRUE)
      if (inherits(r, "try-error") || is.null(r)) NULL else r[, group := g]
    }))
    fwrite(res, out_f, sep = "\t")
    cat(stem, tool, uniqueN(res$group), "groups", nrow(res), "rows", round(as.numeric(difftime(Sys.time(), t0, units = "secs"))), "s\n")
  }
}
