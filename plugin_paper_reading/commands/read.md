---
description: Read published screen papers into checked results, methods and figure evidence for the knowledge base
argument-hint: "<DOIs, PubMed or PMC IDs, or PDFs> [readings]"
---

First load the skill `pertura-reader:reading-papers` with the Skill tool, if this session has not loaded it; its steps
govern everything below. The user's argument: "$ARGUMENTS".

- **Papers named:** ask for the library's folder if no library is open in this session. Use the default Results
  reading unless the user names another research reading; then take each paper through the skill's steps, asking
  before each download and planning each batch of readers with its estimate, as the skill says.
- **Nothing:** say what the plugin does in two sentences, and ask which papers to read and into which library.
