"""The design auditor's code tools (PLAN.md Part 4): readers of a screen deposit and detectors of its design.

The design auditor is an agent: it reads the files, metadata and sample sheets, and asks the user what the data
cannot show. These readers and detectors are its tools, and they also check its work:
- readers.py: which files a deposit holds (10x matrices, guide calls, feature references, processed objects,
  archives), how they pair up, and their contents;
- detectors.py: design facts with a value, a status (observed, reported, inferred) and a source, and the questions
  the data leave open. Each detector targets a trap met in the atlas intakes: proteins and hashtags among the
  guides, guide names with separators or IDs, the library and its controls, MOI, units and pseudobulk reach, pooled
  donors, conditions, and the modality against on-target expression;
- audit.py: all detectors on one deposit, written as design facts in the pertura.design/v1 layout
  (pertura-skills/eval/*/design.yaml) with the open questions.
Nothing here decides a design: the gate (Part 4) asks the user to confirm.
"""
