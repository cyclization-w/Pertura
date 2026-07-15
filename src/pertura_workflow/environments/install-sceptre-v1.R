options(repos = c(CRAN = "https://cloud.r-project.org"))
options(timeout = 1200)
options(download.file.method = "libcurl")

target_version <- "0.99.0"
target_commit <- "4c26938061380fc782786fafaceb4345bf8fc9b2"

required_packages <- c(
  "BH", "cowplot", "crayon", "data.table", "dplyr", "ggplot2", "Matrix",
  "parallelly", "purrr", "Rcpp", "remotes", "scales", "withr"
)
missing_packages <- required_packages[
  !vapply(required_packages, requireNamespace, logical(1), quietly = TRUE)
]
if (length(missing_packages) > 0L) {
  stop(
    "pinned SCEPTRE runtime dependencies are missing from the Micromamba ",
    "environment: ", paste(missing_packages, collapse = ", ")
  )
}
if (packageVersion("Rcpp") < "1.0.9") {
  stop("SCEPTRE requires Rcpp >= 1.0.9")
}
if (packageVersion("parallelly") < "1.23.0") {
  stop("SCEPTRE requires parallelly >= 1.23.0")
}

already_installed <- requireNamespace("sceptre", quietly = TRUE) &&
  identical(as.character(packageVersion("sceptre")), target_version)

if (already_installed) {
  message("sceptre ", target_version, " is already installed; skipping bootstrap")
} else {
  # On older Linux hosts, R may not discover the CA bundle bundled with the
  # Micromamba prefix. Prefer that bundle before trying the host defaults.
  conda_prefix <- Sys.getenv("CONDA_PREFIX", unset = "")
  r_prefix <- normalizePath(
    file.path(R.home(), "..", ".."),
    winslash = "/",
    mustWork = FALSE
  )
  ca_candidates <- unique(c(
    Sys.getenv("SSL_CERT_FILE", unset = ""),
    if (nzchar(conda_prefix)) file.path(conda_prefix, "ssl", "cacert.pem") else "",
    file.path(r_prefix, "ssl", "cacert.pem"),
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/ssl/certs/ca-certificates.crt"
  ))
  ca_bundle <- ca_candidates[nzchar(ca_candidates) & file.exists(ca_candidates)][1]
  if (length(ca_bundle) == 1L && !is.na(ca_bundle)) {
    Sys.setenv(
      SSL_CERT_FILE = ca_bundle,
      CURL_CA_BUNDLE = ca_bundle,
      GIT_SSL_CAINFO = ca_bundle
    )
    message("using CA bundle: ", ca_bundle)
  }

  archive <- tempfile(pattern = "sceptre-", fileext = ".tar.gz")
  archive_urls <- c(
    paste0(
      "https://codeload.github.com/Katsevich-Lab/sceptre/tar.gz/",
      target_commit
    ),
    paste0(
      "https://github.com/Katsevich-Lab/sceptre/archive/",
      target_commit,
      ".tar.gz"
    )
  )
  source <- NULL
  acquisition_errors <- character()

  for (url in archive_urls) {
    acquired <- tryCatch(
      {
        status <- download.file(url, archive, mode = "wb", quiet = FALSE)
        if (!identical(status, 0L) || !file.exists(archive) || file.size(archive) == 0L) {
          stop("download returned no package archive")
        }
        TRUE
      },
      error = function(condition) {
        acquisition_errors <<- c(
          acquisition_errors,
          paste0(url, ": ", conditionMessage(condition))
        )
        FALSE
      }
    )
    if (acquired) {
      source <- archive
      message("downloaded sceptre source from ", url)
      break
    }
  }

  # GitHub's API and codeload endpoints can fail independently on older
  # clusters. An exact-commit checkout uses the same source identity without
  # requiring an API call.
  if (is.null(source)) {
    git <- unname(Sys.which("git"))
    if (!nzchar(git)) {
      stop(
        "unable to acquire sceptre ", target_version,
        " from codeload and git is unavailable: ",
        paste(acquisition_errors, collapse = "; ")
      )
    }
    source_dir <- tempfile(pattern = "sceptre-source-")
    clone_status <- system2(
      git,
      c(
        "clone", "--no-checkout",
        "https://github.com/Katsevich-Lab/sceptre.git", source_dir
      )
    )
    checkout_status <- if (identical(clone_status, 0L)) {
      system2(
        git,
        c(
          paste0("--git-dir=", file.path(source_dir, ".git")),
          paste0("--work-tree=", source_dir),
          "checkout", "--detach", target_commit
        )
      )
    } else {
      clone_status
    }
    observed_commit <- if (identical(checkout_status, 0L)) {
      system2(
        git,
        c(
          paste0("--git-dir=", file.path(source_dir, ".git")),
          "rev-parse", "HEAD"
        ),
        stdout = TRUE
      )
    } else {
      character()
    }
    if (!identical(checkout_status, 0L) ||
        !identical(unname(observed_commit), target_commit) ||
        !file.exists(file.path(source_dir, "DESCRIPTION"))) {
      stop(
        "unable to acquire sceptre ", target_version,
        " at commit ", target_commit,
        " by codeload or fixed-commit git clone: ",
        paste(acquisition_errors, collapse = "; ")
      )
    }
    source <- source_dir
    message("cloned sceptre source at fixed commit ", target_commit)
  }

  remotes::install_local(
    source,
    upgrade = "never",
    dependencies = FALSE,
    build = FALSE
  )
}

if (!requireNamespace("sceptre", quietly = TRUE) ||
    as.character(packageVersion("sceptre")) != target_version) {
  stop("sceptre ", target_version, " was not installed")
}
