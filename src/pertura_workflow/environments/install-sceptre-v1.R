options(repos = c(CRAN = "https://cloud.r-project.org"))
options(timeout = 1200)
options(download.file.method = "libcurl")

target_version <- "0.99.0"

if (!requireNamespace("remotes", quietly = TRUE)) {
  install.packages("remotes")
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
      "https://codeload.github.com/Katsevich-Lab/sceptre/tar.gz/refs/tags/",
      target_version
    ),
    paste0(
      "https://github.com/Katsevich-Lab/sceptre/archive/refs/tags/",
      target_version,
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
  # clusters. A shallow fixed-tag clone uses the same source identity without
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
        "clone", "--depth", "1", "--branch", target_version,
        "https://github.com/Katsevich-Lab/sceptre.git", source_dir
      )
    )
    if (!identical(clone_status, 0L) || !file.exists(file.path(source_dir, "DESCRIPTION"))) {
      stop(
        "unable to acquire sceptre ", target_version,
        " by codeload or fixed-tag git clone: ",
        paste(acquisition_errors, collapse = "; ")
      )
    }
    source <- source_dir
    message("cloned sceptre source at fixed tag ", target_version)
  }

  remotes::install_local(
    source,
    upgrade = "never",
    dependencies = TRUE
  )
}

if (!requireNamespace("sceptre", quietly = TRUE) ||
    as.character(packageVersion("sceptre")) != target_version) {
  stop("sceptre ", target_version, " was not installed")
}
