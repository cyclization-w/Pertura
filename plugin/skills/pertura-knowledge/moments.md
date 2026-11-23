# Moments in the tools that have knowledge

Some knowledge belongs to a moment of the work, not to a module's result. At that moment, the tool's reply names
these entries. Each entry gives the tool it applies at (and the kind of call, where the moment is one kind), what to
read, and why it is read there.

### M1. A finding that rests on a method of one's own

- **Applies at:** `run_code`
- **Read:** R4, PF18
- **The reason:** check a method of one's own on planted effects before trusting it. Do not tune it until something
  passes.

### M2. Every finding

- **Applies at:** `run_result`
- **Read:** PF1, PF16
- **The reason:** every finding states its n and its cells: which units are the replicates, and how many cells each
  side has.

### M3. A cell set

- **Applies at:** `pt.save_cells`
- **Read:** S2, PF17
- **The reason:** a cell set chosen by the perturbation's response (the perturbed cells, a cluster defined by that
  response) changes the population, so it answers another question. A cell type or a state chosen in advance, and
  fitted without the perturbation labels, does not.

### M4. Alternatives tried at a choice

- **Applies at:** `run_code`
- **Read:** HJ1, V5
- **The reason:** judge the alternatives by how the negative controls behave, never by which gives more hits. A
  thread has an end.

### M5. A thread ended

- **Applies at:** `pertura_status`
- **Read:** V5, HJ4
- **The reason:** a thread ends when it has said what it can, and it says so.

### M6. A proposal for validation

- **Applies at:** `write_report`
- **Read:** V2, V5
- **The reason:** a correlation nominates a candidate; the perturbation tests it.

### M7. A figure for the report

- **Applies at:** `write_report`
- **Read:** V3, FG1
- **The reason:** a clear separation after a supervised step is a display, not evidence. Each kind of evidence has
  the chart FG1 gives.
