# Pertura reading

A Claude Code plugin that reads published single-cell CRISPR screen papers into Pertura's knowledge base. By default
one reader extracts the paper's results, methods and figure evidence into `results.json`. Code checks the sources,
returns feedback for up to two retries, then writes supported results to SQLite for `analysis`, `proceed` and
`figure` queries. Norman and Belk are worked examples of a linked comparison and multiple evidence types.

Start it with `/pertura-reader:read <DOIs, PubMed or PMC IDs, or PDFs>`. It asks for a folder to work in (the
library). Other research readings are available when requested.

## Other research readings

- **Follow-up moves:** what the authors did next after each result of their screen, each move in fixed fields with
  two quotes of the paper and the knowledge base's module that asks the same question. A second agent checks every
  row against the paper. A batch's first papers are read twice, independently, and code measures how far the two
  readings agree before the rest are read once.
- **The analysis chain:** the paper's path from its question to its conclusion.
- **Methods and parameters:** each analysis step's choice, package, values and the reason the paper gives.
- **The paper's code:** each step's package call and values, with the file and line, from the repository the paper
  names. The code is read as text and never run.
- **The figures:** for each figure, the conclusion the paper states; for each panel, what it shows (the kind of
  evidence), where its data come from (the screen, a follow-up experiment, the paper's other data, outside data,
  simulated data, or none) and the chart its legend names, with a quote of the paper. The readers see the text, not
  the images. Its version is fixed (`figures v1`, after a calibration on ten papers each read twice): a paper is read
  once, and one paper in five twice, held to a pass line, so that drift shows.

Every reading is started before its reader runs, which records the version of its brief and lists; a reading with
fixed versions merges only rows read under one of them.

## What is checked, in code

Every row's fields and labels; that each figure it names and each number it gives are in the paper's text; that each
reason is the paper's own words or `not stated`; for a move, that its module is in the module list and that both its
quotes are in the paper's text, word for word; for code, that the file and line hold the call at that commit; for a
figure's panel, that its figure's legend is in the text, that its letters are the legend's, and that its quote is in
the text, with one conclusion for each figure, on its first row, found in the text; that no row comes from a source
on your exclusion list, if you keep one. The default Results reading preserves every raw revision, returns check
feedback for at most two retries, and excludes invalid secondary evidence without dropping an otherwise supported
claim. Unresolved failures stay visible. For a paper read twice under a research reading, the
agreement of the two readings against a pass line (for the figures panel by panel), and a list of where they differ.

## What it asks you first

- every download: the full text, the code;
- a batch of readers that would pass one and a half times its estimate: a batch is planned with its estimate (about
  0.1 million tokens of Sonnet a figures reading), and one within it runs, its cost recorded;
- a paper read twice that falls below the pass line, and any change to a brief or a list (a new version);
- a value under `other (...)` that recurs in three papers, as a candidate kind;
- every entry of judgement drafted from the rows.

## What it needs

Python 3.9 or later, with nothing beyond the standard library; the `pypdf` package to read a PDF; the internet, to
look papers up at NCBI and to fetch them; and, for the follow-up moves, the knowledge base's folder with its module
entries (`modules/*.md`).

## Where the entries go

The facts are written by `papers.py write-base` into the knowledge base's files, between the markers a file holds
(`<!-- from the papers: <part> -->` and `<!-- end of the part from the papers -->`), and nowhere else. Drafts stay in
the library's `drafts/` folder until you decide. Pertura's own knowledge base is changed by its
developers, with its tests. A folder of a user's own entries, read beside Pertura's base, is planned
(`docs/adr/0033-reading-papers-into-the-knowledge-base.md`, D7); until then, accepted drafts stay in the library.
