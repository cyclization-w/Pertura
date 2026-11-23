#!/usr/bin/env Rscript
# pertura script: each cell's hashtag by Seurat's HTODemux or MULTIseqDemux, as Seurat's hashing vignette runs them
# (the hashtag counts CLR-normalised, then the demultiplexer with its defaults unless settings name others), one group
# (a Cell Ranger run) at a time; several methods in one run load Seurat once. Hashtags without a count in a group, and
# cells without a hashtag count, are left out of its run (the cells are called none).
# Input:  $PERTURA_HTO_DIR/counts.mtx (cells x hashtags, Matrix Market), cells.tsv (cell, group), settings.json
# Output: per method, $PERTURA_HTO_DIR/calls_<method>.tsv (cell, call, hashtag: call one, several or none; hashtag
#         the called one's column number, 1-based, for one) and groups_<method>.tsv (group, error)
# Usage:  Rscript hashtags_seurat.R <htodemux | multiseqdemux>[,<another>]
suppressMessages({library(Seurat); library(Matrix); library(data.table); library(jsonlite)})
args <- commandArgs(trailingOnly = TRUE)
methods <- strsplit(if (length(args)) args[1] else "htodemux", ",")[[1]]
base <- Sys.getenv("PERTURA_HTO_DIR")
m <- t(as(readMM(file.path(base, "counts.mtx")), "CsparseMatrix"))  # hashtags x cells
cells <- fread(file.path(base, "cells.tsv"), colClasses = "character")
settings <- if (file.exists(file.path(base, "settings.json"))) fromJSON(file.path(base, "settings.json")) else list()
rownames(m) <- paste0("h", seq_len(nrow(m)))  # plain names: Seurat rewrites '_' in feature names
colnames(m) <- paste0("c", seq_len(ncol(m)))
for (method in methods) {
  out <- data.table(cell = cells$cell, call = "none", hashtag = NA_integer_)
  errors <- data.table(group = character(), error = character())
  for (g in unique(cells$group)) {
    idx <- which(cells$group == g)
    sub <- m[, idx, drop = FALSE]
    sub <- sub[Matrix::rowSums(sub) > 0, , drop = FALSE]
    counted <- Matrix::colSums(sub) > 0  # cells without a hashtag count are no hashtag's: HTODemux cannot cluster them
    idx <- idx[counted]
    sub <- sub[, counted, drop = FALSE]
    res <- tryCatch({
      if (nrow(sub) < 2) stop("fewer than two hashtags with counts")
      obj <- CreateSeuratObject(counts = sub, assay = "HTO", min.cells = 0, min.features = 0)
      obj <- NormalizeData(obj, assay = "HTO", normalization.method = "CLR", verbose = FALSE)
      if (method == "htodemux") {
        obj <- do.call(HTODemux, c(list(object = obj, assay = "HTO", verbose = FALSE), settings))
        cls <- as.character(obj$HTO_classification.global)
        id <- as.character(obj$hash.ID)
        data.table(call = ifelse(cls == "Singlet", "one", ifelse(cls == "Doublet", "several", "none")),
                   hashtag = ifelse(cls == "Singlet", as.integer(sub("^h", "", id)), NA_integer_))
      } else if (method == "multiseqdemux") {
        obj <- do.call(MULTIseqDemux, c(list(object = obj, assay = "HTO", verbose = FALSE), settings))
        id <- as.character(obj$MULTI_ID)
        one <- grepl("^h[0-9]+$", id)
        data.table(call = ifelse(one, "one", ifelse(id == "Doublet", "several", "none")),
                   hashtag = ifelse(one, as.integer(sub("^h", "", id)), NA_integer_))
      } else stop(paste("unknown method", method))
    }, error = function(e) conditionMessage(e))
    if (is.character(res)) {
      errors <- rbind(errors, data.table(group = g, error = res))
    } else {
      out[idx, `:=`(call = res$call, hashtag = res$hashtag)]
    }
  }
  fwrite(out, file.path(base, paste0("calls_", method, ".tsv")), sep = "\t")
  fwrite(errors, file.path(base, paste0("groups_", method, ".tsv")), sep = "\t")
  cat(method, "hashtag calls:", sum(out$call == "one"), "one,", sum(out$call == "several"), "several,",
      sum(out$call == "none"), "none\n")
}
