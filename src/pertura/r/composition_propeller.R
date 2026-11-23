#!/usr/bin/env Rscript
# pertura executor script: propeller (speckle) composition test of every group against REF, one model per group.
# Adapted from legacy/tool_search/s2_composition_tools.R (run_propeller): logit of (count + 0.5) / (n + 1) per sample
# (sample = unit x group), design ~ 0 + group + unit with a group-vs-REF contrast, propeller.ttest(robust = TRUE,
# trend = FALSE). Changes: every state in the table is tested (not four named states); each group is fitted with REF
# only (pairwise, so groups that share cells do not enter one model); samples need at least MIN_CELLS cells; the
# transform is propeller's arcsine square root instead of logit((k + 0.5) / (n + 1)), whose floor at k = 0 depends
# on n: a state absent from both the group and REF looked shifted whenever their sample sizes differed.
# Input:  $PERTURA_COMP_DIR/counts/<stem>.csv (unit, group, state, count), groups with neutral names, reference REF
# Output: $PERTURA_COMP_DIR/r_results/<stem>__propeller.tsv (group, state, effect = log2 PropRatio, p);
#         the ratio is clipped to [1e-6, 1e6] so states absent from one side keep a finite effect
# Usage:  Rscript composition_propeller.R <min_cells>
suppressMessages({library(speckle); library(limma); library(data.table)})
args <- commandArgs(trailingOnly = TRUE)
min_cells <- if (length(args)) as.integer(args[1]) else 5
base <- Sys.getenv("PERTURA_COMP_DIR")
dir.create(file.path(base, "r_results"), showWarnings = FALSE)
MIN_UNITS <- 3

fit_one <- function(tab, g, states) {
  t <- tab[group %in% c("REF", g)]
  t[, sample := paste(unit, group, sep = "|")]
  tot <- t[, .(n = sum(count)), by = .(sample, unit, group)]
  tot <- tot[n >= min_cells]
  units <- intersect(tot[group == g, unit], tot[group == "REF", unit])
  if (length(units) < MIN_UNITS) return(NULL)
  tot <- tot[unit %in% units]
  t <- t[sample %in% tot$sample]
  wide <- dcast(t, state ~ sample, value.var = "count", fun.aggregate = sum, fill = 0)
  wide <- merge(data.table(state = states), wide, by = "state", all.x = TRUE)
  for (j in names(wide)[-1]) set(wide, which(is.na(wide[[j]])), j, 0)
  counts <- as.matrix(wide[, -1]); rownames(counts) <- wide$state
  props <- t(t(counts) / colSums(counts))
  n_mat <- matrix(colSums(counts), nrow = nrow(counts), ncol = ncol(counts), byrow = TRUE)
  trans <- asin(sqrt(props))   # arcsine square root: no pseudo-count floor that depends on n (see header)
  prop.list <- list(Counts = counts, TransformedProps = trans, Proportions = props)
  smp <- tot[match(colnames(counts), sample)]
  smp[, group := factor(ifelse(group == "REF", "REF", "G"), levels = c("REF", "G"))]
  smp[, unit := factor(unit)]
  design <- model.matrix(~ 0 + group + unit, data = smp)
  ctr <- setNames(numeric(ncol(design)), colnames(design)); ctr[c("groupG", "groupREF")] <- c(1, -1)
  r <- propeller.ttest(prop.list, design, contrasts = ctr, robust = TRUE, trend = FALSE, sort = FALSE)
  r <- as.data.table(r, keep.rownames = "state")
  # a state absent from both sides has no ratio and no test: effect 0, p 1
  r[, .(group = g, state, effect = ifelse(is.na(PropRatio), 0, log2(pmin(pmax(PropRatio, 1e-6), 1e6))),
        p = ifelse(is.na(P.Value), 1, P.Value))]
}

files <- list.files(file.path(base, "counts"), pattern = "\\.csv$", full.names = TRUE)
for (f in files) {
  tab <- fread(f, colClasses = c(unit = "character", group = "character", state = "character"))
  stem <- sub("\\.csv$", "", basename(f))
  states <- sort(unique(tab$state))
  t0 <- Sys.time()
  res <- rbindlist(lapply(setdiff(unique(tab$group), "REF"), function(g) {
    r <- try(fit_one(tab, g, states), silent = TRUE)
    if (inherits(r, "try-error")) NULL else r
  }))
  fwrite(res, file.path(base, "r_results", paste0(stem, "__propeller.tsv")), sep = "\t")
  cat(stem, "propeller", uniqueN(res$group), "groups", nrow(res), "rows",
      round(as.numeric(difftime(Sys.time(), t0, units = "secs"))), "s\n")
}
