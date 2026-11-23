---
description: The state of a Pertura analysis, or open one on a data folder of the analysis server
argument-hint: "[<data folder on the analysis server>]"
---

First load the Pertura skill with the Skill tool (`pertura:pertura`) if this session has not loaded it; its steps
govern everything below. The user's argument: "$ARGUMENTS".

- **Nothing, or "status":** call `pertura_status`, and show the analysis's state, in this shape:
  - the project and its data folder;
  - the design: confirmed or not; the screen object: built or building;
  - the digest, as `pertura_status` gives it: the decisions waiting on the user, the thread you are on and your
    reason, the changes since, by thread, and the open threads as the options next;
  - the findings, with whether their checks ran or disagree and the user's replies;
  - the open items (findings whose checks have not run or disagree, doubts not yet settled) and the check due (the
    skeptic).
- **A folder:** `open_project` with that folder, then continue from the stage it is at, as the skill's steps say.
- **No project yet:** ask where the data is (a folder on the analysis server).
