#!/usr/bin/env Rscript
# Pertura's enrichment step, the clusterprofiler backend: clusterProfiler's enricher (over-representation, the
# hypergeometric test) or GSEA (fgsea inside) on the sets given as TERM2GENE, with its defaults (minGSSize 10,
# maxGSSize 500, Benjamini-Hochberg) and every set reported (pvalueCutoff and qvalueCutoff 1). enricher keeps the
# genes of the list and of the universe that some set holds: its documented rule.
# Input:  $PERTURA_ENRICH_DIR/sets.tsv (term, gene); for ora lists.tsv (list, gene) and universe.txt; for gsea
#         ranking.tsv (gene, stat)
# Output: $PERTURA_ENRICH_DIR/result.tsv: for ora, list, ID, Count, GeneRatio, BgRatio, pvalue, p.adjust, geneID;
#         for gsea, ID, setSize, NES, pvalue, p.adjust, core_enrichment
# Usage:  Rscript enrich_clusterprofiler.R <ora|gsea> <minGSSize> <maxGSSize> <seed>
suppressMessages({library(clusterProfiler); library(data.table)})
args <- commandArgs(trailingOnly = TRUE)
method <- args[1]
min_size <- as.integer(args[2])
max_size <- as.integer(args[3])
seed <- as.integer(args[4])
base <- Sys.getenv("PERTURA_ENRICH_DIR")
t2g <- fread(file.path(base, "sets.tsv"), colClasses = "character")
out <- data.table()
if (method == "ora") {
  lists <- fread(file.path(base, "lists.tsv"), colClasses = "character")
  universe <- readLines(file.path(base, "universe.txt"))
  for (name in unique(lists$list)) {
    e <- enricher(lists[list == name, gene], universe = universe, TERM2GENE = t2g, pvalueCutoff = 1,
                  qvalueCutoff = 1, minGSSize = min_size, maxGSSize = max_size, pAdjustMethod = "BH")
    if (!is.null(e) && nrow(as.data.frame(e))) {
      r <- as.data.table(as.data.frame(e))[, .(ID, Count, GeneRatio, BgRatio, pvalue, p.adjust, geneID)]
      out <- rbind(out, cbind(list = name, r))
    }
  }
} else {
  rk <- fread(file.path(base, "ranking.tsv"), colClasses = c(gene = "character", stat = "numeric"))
  stats <- sort(setNames(rk$stat, rk$gene), decreasing = TRUE)
  set.seed(seed)
  e <- GSEA(stats, TERM2GENE = t2g, minGSSize = min_size, maxGSSize = max_size, pvalueCutoff = 1, eps = 1e-10,
            pAdjustMethod = "BH", seed = TRUE, verbose = FALSE)
  if (!is.null(e) && nrow(as.data.frame(e))) {
    out <- as.data.table(as.data.frame(e))[, .(ID, setSize, NES, pvalue, p.adjust, core_enrichment)]
  }
}
fwrite(out, file.path(base, "result.tsv"), sep = "\t")
