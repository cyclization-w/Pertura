# References, control guides and nulls

## R1. Calibrate before discovering

- **If you see:** a test about to be used on many targets, on a screen where it has not been checked.
- **Consider:** a fixed order:
  1. define the discoveries the test will be asked for;
  2. run the same test on control-vs-control comparisons (control guides taken as targets: `C.genes`' calibration
     check);
  3. if it calls them too often, change the covariates or the QC, and check again;
  4. only then run the discoveries.
- **It acts on:** the estimator and its QC, fixed before the results are seen.
- **Read it as:** in the source, a test's failure on control-vs-control pairs was traced to a cause: few cells
  expressing the gene, or a replicate confounded with guide frequency. The fix targeted that cause. Try a method for
  a calibration failure it addresses, not for its novelty.
- **Stop or turn when:** consider stopping inference with that test (the explanation weakened, for the route) when the
  calibration cannot be met, rather than looking for a test that comes out significant.
- **Source:** *Robust differential expression testing for single-cell CRISPR screens at low multiplicity of infection*,
  Genome Biol 2024, doi:10.1186/s13059-024-03254-2. Pertura's choice: inference waits for a calibrated test.

## R2. A null built from control guides

- **If you see:** effects of a target on genes other than its own, and a wish to call them false because they are
  not "on target".
- **Consider:** a null built from control guides against control guides, not from target-gene pairs assumed to be
  null. A downstream effect can be real.
- **It acts on:** what the calibration evidence is.
- **Read it as:** control-vs-control comparisons show the test's false calls on negative controls. Real targets'
  false calls behave the same only if the null is comparable (same sizes, units, selection), a further assumption.
  An off-target-looking effect is a hypothesis to test, not an error by definition. A value read against
  control-guide groups is an empirical p. With k of n groups as extreme, p = (k + 1) / (n + 1), at best 1 / (n + 1),
  as Phipson and Smyth 2010 give it for random permutations. Here n counts groups that share no control guide, not
  draws.
- **Stop or turn when:** consider another question when the question becomes direct versus indirect
  regulation. A mean expression change alone does not answer it.
- **Source:** *Robust differential expression testing for single-cell CRISPR screens at low
  multiplicity of infection*, Genome Biol 2024, doi:10.1186/s13059-024-03254-2; *Permutation P-values should never
  be zero*, Stat Appl Genet Mol Biol 2010, doi:10.2202/1544-6115.1585. Pertura's choice: n counts groups sharing no
  guide.

## R3. The shift every perturbation shares

- **If you see:** a target's effect on a gene that many other targets show too (a shared shift between perturbed and
  control cells: cutting, stress, selection).
- **Consider:** comparing the effect with what perturbations do to that gene in general (D.common_response: each
  target against the other targets' cells), and with a simple baseline such as the mean of all perturbed cells.
- **It acts on:** the interpretation and, if the claim is narrowed, the comparison.
- **Read it as:**
  - an effect that does not exceed the shared shift is not specific to the target;
  - a shared shift can be real biology, so it need not be noise either.

  In the source's evaluations, simple perturbed-mean baselines matched or beat complex models, because much of what
  the models captured was this shift.
- **Stop or turn when:** consider narrowing the claim to what is specific, or recording it as the shared response (the
  explanation weakened, for the specific explanation), when the claim only restates the shared shift.
- **Source:** *Systema: a framework for evaluating genetic perturbation response prediction
  beyond systematic variation*, Nat Biotechnol 2025, doi:10.1038/s41587-025-02777-8. Pertura's choice: a claim that
  restates the shift is narrowed.

## R4. Checking your own method on planted effects

- **If you see:** a new or custom analysis whose error rate you want to check before trusting it.
- **Consider:** planting known effects in real counts (binomial thinning of control cells) rather than simulating
  from a parametric model. Real counts keep the unwanted variation you worry about.
- **It acts on:** the check of the method, not the screen's analysis.
- **Read it as:** a check covers only the structures the planting kept (effect sizes, sparsity, confounding). Good
  results with random labels do not carry over to a confounded design. Thinning lowers counts, so it plants
  decreases directly. Increases need another construction.
- **Stop or turn when:** consider stopping when the planted scenarios fixed in advance have run. Do not keep adjusting
  until the real screen looks significant.
- **Source:** *Data-based RNA-seq simulations by binomial thinning*, BMC Bioinformatics 2020,
  doi:10.1186/s12859-020-3450-9. Pertura's choice: stop after the planned scenarios.

## R5. Explicit controls, not the rest of the panel

- **If you see:** a target judged against the other targets of a focused panel (its rank among them), not against
  control guides.
- **Consider:** explicit control guides as the reference: non-targeting guides, or intergenic guides for the
  cutting response.
- **It acts on:** the reference and the hypothesis.
- **Read it as:** against the rest of the panel, an effect means "different from these other targets". Changing the
  panel changes the answer. Against cutting controls, a knockout's effect excludes the response to cutting itself.
- **Stop or turn when:** consider saying which comparison is left (`cannot_answer` for the other) when the screen has no
  control guides of the needed kind.
- **Source:** *Framework for in vivo T cell screens*, J Exp Med 2024, doi:10.1084/jem.20230699. Pertura's choice:
  the missing comparison is `cannot_answer`.
