#!/usr/bin/env Rscript
# Pertura's composition step, the propeller backend: speckle's propeller.ttest of each target's share of cells in
# each state against its reference, one model per target with its reference alone, in the samples both hold.
# The proportions are transformed by speckle's getTransformedProps over the states the two sides' cells have, as
# speckle's propeller does on those cells; a state of the labelling with no cell on either side has no test (p 1).
# Output: per state, the mean proportions, their ratio, limma's moderated t and its p.
# Input:  $PERTURA_COMP_DIR/cells/<key>.tsv.gz (unit, group G or REF, state): the target's and its reference's
#         cells in the samples both hold; $PERTURA_COMP_DIR/states.txt: every state of the labelling
# Output: $PERTURA_COMP_DIR/results/<key>.tsv (state, prop_target, prop_reference, prop_ratio, t, p)
# Usage:  Rscript propeller_by_target.R <logit|asin> <robust TRUE|FALSE> <trend TRUE|FALSE>
suppressMessages({library(speckle); library(limma); library(data.table)})
args <- commandArgs(trailingOnly = TRUE)
transform <- if (length(args) >= 1) args[1] else "asin"
robust <- if (length(args) >= 2) as.logical(args[2]) else TRUE
trend <- if (length(args) >= 3) as.logical(args[3]) else FALSE
base <- Sys.getenv("PERTURA_COMP_DIR")
states <- readLines(file.path(base, "states.txt"))
states <- states[nzchar(states)]
dir.create(file.path(base, "results"), showWarnings = FALSE)

for (f in list.files(file.path(base, "cells"), pattern = "[.]tsv[.]gz$", full.names = TRUE)) {
  key <- sub("[.]tsv[.]gz$", "", basename(f))
  d <- fread(f, colClasses = "character")
  d <- d[state %in% states]
  d[, sample := paste(unit, group, sep = "|")]
  here <- sort(unique(d$state))
  props <- suppressMessages(getTransformedProps(clusters = factor(d$state, levels = here), sample = d$sample,
                                                transform = transform))
  smp <- unique(d[, .(sample, unit, group)])
  smp <- smp[match(colnames(props$Proportions), smp$sample)]
  grp <- factor(ifelse(smp$group == "REF", "REF", "G"), levels = c("REF", "G"))
  unit <- factor(smp$unit)
  design <- model.matrix(~ 0 + grp + unit)
  ctr <- setNames(numeric(ncol(design)), colnames(design))
  ctr[c("grpG", "grpREF")] <- c(1, -1)
  out <- data.table(state = states, prop_target = 0, prop_reference = 0, prop_ratio = NA_real_, t = NA_real_,
                    p = 1)
  if (length(here) >= 1) {
    r <- suppressMessages(propeller.ttest(props, design, contrasts = ctr, robust = robust, trend = trend,
                                          sort = FALSE))
    i <- match(rownames(r), out$state)
    out$prop_target[i] <- r$PropMean.grpG
    out$prop_reference[i] <- r$PropMean.grpREF
    out$prop_ratio[i] <- r$PropRatio
    out$t[i] <- r$Tstatistic
    out$p[i] <- r$P.Value
  }
  fwrite(out, file.path(base, "results", paste0(key, ".tsv")), sep = "\t")
}
