"""The paper-reading plugin's scripts (docs/adr/0033, D3-D5; docs/build/reading_papers/the_procedure.md), on a small
made-up paper, knowledge base and repository, with no network: registering against an exclusion list, cutting a JATS
text, the module list from the knowledge base's entries, the checks of each reading's rows (the moves reading's
fields, units, modules, relations and quotes; the figures reading's lists, panels, quotes and conclusions, each check
shown to fail), the matching of two pipelines' rows and the pass line, the agreement of two figure readings panel by
panel, the review page of a calibration batch, merging and counting, the check of a draft's sources, unpacking code
safely, and the scripts' labels against the briefs; readings started under a fixed version and merged only under one,
the figures' pass line, batches with their drift check and cost, and the parts of the knowledge base written from the
catalogue."""
import contextlib
import hashlib
import html
import importlib.util
import io
import json
import os
import re
import shutil
import tempfile
import time
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(ROOT, "plugin_paper_reading")
_spec = importlib.util.spec_from_file_location(
    "papers", os.path.join(PLUGIN, "skills", "reading-papers", "scripts", "papers.py"))
papers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(papers)

LABEL = "PMID123 Zhao 2026"
JATS = """<article><body>
<sec><title>Introduction</title><p>Screens map regulators.</p></sec>
<sec><title>Results</title>
<p>Knockout of GENE1 changed 120 genes against the control guides (Fig. 2a), with a correlation of 0.31 between
replicates (Fig. S3b).</p>
<p>We focused on GENE1 because "it had the most changed genes" of all targets (Fig. 2c).</p>
<p>Cells with guides against GENE2 shifted into the cycling state compared with control cells of the same donor
(Extended Data Fig. 3b), in 4 of 5 donors.</p>
<p>Targets were clustered by the correlation of their profiles, and GENE1 grouped with GENE3 in one module of
co-regulated genes (Fig. 4c).</p>
<fig><label>Figure 2</label><caption><p>Effects of GENE1 knockout on 120 genes.</p></caption></fig>
</sec>
<sec><title>Discussion</title><p>GENE1 matters.</p></sec>
<sec><title>Methods</title>
<sec><title>Differential expression</title><p>Pseudobulk by donor, tested with edgeR at FDR 0.05.</p></sec>
</sec>
<sec><title>Code availability</title><p>Code is at https://github.com/lab/screen-code.</p></sec>
</body></article>
"""
KEPT_OUT = {"series": {"ids": ["GSE111111"]},
            "papers": {"dois": ["10.1000/kept.out"], "ids": ["PMC999", "999"], "names": ["Keptout"],
                       "names_with_years": [["Leftout", 2026]]}}
QUOTE_WORDS_TEXT = f"{papers.QUOTE_WORDS[0]} to {papers.QUOTE_WORDS[1]} words"
QUOTE_A = "Knockout of GENE1 changed 120 genes against the control guides"
QUOTE_B = "Cells with guides against GENE2 shifted into the cycling state compared with control cells of the same donor"
QUOTE_C = "Targets were clustered by the correlation of their profiles, and GENE1 grouped with GENE3"
MODULES = {
    "C.genes": "# C.genes: The genes each target changes\n\n**The biology.** The genes each target changes, against\n"
               "the control guides.\n\n**The measurement.** Per target, the changed genes (q < 0.05).\n\n"
               "## Ways the result can mislead\n\n### a shared response\n\n- **Read:** R3\n",
    "C.states_and_composition": "# C.states_and_composition: Cell states, and each target's share of them\n\n"
                                "**The biology.** Cell states and the share of each target's cells in them.\n\n"
                                "**The measurement.** Per target and state, the share against the controls'.\n\n"
                                "## Choices\n\n- propeller by sample\n- scCODA\n\n## Next steps\n\n- C3.\n",
    "E.similar_targets": "# E.similar_targets: Targets with similar profiles\n\n**The biology.** Targets whose "
                         "profiles correlate.\n\n**The measurement.** The correlation of every two targets' profiles."
                         "\n\n**The choices.** the top genes; all genes.\n",
    "B.escape": "# B.escape: Perturbed cells and the cells that escaped\n\nNo parts of the usual form.\n"}
DESIGN_LINES = "\n".join([
    "| modality | CRISPR knockout | not stated |",
    "| system | primary cells | not stated |",
    "| guides per cell | one | not stated |",
    "| conditions | none | not stated |",
    "| readout | transcriptome | not stated |",
    "| replicates | biological (donors, animals or separate cultures) | Pseudobulk by donor, tested with edgeR at FDR "
    "0.05 |"]) + "\n"


def move(paper=LABEL, panel="Fig. 2a", trigger="Targets profiled", question="Which genes does GENE1 change?",
         cells="cells with a guide against GENE1", compared_with="control guides", unit="cells",
         method="differential expression with edgeR", readout="each gene's expression", showed="120 genes changed",
         kind="modules or programmes", module="C.genes", relation="default", decided_by="not stated",
         evidence=QUOTE_A, result_evidence=QUOTE_A):
    """A moves row of the current version, its fields as the reader's brief orders them."""
    return "| " + " | ".join((paper, panel, trigger, question, cells, compared_with, unit, method, readout, showed,
                              kind, module, relation, decided_by, evidence, result_evidence)) + " |"


GOOD = move()
FIRST_VERSION = (f"| {LABEL} | Fig. 2a | Targets profiled | Which genes does GENE1 change? | DE against control guides "
                 f"(cells) | 120 genes changed | modules or programmes | not stated |")
MOVE_B = move(panel="Extended Data 3b", trigger="GENE1's changed genes",
              question="Does GENE2 shift cells between states?",
              cells="cells with guides against GENE2", compared_with="control cells of the same donor",
              method="the share of cells per state, by donor", readout="the share of cells in the cycling state",
              showed="in 4 of 5 donors", kind="states or composition", module="C.states_and_composition",
              evidence=QUOTE_B, result_evidence=QUOTE_B)
MOVE_C = move(panel="Fig. 4c", trigger="Many targets changed genes", question="Which targets act together?",
              cells="all targets", compared_with="the other targets", unit="targets",
              method="clustering by correlation of profiles", readout="clusters of targets", showed="GENE1 with GENE3",
              kind="similar targets or clusters", module="E.similar_targets", evidence=QUOTE_C, result_evidence=QUOTE_C)
# a second pipeline's verified rows: moves A and B in its own words, B with another module and relation, and a row
# whose unit is not one of the list
MOVES_2 = [move(panel="Fig. 2a-b", question="Which genes change when GENE1 is knocked out?",
                cells="GENE1 knockout cells", method="edgeR test against control cells",
                readout="differential expression"),
           MOVE_B.replace("Extended Data 3b", "Extended Data Fig. 3b").replace(
               "C.states_and_composition | default", "E.similar_targets | question"),
           move(unit="people")]


FIGURES_JATS = (
    "<article><body>\n<sec><title>Results</title>\n"
    "<p>We screened 20 regulators in primary T cells from four donors with a pooled knockout library (Fig. 1a).</p>\n"
    "<p>Knockout of GENE1 lowered its own transcript in the cells that carry its guides, in all four donors "
    "(Fig. 1b, c).</p>\n"
    "<p>The screen captured a median of 200 cells per guide across the library (Extended Data Fig. 3a).</p>\n"
    "<fig><label>Figure 1</label><caption><title>GENE1 knockout lowers its own transcript.</title><p>(A) Schematic of "
    "the pooled screen in primary T cells from four donors. (B and C) Boxplots of GENE1 expression in cells with "
    "guides against GENE1 and in control cells, n = 4 donors. (D) Heatmap of the correlation of every two guides' "
    "effects."
    "</p></caption></fig>\n"
    "<fig><label>Figure 2</label><caption><p>Cells lacking GENE1 secrete less interferon gamma in an arrayed test of "
    "three donors.</p></caption></fig>\n"
    "<fig><label>Extended Data Fig. 3</label><caption><p>Quality of the screen. a) Cells per guide across the library "
    "of the screen. b) UMAP of all cells coloured by donor and replicate.</p></caption></fig>\n"
    "</sec>\n<sec><title>Methods</title><sec><title>Guide assignment</title><p>Guides were assigned with a mixture "
    "model.</p></sec></sec>\n</body></article>\n")
QUOTE_1A = "Schematic of the pooled screen in primary T cells from four donors."
QUOTE_1BC = "Boxplots of GENE1 expression in cells with guides against GENE1 and in control cells"
QUOTE_2 = "Cells lacking GENE1 secrete less interferon gamma in an arrayed test of three donors."
QUOTE_3A = "Cells per guide across the library of the screen."
QUOTE_3B = "UMAP of all cells coloured by donor and replicate."
CONCLUSION_1 = "GENE1 knockout lowers its own transcript."
CONCLUSION_2 = "Cells lacking GENE1 secrete less interferon gamma"
CONCLUSION_3 = "The screen captured a median of 200 cells per guide"  # from the Results: the title names a subject


def figure_row(figure="Fig. 1", panel="a", conclusion="-", evidence="the design", source="none", chart="schematic",
               readout="the screen's design", compared_with="nothing", n_stated="not stated", quote=QUOTE_1A,
               paper=LABEL, marks="none", targets_shown="no target", colour="one colour",
               colour_named_by="not applicable", image="none"):
    """A figures row of the current version, its fields as the figure reader's brief orders them."""
    return "| " + " | ".join((paper, figure, panel, conclusion, evidence, source, chart, marks, targets_shown, colour,
                              colour_named_by, image, readout, compared_with, n_stated, quote)) + " |"


def version_1(row):
    """A figures row of version 1's eleven fields: the current row without the five read from the images."""
    cells = papers.table_rows(row)[0]
    return "| " + " | ".join(cells[:7] + cells[12:]) + " |"


FIGURE_ROWS = [
    figure_row(conclusion=CONCLUSION_1),
    figure_row(panel="b-c", evidence="a target's own gene", source="the screen", chart="box or violin",
               readout="GENE1 expression per cell", compared_with="control cells", n_stated="n = 4 donors",
               quote=QUOTE_1BC),
    figure_row(figure="Fig. 2", panel="all", conclusion=CONCLUSION_2, evidence="a phenotype",
               source="a follow-up experiment", chart="not stated", readout="interferon gamma secreted",
               compared_with="control cells", quote=QUOTE_2),
    figure_row(figure="Extended Data Fig. 3", conclusion=CONCLUSION_3, evidence="cells, guides and quality",
               source="the screen", chart="not stated", readout="cells per guide", quote=QUOTE_3A),
    figure_row(figure="Extended Data Fig. 3", panel="b", evidence="cells, guides and quality", source="the screen",
               chart="embedding", readout="cells coloured by donor", quote=QUOTE_3B)]


def first_version(row, kind):
    """A figures row of the pilot's ten fields: the evidence and the source as one kind."""
    cells = papers.table_rows(version_1(row))[0]
    return "| " + " | ".join(cells[:4] + [kind] + cells[6:]) + " |"


def second_figure_reading():
    """Fig. 1b and c as two rows, b's evidence and c's chart given differently; Fig. 2's row failing its quote;
    Extended Data Fig. 3b's source given differently, and its conclusion taken from the legend's title."""
    return [figure_row(conclusion="GENE1 knockout lowers its own transcript"),
            figure_row(panel="b", evidence="a program or protein per cell", source="the screen",
                       chart="box or violin", quote=QUOTE_1BC),
            figure_row(panel="c", evidence="a target's own gene", source="the screen",
                       chart="box or violin with points", quote=QUOTE_1BC),
            FIGURE_ROWS[2].replace("an arrayed test", "a pooled test"),
            figure_row(figure="Extended Data Fig. 3", conclusion="Quality of the screen.",
                       evidence="cells, guides and quality", source="the screen", chart="not stated",
                       quote=QUOTE_3A),
            figure_row(figure="Extended Data Fig. 3", panel="b", evidence="cells, guides and quality",
                       source="the paper's other data", chart="embedding", quote=QUOTE_3B)]


# the review page's paper: Fig. 2's legend holds markup and an ampersand, which the page must show as text
QUOTE_2_MARKUP = ("Cells lacking GENE1 secrete less interferon gamma in an arrayed test of three donors "
                  "<script>x</script> & more.")
REVIEW_JATS = FIGURES_JATS.replace("in an arrayed test of three donors.</p>",
                                   "in an arrayed test of three donors &lt;script&gt;x&lt;/script&gt; &amp; more.</p>")


def review_reading_1():
    """The first reading of the review page's paper: FIGURE_ROWS, Fig. 2's quote holding its legend's markup, and
    Extended Data Fig. 3b's evidence under other (...)."""
    rows = [row.replace(QUOTE_2, QUOTE_2_MARKUP) for row in FIGURE_ROWS]
    rows[4] = rows[4].replace("| cells, guides and quality | the screen | embedding",
                              "| other (donor effects) | the screen | embedding")
    return rows


def review_reading_2():
    return second_figure_reading()


def section(page, name):
    """One section of a review page, by its id."""
    return re.search(rf'<(section|footer) id="{name}">(.*?)</\1>', page, re.S).group(2)


def summary(pmid, author, year, doi="", pmc=""):
    ids = [{"idtype": "doi", "value": doi}] if doi else []
    ids += [{"idtype": "pmc", "value": pmc}] if pmc else []
    return {"result": {pmid: {"authors": [{"name": author}], "pubdate": f"{year} Jan 5", "articleids": ids}}}


def write_knowledge_base(folder, modules=MODULES):
    for name, text in modules.items():
        papers.write_text(os.path.join(folder, "modules", f"{name}.md"), text)
    papers.write_text(os.path.join(folder, "modules", "notes.txt"), "not an entry\n")


class Library(unittest.TestCase):
    """A library in a temporary folder, with a knowledge base of four modules and the network replaced by canned
    replies."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lib = os.path.join(self.tmp.name, "library")
        self.kept = os.path.join(self.tmp.name, "kept_out.json")
        self.kb = os.path.join(self.tmp.name, "knowledge")
        with open(self.kept, "w", encoding="utf-8") as fh:
            json.dump(KEPT_OUT, fh)
        write_knowledge_base(self.kb)
        papers.init(self.lib, self.kept, self.kb)
        self.replies = {"123": summary("123", "Zhao Y", 2026, "10.1000/zhao", "PMC123"),
                        "456": summary("456", "Leftout X", 2026, "10.1000/left"),
                        "789": summary("789", "Smith J", 2025, "10.1000/kept.out")}
        self._eutils = papers.eutils
        papers.eutils = lambda what, **p: self.replies.get(p.get("id"), {})

    def tearDown(self):
        papers.eutils = self._eutils
        self.tmp.cleanup()

    def cut_paper(self, jats=JATS):
        papers.register(self.lib, ["123"])
        folder = os.path.join(self.lib, "sources", "pmid123")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "fulltext.xml"), "w", encoding="utf-8") as fh:
            fh.write(jats)
        return papers.cut(self.lib, "pmid123")

    def write_rows(self, reading, text):
        papers.write_text(os.path.join(self.lib, "rows", "pmid123", f"{reading}.md"), text)

    def write_pipeline(self, p, name, text):
        papers.write_text(os.path.join(self.lib, "rows", "pmid123", f"pipe_{p}", name), text)


class TestRegistering(Library):
    def test_a_paper_gets_its_key_label_and_open_text(self):
        out = papers.register(self.lib, ["123"])
        self.assertIn("pmid123: PMID123 Zhao 2026, registered; open full text at Europe PMC", out[0])
        row = papers.paper(papers.papers(self.lib), "pmid123")
        self.assertEqual((row["doi"], row["pmcid"], row["state"]), ("10.1000/zhao", "PMC123", "registered"))
        self.assertIn("PMC123/fullTextXML", row["text_url"])
        self.assertIn("registered before", papers.register(self.lib, ["123"])[0])

    def test_a_paper_on_the_exclusion_list_goes_no_further(self):
        out = papers.register(self.lib, ["456", "789"])
        self.assertIn("kept out: Leftout 2026 is on the exclusion list", out[0])
        self.assertIn("kept out: its DOI 10.1000/kept.out is on the exclusion list", out[1])
        row = papers.paper(papers.papers(self.lib), "pmid456")
        self.assertEqual(row["state"], "kept out")
        with self.assertRaises(papers.LibraryError):
            papers.fetch(self.lib, ["pmid456"])

    def test_a_pdf_is_registered_under_its_label(self):
        pdf = os.path.join(self.tmp.name, "paper.pdf")
        with open(pdf, "wb") as fh:
            fh.write(b"%PDF-1.4")
        out = papers.register(self.lib, [], pdf=pdf, label="Lee 2025")
        self.assertIn("pdf-lee-2025: Lee 2025, its PDF is in the library", out[0])
        self.assertTrue(os.path.exists(os.path.join(self.lib, "sources", "pdf-lee-2025", "paper.pdf")))
        self.assertIn("kept out", papers.register(self.lib, [], pdf=pdf, label="Keptout 2025")[0])

    def test_a_library_is_started_once(self):
        with self.assertRaises(papers.LibraryError):
            papers.init(self.lib)


class TestCutting(Library):
    def test_the_results_legends_methods_and_availability_are_cut_apart(self):
        out = self.cut_paper()
        self.assertIn("code named: https://github.com/lab/screen-code", out)
        texts = os.path.join(self.lib, "texts", "pmid123")
        results = papers.read_text(os.path.join(texts, "results.txt"))
        self.assertIn("changed 120 genes", results)
        self.assertIn("Figure 2. Effects of GENE1", results)
        self.assertNotIn("Screens map regulators", results)
        self.assertNotIn("GENE1 matters", results)
        self.assertIn("edgeR at FDR 0.05", papers.read_text(os.path.join(texts, "methods.txt")))
        self.assertIn("github.com/lab/screen-code", papers.read_text(os.path.join(texts, "availability.txt")))
        self.assertEqual(papers.fetch_list(self.lib),
                         ["pmid123 (PMID123 Zhao 2026): its main figures' images, from PMC's open data "
                          f"({papers.OPEN_DATA}), with --figure-images",
                          "pmid123 (PMID123 Zhao 2026): code named in the paper: https://github.com/lab/screen-code"])

    def test_a_text_that_names_a_listed_series_yields_no_text(self):
        out = self.cut_paper(JATS.replace("Screens map regulators.", "Data of GSE111111 were reused."))
        self.assertIn("kept out, its text names GSE111111", out)
        self.assertFalse(os.path.exists(os.path.join(self.lib, "texts", "pmid123")))
        self.assertEqual(papers.paper(papers.papers(self.lib), "pmid123")["state"], "kept out")


class TestTheModuleList(Library):
    def test_each_module_is_listed_with_what_it_is_for_what_it_returns_and_its_choices_where_given(self):
        out = papers.write_module_list(self.lib)
        path = os.path.join(self.lib, "module_list.md")
        text = papers.read_text(path)
        self.assertIn(f"4 modules in {path}; sha256 {hashlib.sha256(text.encode('utf-8')).hexdigest()}", out)
        self.assertEqual(papers.module_names(self.lib),
                         ["B.escape", "C.genes", "C.states_and_composition", "E.similar_targets"])
        genes = text.split("## C.genes\n")[1].split("\n## ")[0]
        self.assertIn("- what it is for: The genes each target changes, against the control guides.", genes)
        self.assertIn("- what it returns: Per target, the changed genes (q < 0.05).", genes)
        self.assertNotIn("choices", genes)
        self.assertNotIn("a shared response", genes)
        self.assertIn("- choices: propeller by sample; scCODA", text)
        self.assertIn("- choices: the top genes; all genes.", text)
        escape = text.split("## B.escape\n")[1].split("\n## ")[0]
        self.assertIn("- what it is for: Perturbed cells and the cells that escaped", escape)
        self.assertIn("- what it returns: not given in the entry", escape)
        self.assertEqual(papers.write_module_list(self.lib), out)  # the same entries, the same list, no warning

    def test_a_changed_knowledge_base_gives_a_changed_list_and_says_so(self):
        papers.write_module_list(self.lib)
        write_knowledge_base(self.kb, {"G.meaning": "# G.meaning: The meaning of the changed genes\n"})
        self.assertIn("the list changed", papers.write_module_list(self.lib))
        self.assertIn("G.meaning", papers.module_names(self.lib))

    def test_the_list_needs_a_knowledge_base_with_module_entries(self):
        other = os.path.join(self.tmp.name, "other")
        papers.init(other)
        with self.assertRaises(papers.LibraryError):
            papers.write_module_list(other)
        with self.assertRaises(papers.LibraryError):
            papers.module_names(other)
        self.assertIn("4 modules", papers.write_module_list(other, self.kb))
        with self.assertRaises(papers.LibraryError):
            papers.write_module_list(other, self.tmp.name)  # a folder without modules/
        write_knowledge_base(self.kb, {"C.genes_again": "# C.genes: The genes each target changes\n"})
        with self.assertRaises(papers.LibraryError):
            papers.write_module_list(other, self.kb)  # two entries give one name


class TestChecking(Library):
    def setUp(self):
        super().setUp()
        self.cut_paper()
        papers.write_module_list(self.lib)

    def report(self, reading, pipeline=None):
        papers.check(self.lib, "pmid123", pipeline)
        with open(os.path.join(self.lib, "checks", "pmid123.json"), encoding="utf-8") as fh:
            return json.load(fh)[reading]

    def test_a_move_row_passes_when_the_text_holds_it_and_fails_when_it_does_not(self):
        self.write_rows("moves", "\n".join([
            GOOD,
            GOOD.replace("modules or programmes", "a new kind"),
            GOOD.replace(LABEL, "PMID124 Zhao 2026"),
            GOOD.replace("Fig. 2a", "Fig. 7b"),
            GOOD.replace("120 genes changed", "340 genes changed"),
            GOOD.replace("not stated |", 'the authors said "it was the most interesting one" |'),
            GOOD.replace("not stated |", 'because "it had the most changed genes" |'),
            GOOD.replace("Targets profiled", "as in Leftout 2026"),
        ]) + "\n")
        rows = self.report("moves")["rows"]
        self.assertEqual((rows[0]["fails"], rows[0]["looks"]), ([], []))
        self.assertIn("is not one of the list", rows[1]["fails"][0])
        self.assertIn("not 'PMID123 Zhao 2026'", rows[2]["fails"][0])
        self.assertIn("no figure of 'Fig. 7b' is in the text", rows[3]["fails"])
        self.assertEqual(rows[4]["fails"], [])
        self.assertIn("numbers of showed not in the text: 340", rows[4]["looks"])
        self.assertIn("quote not in the text", rows[5]["looks"][0])
        self.assertEqual(rows[6]["looks"], [])
        self.assertIn("cites sources kept out: Leftout 2026", rows[7]["fails"][0])
        self.assertEqual(papers.paper(papers.papers(self.lib), "pmid123")["state"], "checked")

    def test_a_move_row_gives_a_listed_unit_a_listed_module_its_relation_and_two_quotes_of_the_text(self):
        self.write_rows("moves", "\n".join([
            move(unit="people"),
            move(module="Z.nothing"),
            move(relation="sideways"),
            move(module="none"),
            move(relation="none"),
            move(module="none", relation="none"),
            move(evidence="GENE1 changed genes"),
            move(result_evidence="Knockout of GENE1 changed 120 genes ... against the control guides"),
            move(evidence="Knockout of GENE1 changed 120 genes against the treated guides"),
            move(evidence=QUOTE_A.upper()),
            "| " + " | ".join(["x"] * 12) + " |",
        ]) + "\n")
        rows = self.report("moves")["rows"]
        self.assertEqual(rows[0]["fails"], ["unit 'people' is not one of the list"])
        self.assertEqual(rows[1]["fails"], ["module 'Z.nothing' is not in the module list"])
        self.assertEqual(rows[2]["fails"], ["relation 'sideways' is not one of the list"])
        self.assertIn("the relation is none exactly when the module is", rows[3]["fails"][0])
        self.assertIn("the relation is none exactly when the module is", rows[4]["fails"][0])
        self.assertEqual(rows[5]["fails"], [])
        self.assertEqual(rows[6]["fails"], ["evidence has 3 words, not 8 to 40"])
        self.assertEqual(rows[7]["fails"], ["result_evidence holds an ellipsis"])
        self.assertEqual(rows[8]["fails"], ["evidence is not in the text"])
        self.assertEqual(rows[9]["fails"], [])  # found after the normalisation, which ignores case
        self.assertEqual(rows[10]["fails"], ["12 fields, not 16"])

    def test_a_row_of_the_first_version_fails_with_its_reason(self):
        self.write_rows("moves", FIRST_VERSION + "\n")
        rows = self.report("moves")["rows"]
        self.assertEqual(rows[0]["fails"], [papers.FIRST_VERSION_ROW.format(8, 16)])
        # outside a library's check, a first-version row is checked by the first version's checks
        (_, fails, _), = papers.check_rows("moves", papers.source_text(self.lib, "pmid123"), LABEL,
                                           [papers.table_rows(FIRST_VERSION)[0]], papers.KeptOut())
        self.assertEqual(fails, [])

    def test_the_design_gives_six_listed_features_each_with_its_evidence(self):
        self.write_rows("moves", GOOD + "\n")
        self.write_rows("design", DESIGN_LINES)
        self.assertEqual(self.report("moves")["design"]["fails"], [])
        self.write_rows("design", DESIGN_LINES.replace("primary cells", "yeast").replace(
            "| conditions | none | not stated |\n", "").replace("tested with edgeR", "tested with DESeq2"))
        design = self.report("moves")["design"]
        self.assertEqual(design["values"]["system"], "yeast")
        self.assertEqual(design["fails"], ["system: 'yeast' is not one of its list",
                                           "replicates: the evidence is not in the text", "no line for conditions"])

    def test_a_pipeline_s_verified_rows_are_checked_as_the_paper_s_moves(self):
        self.write_pipeline(1, "moves.md", GOOD + "\n" + move(unit="people") + "\n")
        self.write_pipeline(1, "verified.md", GOOD + "\n")
        self.write_pipeline(1, "design.md", DESIGN_LINES)
        with self.assertRaises(papers.LibraryError) as e:
            papers.check(self.lib, "pmid123")  # no rows of the paper's own, and no pipeline named
        self.assertIn("name the pipeline whose verified rows are the paper's with --pipeline", str(e.exception))
        with self.assertRaises(papers.LibraryError):
            papers.check(self.lib, "pmid123", 2)
        rep = self.report("moves", 1)
        self.assertEqual(rep["file"], "rows/pmid123/pipe_1/verified.md")
        self.assertEqual([x["fails"] for x in rep["rows"]], [[]])
        self.assertEqual(rep["design"]["fails"], [])
        self.assertEqual(rep["module_list_sha256"], hashlib.sha256(papers.read_text(
            os.path.join(self.lib, "module_list.md")).encode("utf-8")).hexdigest())
        self.assertIn("Fig. 2a", "\n".join(papers.show(self.lib, "pmid123", 1)))

    def test_a_chain_needs_its_fields_numbered_steps_and_known_data(self):
        self.write_rows("chain", "\n".join([
            f"- **paper:** {LABEL}",
            "- **question:** Which regulators shape the response?",
            "- **design:** in vitro CRISPRi of 20 targets; not stated for units",
            "| 1 | the screen | which genes? | DE (cells) | screen data | 120 genes | Fig. 2a | |",
            "| 3 | GENE1 led | why? | correlation | made-up data | 0.31 | Fig. S3b | composed |"]) + "\n")
        rep = self.report("chain")
        self.assertIn("no depth field", rep["whole"])
        self.assertIn("the steps are not numbered 1, 2, 3 in order", rep["whole"])
        self.assertEqual(rep["rows"][0]["fails"], [])
        self.assertIn("data 'made-up data' is not one of the list", rep["rows"][1]["fails"])

    def test_a_methods_row_names_a_listed_step_and_a_heading_of_the_text(self):
        self.write_rows("methods", "\n".join([
            f"| {LABEL} | the test of perturbation effects | used | pseudobulk by donor | edgeR | FDR 0.05 | "
            f"not stated | Differential expression |",
            f"| {LABEL} | normalisation | maybe | log1p | scanpy | not stated | not stated | Normalisation |"]) + "\n")
        rows = self.report("methods")["rows"]
        self.assertEqual((rows[0]["fails"], rows[0]["looks"]), ([], []))
        self.assertIn("as is 'maybe', not 'used' or 'also tried'", rows[1]["fails"])
        self.assertIn("sections not in the text: Normalisation", rows[1]["looks"])

    def test_a_code_row_is_the_call_at_its_file_and_line_of_the_fetched_commit(self):
        rows = papers.papers(self.lib)
        r = papers.paper(rows, "pmid123")
        r["code_url"], r["code_commit"] = "https://github.com/lab/screen-code", "abc123"
        papers.save_papers(self.lib, rows)
        repo = os.path.join(self.lib, "sources", "pmid123", "code", "screen-code-abc123")
        papers.write_text(os.path.join(repo, "analysis", "de.py"),
                          "import scanpy as sc\n\nsc.tl.rank_genes_groups(adata, 'target',\n    method='wilcoxon')\n")
        self.write_rows("code", "\n".join([
            "- **repository:** https://github.com/lab/screen-code",
            "- **commit:** abc123",
            f"| {LABEL} | the test of perturbation effects | analysis/de.py | 3 | "
            f"sc.tl.rank_genes_groups(adata, 'target', | method='wilcoxon' |",
            f"| {LABEL} | the test of perturbation effects | analysis/de.py | 1 | sc.tl.rank_genes_groups( | |",
            f"| {LABEL} | normalisation | analysis/norm.py | 4 | sc.pp.normalize_total(adata) | |",
            f"| {LABEL} | normalisation | ../outside.py | 1 | import os | |",
            f"| {LABEL} | normalisation | analysis/de.py | 1 | import scanpy as sc \\|\\| x | |"]) + "\n")
        rep = self.report("code")
        self.assertEqual(rep["whole"], [])
        self.assertEqual(rep["rows"][0]["fails"], [])
        self.assertIn("the call is not at analysis/de.py:1", rep["rows"][1]["fails"])
        self.assertIn("analysis/norm.py:4 is not in the repository", rep["rows"][2]["fails"])
        self.assertIn("../outside.py:1 is not in the repository", rep["rows"][3]["fails"])
        self.assertEqual(rep["rows"][4]["row"][4], "import scanpy as sc || x")  # a cell's own pipes, unescaped
        self.assertIn("the call is not at analysis/de.py:1", rep["rows"][4]["fails"])


class TestMergingAndCounting(Library):
    def setUp(self):
        super().setUp()
        self.cut_paper()
        papers.write_module_list(self.lib)

    def test_only_rows_that_passed_enter_the_catalogue_and_counts_are_by_papers(self):
        self.write_rows("moves", "\n".join([GOOD, GOOD.replace("modules or programmes", "a new kind"), MOVE_C,
                                            move(module="none", relation="none", kind="other (a tool's check)")]))
        papers.check(self.lib, "pmid123")
        self.assertEqual(papers.merge(self.lib), ["pmid123 moves: 3 of 4 rows merged"])
        moves = papers.catalogue(self.lib, "moves")
        self.assertEqual([m["module"] for m in moves], ["C.genes", "E.similar_targets", "none"])
        self.assertEqual(moves[0]["result_evidence"], QUOTE_A)
        out = papers.count(self.lib, "moves")  # by module, unless another field is named
        self.assertEqual(out[0], "| module | papers | rows |")
        self.assertIn("| C.genes | 1 | 1 |", out)
        self.assertIn("| none | 1 | 1 |", out)
        self.assertEqual(out[-1], "1 papers in all")
        out = papers.count(self.lib, "moves", ["kind"])
        self.assertIn("| modules or programmes | 1 | 1 |", out)
        self.assertIn("| other | 1 | 1 |", out)
        self.assertIn("| E.similar_targets | default | 1 | 1 |",
                      papers.count(self.lib, "moves", ["module", "relation"]))
        with self.assertRaises(papers.LibraryError):
            papers.count(self.lib, "chain")  # no default field for the other readings

    def test_rows_changed_after_their_check_are_not_merged(self):
        self.write_rows("moves", GOOD + "\n")
        papers.check(self.lib, "pmid123")
        self.write_rows("moves", GOOD.replace("120 genes changed", "121 genes changed") + "\n")
        self.assertIn("its rows changed after the check", papers.merge(self.lib)[0])

    def test_a_pipeline_s_rows_merge_from_their_own_file(self):
        self.write_pipeline(1, "verified.md", GOOD + "\n")
        papers.check(self.lib, "pmid123", 1)
        self.assertEqual(papers.merge(self.lib), ["pmid123 moves: 1 of 1 rows merged"])
        self.write_pipeline(1, "verified.md", MOVE_C + "\n")
        self.assertIn("its rows changed after the check", papers.merge(self.lib)[0])

    def test_rows_checked_as_another_version_of_the_reading_are_not_merged(self):
        self.write_rows("moves", GOOD + "\n")
        papers.check(self.lib, "pmid123")
        path = os.path.join(self.lib, "checks", "pmid123.json")
        with open(path, encoding="utf-8") as fh:
            report = json.load(fh)
        report["moves"]["rows"] = [{"row": papers.table_rows(FIRST_VERSION)[0], "fails": [], "looks": []}]
        papers.write_text(path, json.dumps(report))
        self.assertEqual(papers.merge(self.lib),
                         ["pmid123 moves: left out, checked as another version of the reading; check it again"])


class TestTwoPipelines(Library):
    def setUp(self):
        super().setUp()
        self.cut_paper()
        papers.write_module_list(self.lib)

    def pipelines(self, verified_2):
        """Pipeline 1 finds moves A, B and C; pipeline 2 finds A and B in its own words, and whatever it is given."""
        self.write_pipeline(1, "moves.md", "\n".join([GOOD, MOVE_B, MOVE_C, move(panel="Fig. 2c")]) + "\n")
        self.write_pipeline(1, "verdicts.md", "\n".join([
            f"| 1 | accept | none | as the text says | {QUOTE_A} |",
            f"| 2 | fix | showed | the donors | {QUOTE_B} |",
            f"| 3 | accept | none | as the text says | {QUOTE_C} |",
            "| 4 | delete | none | repeats row 1 | not in the text |",
            "missed: Fig. 5a, the combination of GENE1 and GENE3"]) + "\n")
        self.write_pipeline(1, "verified.md", "\n".join([GOOD, MOVE_B, MOVE_C]) + "\n")
        self.write_pipeline(2, "moves.md", "\n".join(verified_2) + "\n")
        self.write_pipeline(2, "verdicts.md", "\n".join(f"| {i} | accept | none | fits | {QUOTE_A} |"
                                                        for i in range(1, len(verified_2) + 1)) + "\n")
        self.write_pipeline(2, "verified.md", "\n".join(verified_2) + "\n")
        for p in (1, 2):
            self.write_pipeline(p, "design.md", DESIGN_LINES)

    def test_two_pipelines_are_matched_measured_and_held_to_the_pass_line(self):
        self.pipelines(MOVES_2)
        out = papers.agree(self.lib, "pmid123")
        with open(os.path.join(self.lib, "checks", "pmid123.agreement.json"), encoding="utf-8") as fh:
            rep = json.load(fh)
        m = rep["measures"]
        self.assertEqual((m["quote_check"], m["row_agreement"], m["module_agreement"]), (1.0, 0.8, 0.5))
        self.assertEqual((m["relation_agreement"], m["kind_agreement"], m["kind_agreement_as_written"]),
                         (0.5, 1.0, 1.0))
        self.assertEqual(m["items_by_support"], {"1": 1, "2": 2})
        self.assertEqual(m["pass"], {"quote_check": True, "row_agreement": True, "module_agreement": False})
        self.assertFalse(m["passes"])
        self.assertEqual(m["design_agreement"], 6)
        self.assertEqual(m["before_verification"]["items_by_support"], {"1": 2, "2": 2})
        self.assertEqual(rep["pipelines"]["1"]["verdicts"], {"accept": 2, "fix": 1, "delete": 1})
        self.assertEqual(rep["pipelines"]["1"]["verdict_notes"], [])
        self.assertIn("  module agreement 0.50 (line 0.75): does not pass", out)
        self.assertIn("  the paper does not pass: module agreement", out)
        review = "\n".join(out)
        self.assertIn("one pipeline | pipe 1 row 3 | Fig. 4c | Which targets act together?", review)
        self.assertIn("modules differ: C.states_and_composition; E.similar_targets | pipe 1 row 2, pipe 2 row 2",
                      review)
        self.assertIn("failed check | pipe 2 row 3 | Fig. 2a | unit 'people' is not one of the list", review)
        self.assertIn("verifier 1 names a missed move | Fig. 5a, the combination of GENE1 and GENE3", review)

    def test_a_paper_whose_pipelines_agree_passes(self):
        self.pipelines([GOOD, MOVE_B, MOVE_C])
        out = papers.agree(self.lib, "pmid123")
        self.assertIn("  the paper passes", out)
        self.assertIn("  module agreement 1.00 (line 0.75): passes", out)

    def test_the_review_page_shows_the_moves_two_pipelines_read_differently(self):
        self.pipelines(MOVES_2)
        papers.agree(self.lib, "pmid123")
        out = os.path.join(self.lib, "calibration_review_moves.html")
        self.assertIn("1 place where two pipelines disagree", papers.review(self.lib, "moves", out=out)[0])
        page = papers.read_text(out)
        self.assertIn('<td class="n">0.50</td><td class="n">1.00</td><td class="n">does not pass</td>',
                      section(page, "papers"))  # module, relation, kind, and the pass line
        part = section(page, "disagreements")
        self.assertIn("Extended Data 3b / Extended Data Fig. 3b: module and relation differ", part)
        self.assertEqual(part.count('<tr class="diff"><th>module</th>'), 2)
        self.assertNotIn("modules differ", part)  # shown side by side, not again in the rest of the list
        self.assertIn("one pipeline | pipe 1 row 3 | Fig. 4c | Which targets act together?", part)
        lists = section(page, "lists")
        for field in ("kind", "module", "relation"):
            self.assertIn(f"<h3>{field}</h3>", lists)
        self.assertRegex(lists, r'E\.similar_targets</a></td><td class="n">1</td><td class="n">1</td>'
                                r'<td class="n">1</td><td class="n">0</td>')  # a move each, not the same move
        self.assertIn("pipeline 1, row 1: Fig. 2a", section(page, "two-rows"))
        self.assertIn("sha256 of the module list, module_list.md", section(page, "footer"))

    def test_both_pipelines_need_their_verified_rows(self):
        self.write_pipeline(1, "verified.md", GOOD + "\n")
        with self.assertRaises(papers.LibraryError):
            papers.agree(self.lib, "pmid123")

    def test_the_commands_reach_the_scripts(self):
        self.pipelines([GOOD, MOVE_B, MOVE_C])
        for argv in (["modules", self.lib], ["agree", self.lib, "pmid123"], ["check", self.lib, "pmid123",
                                                                           "--pipeline", "1"],
                     ["merge", self.lib], ["count", self.lib, "moves"]):
            with contextlib.redirect_stdout(io.StringIO()) as printed:
                self.assertEqual(papers.main(argv), 0, argv)
            self.assertTrue(printed.getvalue().strip(), argv)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(papers.main(["count", self.lib, "chain"]), 1)


class TestTheFigureReading(Library):
    """The figures reading on a paper of three figures: Fig. 1 with panels a-d, Fig. 2 with none, Extended Data Fig. 3
    with a and b."""

    def setUp(self):
        super().setUp()
        self.cut_paper(FIGURES_JATS)
        self.text = papers.source_text(self.lib, "pmid123")
        self.legs = papers.paper_legends(self.lib, "pmid123")

    def read(self, rows, current=True):
        return papers.check_figures_reading(papers.table_rows("\n".join(rows)), LABEL, self.text, self.legs,
                                            papers.KeptOut.of(self.lib), current)

    def write_reading(self, n, rows, started=True, version=None):
        """Reading n's rows, the reading started first (under the current fixed version, or as version 1 with
        version "v1", its rows then version 1's) unless started is False or it was started already."""
        folder = papers.figures_folder(self.lib, "pmid123", n)
        if started and not os.path.exists(os.path.join(folder, papers.VERSION_FILE)):
            papers.start(self.lib, "pmid123", "figures", n)
            if version == "v1":
                path = os.path.join(folder, papers.VERSION_FILE)
                with open(path, encoding="utf-8") as fh:
                    v = json.load(fh)
                papers.write_text(path, json.dumps({**v, "version": "v1", "sha256": papers.VERSIONS["figures"]["v1"]}))
        rows = [version_1(r) for r in rows] if version == "v1" else rows
        papers.write_text(os.path.join(folder, "figures.md"), "\n".join(rows) + "\n")

    def test_a_figure_check_runs_where_the_moves_wait_for_their_module_list(self):
        """A paper with moves rows in a library with no module list: its figures are checked; its moves fail, saying
        why, and are not merged."""
        self.write_reading(1, FIGURE_ROWS)
        papers.write_text(os.path.join(self.lib, "rows", "pmid123", "moves.md"), GOOD + "\n")
        self.assertFalse(os.path.exists(os.path.join(self.lib, papers.MODULE_LIST)))
        out = papers.check(self.lib, "pmid123", figure_reading=1)
        with open(os.path.join(self.lib, "checks", "pmid123.json"), encoding="utf-8") as fh:
            rep = json.load(fh)
        self.assertEqual([r["fails"] for r in rep["figures"]["rows"]], [[]] * 5)
        self.assertEqual(len(rep["moves"]["rows"]), 1)
        self.assertIn("no module list in the library", rep["moves"]["rows"][0]["fails"][0])
        self.assertNotIn("module_list_sha256", rep["moves"])
        self.assertTrue(any(line.startswith("moves: 1 rows; 1 fail") for line in out))

    def test_the_legends_give_each_figure_its_panel_letters_and_its_title(self):
        self.assertEqual({papers.figure_name(f): "".join(sorted(v["letters"])) for f, v in self.legs.items()},
                         {"Fig. 1": "abcd", "Fig. 2": "", "Extended Data Fig. 3": "ab"})
        self.assertEqual(self.legs[("main", 1)]["title"].strip(), CONCLUSION_1)
        self.assertEqual(len(papers.legend_units(self.legs)), 7)

    def test_a_reading_that_keeps_its_brief_passes_every_check(self):
        r = self.read(FIGURE_ROWS)
        self.assertEqual([(f, lk) for _, f, lk in r["rows"]], [([], [])] * 5)
        self.assertEqual({papers.figure_name(f): (v["fails"], v["taken_from"]) for f, v in r["conclusions"].items()},
                         {"Fig. 1": ([], "the legend's title"), "Fig. 2": ([], "the legend's title"),
                          "Extended Data Fig. 3": ([], "the Results text")})
        self.assertEqual(r["counts"]["legend_units_covered"], 6)
        self.assertEqual(r["units_not_read"], ["Fig. 1 d"])
        self.assertEqual([x["letters"] for x in r["passed"]][1], {"b", "c"})

    def test_each_check_can_fail(self):
        """Every check of a row, each failing alone on Fig. 1's second row, and every check of a figure's conclusion;
        a conclusion that fails leaves its figure's rows in."""
        b = dict(figure="Fig. 1", panel="b-c", evidence="a target's own gene", source="the screen",
                 chart="box or violin", quote=QUOTE_1BC)
        rows = [("the number of fields", "| " + " | ".join([LABEL, "Fig. 1"] + ["x"] * 7) + " |", ["9 fields, not 16"]),
                ("the paper", figure_row(**b, paper="PMID124 Zhao 2026"),
                 ["the paper is 'PMID124 Zhao 2026', not 'PMID123 Zhao 2026'"]),
                ("the evidence", figure_row(**{**b, "evidence": "a guess"}),
                 ["evidence 'a guess' is not one of the list"]),
                ("the source", figure_row(**{**b, "source": "the lab"}), ["source 'the lab' is not one of the list"]),
                ("the chart", figure_row(**{**b, "chart": "pie"}), ["chart 'pie' is not one of the list"]),
                ("no figure", figure_row(**{**b, "figure": "Table 1"}),
                 ["the figure field names 0 figures: 'Table 1'"]),
                ("two figures", figure_row(**{**b, "figure": "Figs. 1 and 2"}),
                 ["the figure field names 2 figures: 'Figs. 1 and 2'"]),
                ("a figure without its legend", figure_row(**{**b, "figure": "Fig. 5"}),
                 ["no legend of Fig. 5 in the text"]),
                ("a panel not read", figure_row(**{**b, "panel": "b2"}),
                 ["panel 'b2' is not read as letters or `all`"]),
                ("a letter not in the legend", figure_row(**{**b, "panel": "b-e"}),
                 ["panel letters e are not in the legend of Fig. 1"]),
                ("`all` for a legend with letters", figure_row(**{**b, "panel": "all"}),
                 ["the legend of Fig. 1 names panels abcd, not `all`"]),
                ("a letter for a legend without", figure_row(**{**b, "figure": "Fig. 2", "panel": "b"}),
                 ["the legend of Fig. 2 names no panel letters, so the panel is `all`"]),
                ("a short quote", figure_row(**{**b, "quote": "Boxplots of GENE1 expression"}),
                 ["evidence_quote has 4 words, not 8 to 40"]),
                ("an ellipsis", figure_row(**{**b, "quote": "Boxplots of GENE1 expression in cells ... in control "
                                                            "cells"}), ["evidence_quote holds an ellipsis"]),
                ("a quote not in the text", figure_row(**{**b, "quote": QUOTE_1BC.replace("against GENE1",
                                                                                          "against GENE2")}),
                 ["evidence_quote is not in the text"]),
                ("a source kept out", figure_row(**b, readout="as in Leftout 2026"),
                 ["cites sources kept out: Leftout 2026"]),
                ("the first version", first_version(figure_row(**b), "a target's own gene"),
                 [papers.FIGURES_FIRST_VERSION_ROW.format(10, 16)]),
                ("the marks", figure_row(**{**b, "marks": "a pixel"}), ["marks 'a pixel' is not one of the list"]),
                ("the targets shown", figure_row(**{**b, "targets_shown": "all"}),
                 ["targets shown 'all' is not one of the list"]),
                ("the colour", figure_row(**{**b, "colour": "rainbow", "colour_named_by": "a key"}),
                 ["colour 'rainbow' is not one of the list"]),
                ("where the colour is named", figure_row(**{**b, "colour": "groups", "colour_named_by": "a caption"}),
                 ["colour named by 'a caption' is not one of the list"]),
                ("one colour named", figure_row(**{**b, "colour_named_by": "a key"}),
                 ["one colour is named nowhere, so its colour_named_by is `not applicable`, and only then"]),
                ("an inferred chart", figure_row(**{**b, "chart": "box or violin (inferred)"}),
                 ["chart 'box or violin (inferred)': the chart is read from the image, never inferred"]),
                ("an image the paper does not have", figure_row(**{**b, "image": "f1.jpg"}),
                 ["image 'f1.jpg' is not one of the paper's figure images; a figure without one is `none`"])]
        for name, row, fails in rows:
            with self.subTest(name):
                r = self.read(FIGURE_ROWS[:1] + [row] + FIGURE_ROWS[2:])
                self.assertEqual(r["rows"][1][1], fails)
                self.assertEqual([f for i, (_, f, _) in enumerate(r["rows"]) if i != 1], [[]] * 4)
        conclusions = [("none", ["-"], ["0 conclusions, not one"]),
                       ("two", [CONCLUSION_1, CONCLUSION_1], ["2 conclusions, not one"]),
                       ("not on the first row", ["-", CONCLUSION_1],
                        ["the conclusion is not on the figure's first row"]),
                       ("too short", ["GENE1"], ["conclusion_quote has 1 words, not 2 to 50"]),
                       ("an ellipsis", ["GENE1 knockout ... transcript."], ["conclusion_quote holds an ellipsis"]),
                       ("not in the text", ["GENE1 knockout raises its own transcript."],
                        ["conclusion_quote is not in the text"])]
        for name, given, fails in conclusions:
            with self.subTest(f"conclusion: {name}"):
                first = [FIGURE_ROWS[0].replace(CONCLUSION_1, given[0])]
                second = [FIGURE_ROWS[1].replace("| b-c | - |", f"| b-c | {given[1]} |")] if len(given) > 1 else \
                    FIGURE_ROWS[1:2]
                r = self.read(first + second + FIGURE_ROWS[2:])
                self.assertEqual(r["conclusions"][("main", 1)]["fails"], fails)
                self.assertEqual(len(r["passed"]), 5)
        looks = [("numbers of n_stated", FIGURE_ROWS[1].replace("n = 4 donors", "n = 12 donors"),
                  ["numbers of n_stated not in the text: 12"]),
                 ("a quote from the Results", figure_row(**{**b, "quote": "Knockout of GENE1 lowered its own "
                                                                          "transcript in the cells that carry its"}),
                  ["the evidence quote is from the Results text, not the figure's legend"])]
        for name, row, found in looks:
            with self.subTest(f"to look at: {name}"):
                r = self.read(FIGURE_ROWS[:1] + [row] + FIGURE_ROWS[2:])
                self.assertEqual((r["rows"][1][1], r["rows"][1][2]), ([], found))

    def test_rows_name_their_figure_and_panels_as_the_legends_give_them(self):
        """A row of an Expanded View figure is its legend's; a one-panel legend whose text holds "guide(s)" is `all`."""
        results = ("# Figure and supplementary legends\n- Figure 3. Main. (A) Schematic. (B) UMAP.\n"
                   "- Figure EV1. Expanded view. (A) Cells per guide. (C) Depth. (F) Reads.\n"
                   "- Figure 4. Number of guide(s) per cell in the screen across all donors.\n")
        legs, text = papers.figure_legends(results), papers.normal(results)
        quote = "Number of guide(s) per cell in the screen across all donors."
        row = [LABEL, "Figure EV1", "f", "-", "cells, guides and quality", "the screen", "not stated", "reads",
               "nothing", "not stated", quote]
        for figure, panel in (("Figure EV1", "f"), ("Fig. 4", "all")):
            with self.subTest(figure):
                self.assertEqual(papers.check_figure_row(row[:1] + [figure, panel] + row[3:], LABEL, text, legs)[3],
                                 [])
        self.assertEqual(papers.check_figure_row(row[:1] + ["Fig. 4", "s"] + row[3:], LABEL, text, legs)[3],
                         ["the legend of Fig. 4 names no panel letters, so the panel is `all`"])

    def test_a_conclusion_copied_from_another_figure_s_legend_fails(self):
        rows = list(FIGURE_ROWS)
        rows[2] = rows[2].replace(f"| {CONCLUSION_2} |", f"| {CONCLUSION_1} |")
        fig2 = self.read(rows)["conclusions"][("main", 2)]
        self.assertEqual((fig2["fails"], fig2["taken_from"]),
                         (["conclusion_quote is from the legend of Fig. 1, not its figure's legend or the Results "
                           "text"], "the legend of Fig. 1"))
        self.assertEqual(self.read(FIGURE_ROWS)["conclusions"][("ext", 3)]["taken_from"], "the Results text")

    def test_a_quote_is_within_one_place_named_as_it_is_and_a_dash_gives_no_conclusion(self):
        with self.subTest("a quote from one legend into the next"):
            across = FIGURE_ROWS[1].replace(QUOTE_1BC, "effects. - Figure 2. Cells lacking GENE1 secrete less "
                                                       "interferon")
            r = self.read(FIGURE_ROWS[:1] + [across] + FIGURE_ROWS[2:])
            self.assertEqual(r["rows"][1][1], ["evidence_quote is not within one place of the text (a legend, the "
                                               "Results text, the Methods): it joins two, or quotes a caption that is "
                                               "not read"])
            self.assertEqual(r["counts"]["quotes_found"], 4)
        for dash in ("—", "–"):
            with self.subTest(f"{dash} for no conclusion"):
                second = FIGURE_ROWS[1].replace("| b-c | - |", f"| b-c | {dash} |")
                self.assertEqual(self.read(FIGURE_ROWS[:1] + [second] + FIGURE_ROWS[2:])["conclusions"][("main", 1)]
                                 ["fails"], [])
        with self.subTest("a quote of the Methods"):
            methods = "Guides were assigned to cells with a mixture model of guide counts."
            self.cut_paper(FIGURES_JATS.replace("Guides were assigned with a mixture model.", methods))
            self.text = papers.source_text(self.lib, "pmid123")
            r = self.read(FIGURE_ROWS[:1] + [FIGURE_ROWS[1].replace(QUOTE_1BC, methods)] + FIGURE_ROWS[2:])
            self.assertEqual((r["rows"][1][1], r["rows"][1][2]),
                             ([], ["the evidence quote is from the Methods, not the figure's legend"]))

    def test_a_row_of_the_first_version_is_checked_by_its_own_checks_outside_a_library_s_check(self):
        old = [first_version(row, kind) for row, kind in zip(FIGURE_ROWS, (
            "the design", "a target's own gene", "an independent experiment", "cells, guides and quality",
            "other (donors)"))]
        self.assertEqual([f for _, f, _ in self.read(old, current=False)["rows"]], [[]] * 5)
        self.assertEqual(self.read([first_version(FIGURE_ROWS[0], "a phenotype")], current=False)["rows"][0][1],
                         ["kind 'a phenotype' is not one of the list"])
        self.assertTrue(all(f[0] == papers.FIGURES_FIRST_VERSION_ROW.format(10, 16)
                            for _, f, _ in self.read(old)["rows"]))
        self.assertEqual(self.read(old)["counts"]["quotes_found"], 0)

    def second_reading(self):
        return second_figure_reading()

    def test_two_readings_are_cut_into_panels_and_measured_panel_by_panel(self):
        self.write_reading(1, FIGURE_ROWS)
        self.write_reading(2, self.second_reading())
        out = papers.agree_figures(self.lib, "pmid123")
        with open(os.path.join(self.lib, "checks", "pmid123.figures_agreement.json"), encoding="utf-8") as fh:
            rep = json.load(fh)
        m = rep["measures"]
        self.assertEqual((m["units_in_legends"], m["units_covered_by_either"], m["units_covered_by_both"],
                          m["unit_agreement"]), (7, 6, 5, 0.833))
        self.assertEqual((m["evidence_agreement"], m["source_agreement"], m["chart_agreement"],
                          m["evidence_source_and_chart_agreement"]), (0.8, 0.8, 0.8, 0.4))
        self.assertEqual((m["evidence_agreement_with_other_words"], m["chart_agreement_with_other_words"]),
                         (0.8, 0.8))
        self.assertNotIn("source_agreement_with_other_words", m)
        self.assertEqual((m["quote_check"], m["conclusion_check"]), (0.909, 1.0))
        self.assertEqual((m["figures_read_by_both"], m["conclusions_found_by_both"], m["same_conclusion_span"],
                          m["same_conclusion_taken_from"]), (3, 3, 0.667, 0.667))
        self.assertEqual(rep["review"], [
            "evidence differs | Fig. 1 b | a target's own gene / a program or protein per cell",
            "chart differs | Fig. 1 c | box or violin / box or violin with points",
            "one reading only | Fig. 2 all | reading 1",
            "source differs | Extended Data Fig. 3 b | the screen / the paper's other data",
            "read by neither | Fig. 1 d",
            "failed check | reading 2 row 4 | Fig. 2 all | evidence_quote is not in the text"])
        self.assertEqual([(u["figure"], u["panel"], u["rows_1"], u["rows_2"]) for u in rep["units"]][:3],
                         [("Fig. 1", "a", [1], [1]), ("Fig. 1", "b", [2], [2]), ("Fig. 1", "c", [2], [3])])
        self.assertEqual(m["pass"], {"evidence_agreement": True, "source_agreement": False, "chart_agreement": True,
                                     "quote_check": False, "conclusion_check": True, "marks_agreement": True,
                                     "targets_shown_agreement": True, "colour_agreement": True,
                                     "colour_named_by_agreement": True})
        self.assertFalse(m["passes"])
        self.assertEqual(rep["labels"], list(papers.FIGURE_LABELS_V2))
        self.assertEqual({n: v["version"] for n, v in rep["versions"].items()}, {"1": "v2", "2": "v2"})
        self.assertIn("  under v2, against the pass line:", out)
        self.assertIn("  from the images, on the panels both cover: marks 1.00; targets shown 1.00; colour 1.00; "
                      "colour named by 1.00", out)
        self.assertIn("    source agreement 0.80, at least 0.85: does not pass", out)
        self.assertIn("    evidence agreement 0.80, at least 0.75: passes", out)
        self.assertIn("  the paper does not pass: source agreement, quote check", out)
        self.assertIn("  - one reading only | Fig. 2 all | reading 1", out)

    def test_readings_not_both_started_under_one_fixed_version_are_measured_and_not_held_to_the_line(self):
        self.write_reading(1, FIGURE_ROWS)
        self.write_reading(2, FIGURE_ROWS, started=False)
        out = papers.agree_figures(self.lib, "pmid123")
        with open(os.path.join(self.lib, "checks", "pmid123.figures_agreement.json"), encoding="utf-8") as fh:
            rep = json.load(fh)
        self.assertFalse(any("pass" in k for k in rep["measures"]))
        self.assertEqual(rep["measures"]["evidence_agreement"], 1.0)
        self.assertIn("  not held to the pass line: the two readings were not both started under one fixed version of "
                      "the reading (v1, v2)", out)

    def test_a_reading_starts_once_under_a_fixed_version_before_its_rows_are_written(self):
        out = papers.start(self.lib, "pmid123", "figures", 1)
        folder = papers.figures_folder(self.lib, "pmid123", 1)
        self.assertIn("pmid123 figures 1: started under v2", out[0])
        with open(os.path.join(folder, papers.VERSION_FILE), encoding="utf-8") as fh:
            v = json.load(fh)
        self.assertEqual((v["version"], v["sha256"], v["key"], v["n"]),
                         ("v2", papers.VERSIONS["figures"]["v2"], "pmid123", 1))
        self.assertEqual(list(v["briefs"]), ["figure-reader.md"])
        with self.assertRaises(papers.LibraryError) as e:
            papers.start(self.lib, "pmid123", "figures", 1)
        self.assertIn("was started already", str(e.exception))
        papers.write_text(os.path.join(papers.figures_folder(self.lib, "pmid123", 2), "figures.md"), "| x |\n")
        with self.assertRaises(papers.LibraryError) as e:
            papers.start(self.lib, "pmid123", "figures", 2)
        self.assertIn("has rows already (figures.md in", str(e.exception))
        with self.assertRaises(papers.LibraryError) as e:
            papers.start(self.lib, "pmid123", "figures")
        self.assertIn("name the reading with --figures", str(e.exception))
        fixed = papers.VERSIONS
        papers.VERSIONS = {"figures": {"v1": "0" * 64}}
        try:
            with self.assertRaises(papers.LibraryError) as e:
                papers.start(self.lib, "pmid123", "figures", 3)
            self.assertIn("not one of its fixed versions (v1): record the new version before reading", str(e.exception))
        finally:
            papers.VERSIONS = fixed

    def test_a_reading_not_started_under_a_fixed_version_is_checked_and_not_merged(self):
        for name, write in (("not started", lambda: self.write_reading(1, FIGURE_ROWS, started=False)),
                            ("its version changed by hand", self.version_changed_by_hand)):
            with self.subTest(name):
                write()
                out = papers.check(self.lib, "pmid123", figure_reading=1)
                self.assertIn("  not read under a fixed version of the reading (v1, v2), so it is not merged: a reading "
                              "is started with `papers.py start` before its reader is launched", out)
                self.assertEqual(papers.merge(self.lib), ["pmid123 figures: left out, not read under a fixed version "
                                                          "of the reading (v1, v2)"])
        shutil.rmtree(papers.figures_folder(self.lib, "pmid123", 1))
        self.write_reading(1, FIGURE_ROWS)
        self.assertIn("  read under v2", papers.check(self.lib, "pmid123", figure_reading=1))
        self.assertEqual(papers.merge(self.lib), ["pmid123 figures: 5 of 5 rows merged"])
        self.assertEqual({x["version"] for x in papers.catalogue(self.lib, "figures")}, {"v2"})

    def test_version_1_s_rows_stay_version_1_s_and_a_reading_holds_rows_of_its_own_version(self):
        """A reading started under v1, its rows of eleven fields, is checked and merged as v1's, the fields read from
        the images empty in the catalogue; rows of eleven fields in a reading started under v2 fail."""
        self.write_reading(1, FIGURE_ROWS, version="v1")
        self.assertIn("  read under v1", papers.check(self.lib, "pmid123", figure_reading=1))
        self.assertEqual(papers.merge(self.lib), ["pmid123 figures: 5 of 5 rows merged"])
        rows = papers.catalogue(self.lib, "figures")
        self.assertEqual(({x["version"] for x in rows}, {x["marks"] for x in rows}), ({"v1"}, {""}))
        shutil.rmtree(papers.figures_folder(self.lib, "pmid123", 1))
        self.write_reading(1, [version_1(r) for r in FIGURE_ROWS])  # started under v2
        out = papers.check(self.lib, "pmid123", figure_reading=1)
        self.assertIn("  row 1 fails: a row of 11 fields in a reading started under v2, whose rows have 16", out)

    def version_changed_by_hand(self):
        shutil.rmtree(papers.figures_folder(self.lib, "pmid123", 1))
        self.write_reading(1, FIGURE_ROWS)
        path = os.path.join(papers.figures_folder(self.lib, "pmid123", 1), papers.VERSION_FILE)
        with open(path, encoding="utf-8") as fh:
            v = json.load(fh)
        papers.write_text(path, json.dumps({**v, "sha256": "1" * 64}))

    def test_each_merge_records_the_papers_whose_rows_it_changed(self):
        self.write_reading(1, FIGURE_ROWS)
        papers.check(self.lib, "pmid123", figure_reading=1)
        papers.merge(self.lib)
        papers.merge(self.lib)  # nothing changed: nothing recorded
        self.write_reading(1, FIGURE_ROWS[:4])
        papers.check(self.lib, "pmid123", figure_reading=1)
        papers.merge(self.lib)
        with open(os.path.join(self.lib, "catalogue", papers.CHANGES), encoding="utf-8", newline="") as fh:
            lines = [line.split("\t")[1:] for line in fh.read().splitlines()]
        self.assertEqual(lines, [["reading", "key", "rows_before", "rows_now", "versions"],
                                 ["figures", "pmid123", "0", "5", "v2"], ["figures", "pmid123", "5", "4", "v2"]])

    def test_a_reading_without_fixed_versions_records_its_version_and_merges_as_before(self):
        out = papers.start(self.lib, "pmid123", "chain")
        self.assertIn("started under no fixed version (the reading has none yet)", out[0])
        self.assertTrue(os.path.exists(os.path.join(self.lib, "rows", "pmid123", "chain.version.json")))
        self.assertNotIn("chain", papers.VERSIONS)

    def test_both_readings_need_their_rows(self):
        self.write_reading(1, FIGURE_ROWS)
        with self.assertRaises(papers.LibraryError):
            papers.agree_figures(self.lib, "pmid123")
        with self.assertRaises(papers.LibraryError) as e:
            papers.check(self.lib, "pmid123")  # no other rows, and no reading named
        self.assertIn("name the reading whose rows are the paper's with --figures", str(e.exception))
        with self.assertRaises(papers.LibraryError):
            papers.check(self.lib, "pmid123", figure_reading=2)

    def merged(self):
        """Reading 1, with Fig. 2's conclusion too short, Extended Data Fig. 3b's evidence under other (...), and a
        row for Fig. 1d whose chart is not one of the list, checked as the paper's figures and merged."""
        rows = list(FIGURE_ROWS)
        rows[2] = rows[2].replace(f"| {CONCLUSION_2} |", "| Cells |")
        rows[4] = rows[4].replace("| cells, guides and quality | the screen | embedding",
                                  "| other (donor effects) | the screen | embedding")
        rows.insert(2, figure_row(panel="d", chart="pie", quote="Heatmap of the correlation of every two guides' "
                                                                "effects."))
        self.write_reading(1, rows)
        out = papers.check(self.lib, "pmid123", figure_reading=1)
        return out, papers.merge(self.lib)

    def test_the_rows_that_pass_enter_the_catalogue_and_a_failed_conclusion_stays_with_them(self):
        out, merged = self.merged()
        self.assertIn("figures: 6 rows of rows/pmid123/figures_1/figures.md; 1 fail, 0 to look at", out)
        self.assertIn("  Fig. 2, its conclusion fails: conclusion_quote has 1 words, not 2 to 50", out)
        self.assertEqual(merged, ["pmid123 figures: 5 of 6 rows merged"])
        rows = papers.catalogue(self.lib, "figures")
        self.assertEqual([(x["figure"], x["panel"]) for x in rows], [("Fig. 1", "a"), ("Fig. 1", "b-c"),
                                                                    ("Fig. 2", "all"), ("Extended Data Fig. 3", "a"),
                                                                    ("Extended Data Fig. 3", "b")])
        self.assertEqual(rows[2]["looks"], "the figure's conclusion: conclusion_quote has 1 words, not 2 to 50")
        self.assertEqual(rows[0]["looks"], "")
        self.write_reading(1, FIGURE_ROWS)
        self.assertIn("its rows changed after the check", papers.merge(self.lib)[0])

    def test_figures_are_counted_by_evidence_source_and_chart_in_papers_rows_and_panels(self):
        self.merged()
        out = papers.count(self.lib, "figures", ["evidence"])
        self.assertEqual(out[0], "| evidence | papers | rows | panels |")
        self.assertEqual(out[1], "|---|---|---|---|")
        for line in ("| a target's own gene | 1 | 1 | 2 |", "| other | 1 | 1 | 1 |", "| the design | 1 | 1 | 1 |",
                     "| a phenotype | 1 | 1 | 1 |"):
            self.assertIn(line, out)
        self.assertIn("| the screen | 1 | 3 | 4 |", papers.count(self.lib, "figures", ["source"]))
        self.assertIn("| not stated | 1 | 2 | 2 | 0 |", papers.count(self.lib, "figures", ["chart"]))
        self.assertIn("| a target's own gene | box or violin | 1 | 1 | 2 | 0 |",
                      papers.count(self.lib, "figures", ["evidence", "chart"]))
        with self.assertRaises(papers.LibraryError):
            papers.count(self.lib, "figures")  # no default field

    def test_the_evidence_has_the_kinds_its_calibration_added(self):
        rows = [FIGURE_ROWS[1].replace("| a target's own gene |", f"| {kind} |")
                for kind in ("gene programs or modules found", "a trajectory or pseudotime",
                             "a target's effect on nearby genes (cis)", "a model fitted to the screen")]
        for row in rows:
            with self.subTest(row[:60]):
                r = self.read(FIGURE_ROWS[:1] + [row] + FIGURE_ROWS[2:])
                self.assertEqual([f for _, f, _ in r["rows"]], [[]] * 5)

    def test_a_chart_the_legend_does_not_name_may_be_a_listed_chart_inferred(self):
        """In version 1, " (inferred)" after a chart of the list, and nothing after it; never after `not stated` or
        `other (...)`."""
        v1 = [version_1(r) for r in FIGURE_ROWS]
        for chart in ("scatter with correlation (inferred)", "box or violin (inferred)"):
            with self.subTest(chart):
                r = self.read(v1[:1] + [v1[1].replace("| box or violin |", f"| {chart} |")] + v1[2:])
                self.assertEqual([f for _, f, _ in r["rows"]], [[]] * 5)
        refused = [(chart, f"chart {chart!r}: `not stated` and `other (...)` are never inferred")
                   for chart in ("not stated (inferred)", "other (pie) (inferred)", "other (inferred)")]
        refused += [(chart, f"chart {chart!r} is not one of the list")
                    for chart in ("box or violin (inferred) here", "box or violin (inferred) (inferred)",
                                  "box or violin(inferred)", "box or violin (Inferred)", "pie (inferred)")]
        for chart, why in refused:
            with self.subTest(chart):
                r = self.read(v1[:1] + [v1[1].replace("| box or violin |", f"| {chart} |")] + v1[2:])
                self.assertEqual(r["rows"][1][1], [why])

    def test_two_readings_compare_charts_without_inferred_and_count_the_panels_given_as_inferred(self):
        self.write_reading(1, FIGURE_ROWS, version="v1")
        second = self.second_reading()
        second[1] = second[1].replace("| box or violin |", "| box or violin (inferred) |")  # Fig. 1b
        self.write_reading(2, second, version="v1")
        out = papers.agree_figures(self.lib, "pmid123")
        with open(os.path.join(self.lib, "checks", "pmid123.figures_agreement.json"), encoding="utf-8") as fh:
            rep = json.load(fh)
        m = rep["measures"]
        self.assertEqual((m["chart_agreement"], m["chart_agreement_with_other_words"]), (0.8, 0.8))
        self.assertEqual((m["units_with_chart_inferred_1"], m["units_with_chart_inferred_2"]), (0, 1))
        b = next(u for u in rep["units"] if (u["figure"], u["panel"]) == ("Fig. 1", "b"))
        self.assertEqual((b["chart_1"], b["chart_2"], b["chart_agrees"]),
                         ("box or violin", "box or violin (inferred)", True))
        self.assertIn('  charts given as inferred, compared without "(inferred)": on 0 panels in reading 1 and 1 in '
                      'reading 2', out)
        self.assertNotIn("chart differs | Fig. 1 b", " ".join(rep["review"]))

    def test_charts_are_counted_without_inferred_with_the_rows_inferred(self):
        rows = list(FIGURE_ROWS)
        rows[1] = rows[1].replace("| box or violin |", "| box or violin (inferred) |")
        rows.insert(2, figure_row(panel="d", chart="box or violin", quote="Heatmap of the correlation of every two "
                                                                          "guides' effects."))
        self.write_reading(1, rows, version="v1")
        papers.check(self.lib, "pmid123", figure_reading=1)
        papers.merge(self.lib)
        out = papers.count(self.lib, "figures", ["chart"])
        self.assertEqual(out[:2], ["| chart | papers | rows | panels | rows inferred |", "|---|---|---|---|---|"])
        self.assertIn("| box or violin | 1 | 2 | 3 | 1 |", out)
        self.assertFalse(any("(inferred)" in line for line in out[2:]))
        self.assertIn("| a target's own gene | box or violin | 1 | 1 | 2 | 1 |",
                      papers.count(self.lib, "figures", ["evidence", "chart"]))
        self.assertEqual(papers.count(self.lib, "figures", ["evidence"])[0], "| evidence | papers | rows | panels |")

    def test_a_conclusion_on_a_row_that_fails_goes_with_its_figure_s_first_merged_row(self):
        rows = list(FIGURE_ROWS)
        rows[0] = rows[0].replace(QUOTE_1A, "Schematic of the pooled screen in primary")  # 7 words: the row fails
        self.write_reading(1, rows)
        papers.check(self.lib, "pmid123", figure_reading=1)
        self.assertEqual(papers.merge(self.lib), ["pmid123 figures: 4 of 5 rows merged"])
        fig1 = [x for x in papers.catalogue(self.lib, "figures") if x["figure"] == "Fig. 1"]
        self.assertEqual([(x["panel"], x["conclusion_quote"], x["looks"]) for x in fig1],
                         [("b-c", CONCLUSION_1, "the figure's conclusion is from its row 1, which failed its checks")])

    def test_panels_are_counted_by_the_figure_read_however_it_is_written(self):
        rows = list(FIGURE_ROWS)
        rows.insert(2, figure_row(figure="Figure 1", panel="b", evidence="a target's own gene", source="the screen",
                                  chart="box or violin", quote=QUOTE_1BC))
        self.write_reading(1, rows)
        papers.check(self.lib, "pmid123", figure_reading=1)
        papers.merge(self.lib)
        self.assertIn("| a target's own gene | 1 | 2 | 2 |", papers.count(self.lib, "figures", ["evidence"]))

    def test_a_draft_s_figures_citation_needs_a_merged_row_of_its_panels(self):
        self.merged()  # Fig. 1d's row fails its chart and is not merged; Extended Data Fig. 3 has panels a and b
        draft = os.path.join(self.lib, "drafts", "panels.md")
        cites = ("Fig. 1d", "Extended Data Fig. 3h", "Fig. 1b", "Fig. 1 a-c", "Fig. 2", "Extended Data Fig. 3 b",
                 "ED Fig. 3a", "Fig. 1c, d")
        papers.write_text(draft, "".join(f"- **If you see:** a panel [{LABEL}, {c}].\n" for c in cites))
        self.assertEqual(papers.check_draft(self.lib, draft),
                         [f"no row of {LABEL} names {c}: [{LABEL}, {c}]" for c in ("Fig. 1d", "Extended Data Fig. 3h",
                                                                                 "Fig. 1c, d")])

    def test_a_draft_may_cite_a_figures_row_by_its_figure_and_panel(self):
        self.merged()
        draft = os.path.join(self.lib, "drafts", "figures.md")
        papers.write_text(draft, f"- **If you see:** a schematic first [{LABEL}, Fig. 1a].\n"
                                 f"- **Read it as:** the design [{LABEL}, Fig. 1b-c].\n"
                                 f"- **Also:** a heatmap [{LABEL}, Fig. 4b].\n")
        out = papers.check_draft(self.lib, draft)
        self.assertEqual(out, [f"no row of {LABEL} names Fig. 4b: [{LABEL}, Fig. 4b]"])
        shown = "\n".join(papers.show(self.lib, "pmid123", 1))
        self.assertIn("figures, row 1 of 6:", shown)
        self.assertIn("We screened 20 regulators", shown.split("  the text:")[1])  # a sentence naming its figure

    def test_the_commands_reach_the_figures_reading(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(papers.main(["start", self.lib, "pmid123", "--reading", "figures", "--figures", "1"]), 0)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(papers.main(["start", self.lib, "pmid123", "--reading", "figures", "--figures", "2",
                                          "--pipeline", "1"]), 1)
        self.write_reading(1, FIGURE_ROWS)
        self.write_reading(2, self.second_reading())
        for argv in (["agree", self.lib, "pmid123", "--reading", "figures"],
                     ["check", self.lib, "pmid123", "--figures", "1"], ["merge", self.lib],
                     ["count", self.lib, "figures", "--by", "evidence", "--by", "chart"]):
            with contextlib.redirect_stdout(io.StringIO()) as printed:
                self.assertEqual(papers.main(argv), 0, argv)
            self.assertTrue(printed.getvalue().strip(), argv)
        self.assertTrue(os.path.exists(os.path.join(self.lib, "checks", "pmid123.figures_agreement.json")))
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(papers.main(["agree", self.lib, "pmid123"]), 1)  # the moves reading has no pipelines


class TestBatches(Library):
    """A batch planned before it runs: the first paper of every five read twice, its estimate and its limit; each
    reading's tokens recorded, and the batch stopped before it would pass its limit."""

    def setUp(self):
        super().setUp()
        self.keys = []
        for i in range(6):
            pmid = str(200 + i)
            self.replies[pmid] = summary(pmid, f"Author{i} A", 2020 + i, f"10.1000/b{i}")
            self.keys.append(f"pmid{pmid}")
        papers.register(self.lib, [k[4:] for k in self.keys])

    def test_a_batch_reads_the_first_of_every_five_papers_twice_and_states_its_estimate(self):
        out = papers.plan_batch(self.lib, "first-batch", "figures", self.keys)
        self.assertEqual(out[:3], ["batch first-batch: 6 papers, 8 readings of the figures reading, under v2",
                                   "read twice, for the drift check: Author0 2020 and Author5 2025",
                                   "estimate 800,000 tokens; the batch stops before it would pass 1,200,000 (1.5 times)"])
        with open(papers.batch_path(self.lib, "first-batch"), encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual([x["readings"] for x in data["papers"]], [[1, 2], [1], [1], [1], [1], [1, 2]])
        self.assertEqual((data["version"], data["sha256"], data["estimate"], data["limit"], data["spent"]),
                         ("v2", papers.VERSIONS["figures"]["v2"], 800000, 1200000, []))
        self.assertIn("3 pipelines of the moves reading", papers.plan_batch(self.lib, "moves", "moves",
                                                                           self.keys[:2])[0])
        for name, keys, why in (("first-batch", self.keys, "is planned already"), ("a/b", self.keys, "a batch's name"),
                                ("twice", self.keys[:1] * 2, "a paper is named twice"),
                                ("none", [], "name the batch's papers"), ("other", ["pmid999"], "no paper pmid999")):
            with self.subTest(why), self.assertRaises(papers.LibraryError) as e:
                papers.plan_batch(self.lib, name, "figures", keys)
            self.assertIn(why, str(e.exception))
        with self.assertRaises(papers.LibraryError):
            papers.plan_batch(self.lib, "chain", "chain", self.keys)

    def test_a_batch_whose_brief_is_not_a_fixed_version_is_not_planned(self):
        fixed = papers.VERSIONS
        papers.VERSIONS = {"figures": {"v1": "0" * 64}}
        try:
            with self.assertRaises(papers.LibraryError) as e:
                papers.plan_batch(self.lib, "b", "figures", self.keys)
            self.assertIn("record the new version before a batch", str(e.exception))
        finally:
            papers.VERSIONS = fixed

    def test_each_reading_s_tokens_are_recorded_and_the_batch_stops_before_passing_its_limit(self):
        papers.plan_batch(self.lib, "b", "figures", self.keys)
        done = [(k, 1) for k in self.keys] + [(self.keys[0], 2), (self.keys[5], 2)]
        outs = [papers.record_spent(self.lib, "b", k, n, 160000) for k, n in done]
        self.assertEqual(outs[0], ["batch b: 1 of 8 done, 160,000 tokens; estimated 100,000 for them and 800,000 for "
                                   "the batch"])
        self.assertEqual(len(outs[5]), 1)  # 960,000 and the next reading's 100,000 stay within 1,200,000
        self.assertEqual(outs[6][1], "stop: the next reading would take the batch past 1,200,000 tokens (1.5 times its "
                                     "estimate); ask the user before it runs")
        self.assertEqual(outs[7][1], "the batch is done, past its limit of 1,200,000")
        for k, n, why in ((self.keys[0], 1, "is recorded already"), (self.keys[1], 2, "is not in batch b"),
                          ("pmid999", 1, "is not in batch b")):
            with self.subTest(why), self.assertRaises(papers.LibraryError) as e:
                papers.record_spent(self.lib, "b", k, n, 1000)
            self.assertIn(why, str(e.exception))
        with self.assertRaises(papers.LibraryError):
            papers.record_spent(self.lib, "none", self.keys[0], 1, 1000)

    def test_the_commands_reach_the_batches(self):
        for argv in (["batch", self.lib, "b", "--reading", "figures", "--keys", ",".join(self.keys)],
                     ["spent", self.lib, "b", self.keys[0], "1", "90000"]):
            with contextlib.redirect_stdout(io.StringIO()) as printed:
                self.assertEqual(papers.main(argv), 0, argv)
            self.assertTrue(printed.getvalue().strip(), argv)


class TestThePartsOfTheBase(Library):
    """The parts of the knowledge base that code writes from the catalogue, between their markers: the papers read,
    and the figures by evidence, from one paper's figures read under v1, Extended Data Fig. 3b's evidence under
    other (...)."""

    BEFORE = "# Figure patterns\n\nWritten by hand.\n\n"
    AFTER = "\nAlso by hand.\n"

    def setUp(self):
        super().setUp()
        self.cut_paper(FIGURES_JATS)
        rows = list(FIGURE_ROWS)
        rows[4] = rows[4].replace("| cells, guides and quality | the screen | embedding",
                                  "| other (donor effects) | the screen | embedding")
        papers.start(self.lib, "pmid123", "figures", 1)
        papers.write_text(os.path.join(papers.figures_folder(self.lib, "pmid123", 1), "figures.md"),
                          "\n".join(rows) + "\n")
        papers.check(self.lib, "pmid123", figure_reading=1)
        papers.merge(self.lib)
        self.patterns = os.path.join(self.kb, "figure_patterns.md")
        self.read_list = os.path.join(self.kb, "papers_read.md")
        papers.write_text(self.patterns, self.BEFORE + "<!-- from the papers: figures by evidence -->\nold\n" +
                          papers.PART_END + self.AFTER)
        papers.write_text(self.read_list, "# The papers read\n\n<!-- from the papers: papers read -->\n" +
                          papers.PART_END + "\n")

    def test_each_part_is_written_between_its_markers_and_nothing_else_changes(self):
        modules = papers.read_text(os.path.join(self.kb, "modules", "C.genes.md"))
        self.assertEqual(papers.write_base(self.lib), ["figure_patterns.md: figures by evidence, written",
                                                       "papers_read.md: papers read, written"])
        text = papers.read_text(self.patterns)
        self.assertTrue(text.startswith(self.BEFORE + "<!-- from the papers: figures by evidence -->\n" +
                                        papers.PART_NOTE))
        self.assertTrue(text.endswith(papers.PART_END + self.AFTER))
        part = text.split("-->\n", 1)[1].split(papers.PART_END)[0]
        self.assertIn("Of the 1 paper read for their figures (v2): the panels by what they show", part)
        rows = [line for line in part.split("\n") if line.startswith("| ") and not line.startswith("| Evidence")]
        self.assertEqual(rows, [
            "| the design | 1 | 1 | schematic (1) | Zhao 2026, Fig. 1a |",
            "| a target's own gene | 1 | 2 | box or violin (1) | Zhao 2026, Fig. 1b-c |",
            "| cells, guides and quality | 1 | 1 | not stated (1) | Zhao 2026, Extended Data Fig. 3a |",
            "| a phenotype | 1 | 1 | not stated (1) | Zhao 2026, Fig. 2 |",
            "| other: donor effects (1) | 1 | 1 | embedding (1) | Zhao 2026, Extended Data Fig. 3b |"])
        self.assertIn("| Zhao 2026 | 10.1000/zhao | figures (v2) |", papers.read_text(self.read_list))
        self.assertIn("1 paper read:", papers.read_text(self.read_list))
        self.assertEqual(papers.read_text(os.path.join(self.kb, "modules", "C.genes.md")), modules)
        self.assertEqual(papers.write_base(self.lib, check_only=True),
                         ["figure_patterns.md: figures by evidence, as the catalogue gives them",
                          "papers_read.md: papers read, as the catalogue gives them"])

    def test_a_part_changed_by_hand_is_named_by_the_check_and_written_again(self):
        papers.write_base(self.lib)
        written = papers.read_text(self.patterns)
        papers.write_text(self.patterns, written.replace("| a phenotype | 1 |", "| a phenotype | 9 |"))
        self.assertEqual(papers.write_base(self.lib, check_only=True)[0],
                         "figure_patterns.md: figures by evidence, not as the catalogue gives them; write them with "
                         "papers.py write-base")
        self.assertIn("| a phenotype | 9 |", papers.read_text(self.patterns))
        papers.write_base(self.lib)
        self.assertEqual(papers.read_text(self.patterns), written)

    def test_markers_that_cannot_be_read_are_refused_and_a_base_without_them_is_told_how(self):
        for name, text, why in (("an unknown part", "<!-- from the papers: moves by module -->\nx\n" + papers.PART_END,
                                 "no part 'moves by module'"),
                                ("no end", "<!-- from the papers: papers read -->\nx\n", "has no line"),
                                ("one inside another", "<!-- from the papers: papers read -->\n<!-- from the papers: "
                                 "papers read -->\n" + papers.PART_END, "begins inside another")):
            with self.subTest(name):
                papers.write_text(self.read_list, text)
                with self.assertRaises(papers.LibraryError) as e:
                    papers.write_base(self.lib)
                self.assertIn(why, str(e.exception))
        os.remove(self.read_list)
        os.remove(self.patterns)
        self.assertIn("no part from the papers in", papers.write_base(self.lib)[0])

    def test_the_command_reaches_the_parts(self):
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(papers.main(["write-base", self.lib, "--check"]), 0)
        self.assertIn("not as the catalogue gives them", printed.getvalue())
        self.assertIn("\nold\n", papers.read_text(self.patterns))


class TestTheReviewPage(Library):
    """The review page of a calibration batch, on the figures reading's paper read twice: Fig. 1b's evidence and 1c's
    chart read differently, Fig. 2 read by reading 1 only (reading 2's row failing its quote), Extended Data Fig. 3b's
    evidence under other (...) in reading 1 and its source read differently."""

    def setUp(self):
        super().setUp()
        self.cut_paper(REVIEW_JATS)
        for n, rows in ((1, review_reading_1()), (2, review_reading_2())):
            self.write_reading(n, rows)
        papers.agree_figures(self.lib, "pmid123")
        self.out = os.path.join(self.lib, "calibration_review_figures.html")

    def write_reading(self, n, rows):
        papers.write_text(os.path.join(self.lib, "rows", "pmid123", f"figures_{n}", "figures.md"),
                          "\n".join(rows) + "\n")

    def page(self, **given):
        papers.review(self.lib, "figures", out=self.out, **given)
        return papers.read_text(self.out)

    def test_the_page_holds_its_sections_in_order_and_needs_no_network(self):
        page = self.page()
        self.assertTrue(page.startswith("<!doctype html>"))
        self.assertEqual(re.findall(r'<(?:section|footer) id="([a-z-]+)"', page),
                         ["papers", "disagreements", "other", "lists", "grain", "two-rows", "footer"])
        self.assertNotRegex(page, r"<script|<link|https?:|src=")  # no script, stylesheet or font from anywhere
        header = section(page, "papers")
        self.assertIn("<td>5, 5 pass</td><td>6, 5 pass</td><td>not checked</td>", header)
        with open(os.path.join(self.lib, "checks", "pmid123.figures_agreement.json"), encoding="utf-8") as fh:
            m = json.load(fh)["measures"]
        self.assertIn("".join(f'<td class="n">{m[k]:.2f}</td>' for k, _ in papers.REVIEW["figures"]["measures"]),
                      header)
        self.assertEqual((m["evidence_agreement"], m["source_agreement"], m["chart_agreement"]), (0.6, 0.8, 0.8))
        grain = section(page, "grain")
        self.assertIn("the batch's median", grain)
        self.assertIn("No report of a figure reader is kept for these papers", grain)
        with open(os.path.join(papers.figures_folder(self.lib, "pmid123", 1), papers.REPORT), "w",
                  encoding="utf-8") as fh:
            fh.write("Fig. 2 b: a hard call <between> two kinds")
        grain = section(self.page(), "grain")  # a reader's report kept beside its rows is shown, escaped
        self.assertIn("a hard call &lt;between&gt; two kinds", grain)
        self.assertNotIn("No report of a figure reader is kept", grain)
        footer = section(page, "footer")
        self.assertIn(f"papers.py review {self.lib} --reading figures --out {self.out}", footer)
        brief = os.path.join(PLUGIN, "agents", "figure-reader.md")
        with open(brief, "rb") as fh:
            self.assertIn(hashlib.sha256(fh.read()).hexdigest(), footer)
        lists = json.dumps(papers.review_lists(self.lib, "figures", papers.FIGURE_LABELS_V2),
                           ensure_ascii=False).encode("utf-8")
        self.assertIn(hashlib.sha256(lists).hexdigest(), footer)

    def test_the_panels_read_differently_are_side_by_side_with_their_legend(self):
        part = section(self.page(), "disagreements")
        self.assertIn("3 places where a paper's two readings give a different evidence, source, chart, marks, targets "
                      "shown, colour or colour named by", part)
        cases = part.split('<div class="case">')[1:]
        self.assertEqual([re.search(r"<h4>(.*?)</h4>", c).group(1) for c in cases],
                         ["Fig. 1 b: evidence differs", "Fig. 1 c: chart differs",
                          "Extended Data Fig. 3 b: evidence and source differ"])
        b = cases[0]
        self.assertIn("(B and C) <mark>Boxplots of GENE1 expression", b)  # the legend's text for the panel, its
        self.assertNotIn("(D) Heatmap", b)                                 # quote marked, not the other panels'
        self.assertIn('<tr class="diff"><th>evidence</th><td>a target&#x27;s own gene</td></tr>', b)
        self.assertIn('<tr class="diff"><th>evidence</th><td>a program or protein per cell</td></tr>', b)
        self.assertEqual(b.count(f"<q>{QUOTE_1BC}</q>"), 2)  # both readings' evidence quotes
        self.assertIn("reading 1, row 2", b)
        self.assertIn("reading 2, row 2", b)
        self.assertIn("<li>one reading only | Fig. 2 all | reading 1</li>", part)  # the rest of agree's list
        self.assertNotIn("<li>evidence differs", part)

    def test_the_rows_under_other_are_grouped_by_their_words(self):
        part = section(self.page(), "other")
        self.assertIn("<h3>donor effects</h3>", part)
        self.assertIn("1 row in 1 paper; evidence", part)
        self.assertIn(f"<q>{QUOTE_3B}</q>", part)

    def test_each_closed_list_counts_its_values_in_panels_with_examples(self):
        part = section(self.page(), "lists")
        for field, values in papers.review_lists(self.lib, "figures").items():
            block = part.split(f"<h3>{field}</h3>")[1].split("</table>")[0]  # its table of counts
            names = re.findall(r"<tr><td>(?:<a [^>]*>)?([^<]+)", block)
            self.assertEqual(names, [html.escape(v) for v in values], field)  # every value, in the brief's order
        # a target's own gene: reading 1's row b-c (two panels), reading 2's row c; panel c both
        self.assertIn('a target&#x27;s own gene</a></td><td class="n">1</td><td class="n">2</td><td class="n">1</td>'
                      '<td class="n">1</td>', part)
        own = part.split("evidence: a target&#x27;s own gene</h4>")[1].split("<h4")[0]
        self.assertIn("<li><b>PMID123 Zhao 2026</b>, reading 1 row 2: Fig. 1 b-c; readout: GENE1 expression per cell. "
                      f"<q>{QUOTE_1BC}</q></li>", own)
        self.assertIn("<summary>all 2 rows</summary>", own)
        self.assertIn("evidence: other</h4>", part)

    def test_the_chart_list_shows_beside_each_value_the_panels_given_as_inferred(self):
        """Version 1's rows, whose charts may be inferred."""
        second = [version_1(r) for r in review_reading_2()]
        second[1] = second[1].replace("| box or violin |", "| box or violin (inferred) |")  # Fig. 1b
        self.write_reading(1, [version_1(r) for r in review_reading_1()])
        self.write_reading(2, second)
        papers.agree_figures(self.lib, "pmid123")
        part = section(self.page(), "lists")
        chart = part.split("<h3>chart</h3>")[1]
        self.assertIn("<th>panels inferred, reading 1</th><th>panels inferred, reading 2</th></tr>", chart)
        # box or violin: reading 1's row b-c, reading 2's row b, inferred; panel b alike
        self.assertIn('box or violin</a></td><td class="n">1</td><td class="n">2</td><td class="n">1</td>'
                      '<td class="n">1</td><td class="n">0</td><td class="n">1</td></tr>', chart)
        self.assertIn("chart: box or violin</h4><p class=\"muted\">1 paper; panels: reading 1 2; reading 2 1; both 1; "
                      "inferred: reading 1 0; reading 2 1</p>", chart)
        self.assertNotIn("(inferred) (not in the list)", chart)
        self.assertNotIn("inferred, reading", part.split("<h3>chart</h3>")[0])  # the evidence and the source

    def test_two_rows_per_paper_stand_beside_their_legend_and_results_sentences(self):
        part = section(self.page(), "two-rows")
        self.assertEqual(re.findall(r"<h4>(.*?)</h4>", part), ["reading 1, row 1: Fig. 1 a",
                                                               "reading 1, row 3: Fig. 2 all"])
        self.assertIn(f"(A) <mark>{QUOTE_1A}</mark>", part)
        self.assertIn('<div class="said">We screened 20 regulators', part)  # the Results sentence citing Fig. 1a,
        self.assertNotIn("# Results", part)                                 # without the cut file's headings

    def test_the_papers_text_is_escaped(self):
        page = self.page()
        self.assertIn(html.escape(QUOTE_2_MARKUP), page)
        self.assertIn("donors &lt;script&gt;x&lt;/script&gt; &amp; more.", section(page, "two-rows"))  # the legend
        self.assertNotIn("<script>", page)

    def test_the_same_rows_give_the_same_page_but_for_the_time(self):
        first, second = self.page(written=time.gmtime(0)), self.page(written=time.gmtime(86400))
        self.assertNotEqual(first, second)

        def untimed(page):
            return re.sub(r"<time [^>]*>.*?</time>", "", page)
        self.assertEqual(untimed(first), untimed(second))
        self.assertEqual(untimed(self.page()), untimed(first))

    def test_rows_changed_after_agree_are_refused(self):
        self.write_reading(2, review_reading_2()[:-1])
        with self.assertRaises(papers.LibraryError) as e:
            self.page()
        self.assertIn(f"run papers.py agree {self.lib} pmid123 --reading figures again", str(e.exception))

    def test_rows_changed_after_agree_in_a_field_agree_does_not_measure_are_refused(self):
        self.write_reading(1, [r.replace("GENE1 expression per cell", "SOMETHING ELSE") for r in review_reading_1()])
        with self.assertRaises(papers.LibraryError) as e:
            self.page()
        self.assertIn("rows/pmid123/figures_1/figures.md changed after `agree`", str(e.exception))
        self.write_reading(1, review_reading_1())
        path = os.path.join(self.lib, "checks", "pmid123.figures_agreement.json")
        with open(path, encoding="utf-8") as fh:
            stored = json.load(fh)
        self.assertEqual(set(stored["read"]), {f"rows/pmid123/figures_{n}/figures.md" for n in (1, 2)})
        self.page()  # the same rows again
        del stored["read"]
        papers.write_text(path, json.dumps(stored))
        with self.assertRaises(papers.LibraryError) as e:
            self.page()
        self.assertIn("does not record the rows `agree` read", str(e.exception))

    def test_the_page_takes_the_place_of_no_file_of_the_library(self):
        tsv = os.path.join(self.lib, "papers.tsv")
        before = papers.read_text(tsv)
        other = os.path.join(self.lib, "notes.html")
        papers.write_text(other, "<p>the user's notes</p>\n")
        for out, why in ((tsv, "is not one"), (os.path.join(self.lib, "checks", "page.html"), "checks/ folder"),
                         (os.path.join(self.lib, "rows", "pmid123", "page.html"), "rows/ folder"),
                         (other, "not an earlier review page")):
            with self.subTest(out):
                with self.assertRaises(papers.LibraryError) as e:
                    papers.review(self.lib, "figures", out=out)
                self.assertIn(why, str(e.exception))
        self.assertEqual((papers.read_text(tsv), papers.read_text(other)), (before, "<p>the user's notes</p>\n"))
        self.assertFalse(os.path.exists(os.path.join(self.lib, "checks", "page.html")))
        self.page()
        self.page()  # an earlier review page is written again
        link = os.path.join(self.lib, "linked.html")
        try:
            os.symlink(os.path.join(self.tmp.name, "outside.html"), link)
        except (OSError, NotImplementedError):
            return  # links need a privilege this system does not give
        with self.assertRaises(papers.LibraryError) as e:
            papers.review(self.lib, "figures", out=link)
        self.assertIn("written inside the library", str(e.exception))
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "outside.html")))

    def test_both_counts_the_panels_two_readings_give_alike_and_no_shared_panel_shows_none(self):
        # reading 1 gives Fig. 1b a second row, of reading 2's evidence: agree no longer counts panel b alike
        extra = figure_row(panel="b", evidence="a program or protein per cell", source="the screen",
                           chart="box or violin", quote=QUOTE_1BC)
        self.write_reading(1, review_reading_1()[:2] + [extra] + review_reading_1()[2:])
        papers.agree_figures(self.lib, "pmid123")
        part = section(self.page(), "lists")
        self.assertIn('a program or protein per cell</a></td><td class="n">1</td><td class="n">1</td>'
                      '<td class="n">1</td><td class="n">0</td>', part)
        self.write_reading(1, review_reading_1()[:2])
        self.write_reading(2, review_reading_2()[3:])
        papers.agree_figures(self.lib, "pmid123")
        self.assertIn('<td class="n">0.00</td>' + '<td class="n">none</td>' * 4, section(self.page(), "papers"))

    def test_a_paper_read_once_is_reviewed_from_its_check(self):
        papers.check(self.lib, "pmid123", figure_reading=1)
        os.remove(os.path.join(self.lib, "checks", "pmid123.figures_agreement.json"))
        page = self.page()
        self.assertIn("<td>5, 5 pass</td><td>-</td><td>reading 1</td><td class=\"n\">read once</td>",
                      section(page, "papers"))
        self.assertIn("0 places where", section(page, "disagreements"))
        self.write_reading(1, review_reading_1()[:-1])
        with self.assertRaises(papers.LibraryError) as e:
            self.page()
        self.assertIn("its rows changed after the check; check it again", str(e.exception))

    def test_the_page_stays_in_the_library_and_the_command_reaches_it(self):
        outside = os.path.join(self.tmp.name, "review.html")
        with self.assertRaises(papers.LibraryError) as e:
            papers.review(self.lib, "figures", out=outside)
        self.assertIn("written inside the library", str(e.exception))
        self.assertFalse(os.path.exists(outside))
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(papers.main(["review", self.lib, "--reading", "figures", "--keys", "pmid123",
                                          "--out", self.out]), 0)
        self.assertIn("keep it in the library, out of any public place", printed.getvalue())
        self.assertIn("--keys pmid123", papers.read_text(self.out))
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(papers.main(["review", self.lib, "--reading", "figures", "--out", outside]), 1)
            self.assertEqual(papers.main(["review", self.lib, "--reading", "figures", "--keys", "pmid999",
                                          "--out", self.out]), 1)
        with contextlib.redirect_stdout(io.StringIO()) as printed, self.assertRaises(SystemExit):
            papers.main(["review", "--help"])
        self.assertIn("inside the library", " ".join(printed.getvalue().split()))


class TestTheDraftCheck(Library):
    def test_every_sentence_has_sources_that_are_rows_of_the_catalogue(self):
        self.cut_paper()
        papers.write_module_list(self.lib)
        self.write_rows("moves", GOOD + "\n")
        papers.check(self.lib, "pmid123")
        papers.merge(self.lib)
        draft = os.path.join(self.lib, "drafts", "pitfall.md")
        papers.write_text(draft, "\n".join([
            "## PF20. A target's many changed genes",
            "",
            f"- **If you see:** a target that changes 120 genes [{LABEL}, Fig. 2a].",
            "- **Consider:** whether the control guides share them. Screens rarely say so.",
            f"- **Read it as:** a broad effect [{LABEL}, Fig. 5c].",
            "- **Also:** the same pattern [PMID999 Keptout 2025, Fig. 1a].",
            "- **Stop or turn when:**",
            f"  - the panel as the row gives it [{LABEL}, Fig. 2a].",
            "  - A shift in the shares is a finding of its own [Pertura's reading].",
            "- **Consider:** an adjustment of our own [Pertura's reading]."]))
        out = papers.check_draft(self.lib, draft)
        self.assertIn("no source: **Consider:** whether the control guides share them.", out)
        self.assertIn("no source: Screens rarely say so.", out)
        self.assertIn(f"no row of {LABEL} names Fig. 5c: [{LABEL}, Fig. 5c]", out)
        self.assertIn("cites a source kept out: 999", out)
        self.assertIn("Pertura's reading, for the reviewer: A shift in the shares is a finding of its own [...].", out)
        self.assertIn("Pertura's reading outside the field the form gives it (consider): **Consider:** an adjustment "
                      "of our own [...].", out)
        self.assertFalse(any("If you see" in line or "Stop or turn" in line or "as the row gives it" in line
                             for line in out))


class TestTheParts(unittest.TestCase):
    def test_a_panel_names_its_figures(self):
        self.assertEqual(papers.figures("Fig. 1D, S2A-C"), [("main", 1), ("supp", 2)])
        self.assertEqual(papers.figures("Fig. 4E-G, Fig. S6"), [("main", 4), ("supp", 6)])
        self.assertEqual(papers.figures("Extended Data Fig. 4c-f"), [("ext", 4)])
        self.assertEqual(papers.figures("Ext. Data Fig. 4a"), [("ext", 4)])
        self.assertEqual(papers.figures("Figs. 2 and 3"), [("main", 2), ("main", 3)])
        self.assertEqual(papers.figures("Supplementary Fig. 5b"), [("supp", 5)])
        self.assertEqual(papers.figures("Table 2"), [])
        text = papers.normal("Lines were screened (Supplementary Figures 1, 2) and in Extended Data Fig. 3.")
        self.assertFalse(papers.figure_in("main", 1, text))
        self.assertFalse(papers.figure_in("main", 3, text))
        self.assertTrue(papers.figure_in("ext", 3, text))
        found = papers.sentences_with("Cells changed (Fig. 5a). Pairs agreed (Fig. 5b). Supplementary Fig. 5 shows it.",
                                      "Fig. 5b")
        self.assertEqual(found, ["Pairs agreed (Fig. 5b).", "Cells changed (Fig. 5a)."])

    def test_a_panel_s_tokens_for_matching_read_extended_data_without_fig_as_a_figure(self):
        self.assertEqual(papers.panel_tokens("Fig. 3c-e"), {("main", 3, "c"), ("main", 3, "d"), ("main", 3, "e")})
        self.assertEqual(papers.panel_tokens("Extended Data Fig. 2a,b"), {("ext", 2, "a"), ("ext", 2, "b")})
        self.assertEqual(papers.panel_tokens("Extended Data 2a-d"), {("ext", 2, x) for x in "abcd"})
        self.assertEqual(papers.panel_tokens("ED 3b"), {("ext", 3, "b")})
        self.assertEqual(papers.panel_tokens("fig. S5G-K"), {("supp", 5, x) for x in "ghijk"})
        self.assertEqual(papers.panel_tokens("Fig. 4E-G, Fig. S6"), {("main", 4, "e"), ("main", 4, "f"),
                                                                     ("main", 4, "g"), ("supp", 6, "*")})
        self.assertEqual(papers.panel_tokens("Figs. 1d and 2a"), {("main", 1, "d"), ("main", 2, "a")})
        self.assertEqual(papers.panel_tokens("data S2, data S3"), set())
        self.assertTrue(papers.tokens_overlap({("main", 3, "*")}, {("main", 3, "c")})[0])
        self.assertFalse(papers.tokens_overlap({("main", 3, "c")}, {("main", 4, "c")})[0])

    def test_rows_match_by_panel_and_content_words_and_the_measures_follow(self):
        def row(panel, question, module, relation, kind="other"):
            return {"panel": panel, "question": question, "analysis": question, "kind": kind, "module": module,
                    "relation": relation}
        readings = {1: [row("Fig. 2a", "mixscape classifies knockout escaping cells per target", "B.escape", "default"),
                        row("Fig. 3b", "linear discriminant embedding separates perturbation classes",
                            "C.states_and_composition", "method")],
                    2: [row("Fig. 2a-b", "mixscape classifies escaping cells for each target", "B.escape", "default"),
                        row("Fig. 3b", "embedding by linear discriminant analysis separates classes of perturbation",
                            "E.similar_targets", "method", "other (a tool)"),
                        row("Fig. 5c", "an unrelated count of reads", "none", "none")]}
        items, m = papers.agreement(readings)
        self.assertEqual(items, [{1: 0, 2: 0}, {1: 1, 2: 1}, {2: 2}])
        self.assertEqual(m["items_by_support"], {1: 1, 2: 2})
        self.assertEqual((m["row_agreement"], m["module_agreement"], m["relation_agreement"]), (0.8, 0.5, 1.0))
        self.assertEqual((m["kind_agreement"], m["kind_agreement_as_written"]), (1.0, 0.5))
        self.assertEqual(papers.match({1: [row("Fig. 2a", "mixscape classifies cells", "x", "y")],
                                       2: [row("Fig. 2a", "an unrelated count of reads", "x", "y")]}),
                         [{1: 0}, {2: 0}])  # a shared panel alone does not match

    def test_a_parenthesised_panel_range_keeps_all_its_panels_and_their_shared_text(self):
        text = ("# Figure and supplementary legends\n"
                "Figure 1. A finding. (A)-(C) Shared expression comparison. (D) A second comparison.\n")
        leg = papers.figure_legends(text)[("main", 1)]
        self.assertEqual(leg["letters"], set("abcd"))
        self.assertIn("Shared expression comparison", papers.legend_part(leg, "b"))
        self.assertNotIn("A second comparison", papers.legend_part(leg, "b"))

    def test_the_figure_legends_and_panels_are_read_as_the_pilot_read_them(self):
        """The figure reading's pilot's own self-test (analysis/figure_reading/check_figure_rows.py), on the port."""
        results = ("# Results\n\nWe did a screen (Fig. 1a).\n\n# Figure and supplementary legends\n\n"
                   "- Figure 1.. The screen finds regulators. a) Experimental design schematic of the whole screen. "
                   "b) Expression of PD-L1 protein in stimulated cells as measured by flow cytometry and (c) CITE-seq. "
                   "d-e) Heatmaps of the z-scored expression of the genes.\n"
                   "- Fig. 2.. Dose-response relationships. (A) Relationship between dosage and impact across all of "
                   "the targets. (B and C) Boxplots of the scores of every target in the screen. (F‐G) UMAP plots.\n"
                   "- Extended Data Fig. 3. A single panel legend with no letters at all in it.\n"
                   "Figure S4: Another legend. (A–B) Bars of the shares of cells in each state.\n"
                   "Table S1: Clinical features.\n")
        legs = papers.figure_legends(results)
        self.assertEqual({papers.figure_name(f): "".join(sorted(v["letters"])) for f, v in legs.items()},
                         {"Fig. 1": "abcde", "Fig. 2": "abcfg", "Extended Data Fig. 3": "", "Fig. S4": "ab"})
        self.assertEqual(papers.normal(legs[("main", 2)]["title"]).strip(), "dose-response relationships.")
        self.assertEqual((papers.row_letters("b-c"), papers.row_letters("F, G"), papers.row_letters("b (left)")),
                         ({"b", "c"}, {"f", "g"}, {"b"}))
        self.assertEqual((papers.row_letters("all"), papers.row_letters("b2")), ({"all"}, None))

        def reading(*rows):
            passed = [{"fig": fig, "letters": set(letters), "kind": kind, "chart": chart, "row": i}
                      for i, (fig, letters, kind, chart) in enumerate(rows, 1)]
            return {"passed": passed, "conclusions": {}, "counts": {
                "rows": len(rows), "quotes_found": len(rows), "figures_with_one_conclusion_found": 0,
                "figures_read": 0}}
        two = {1: reading((("main", 2), "bc", "effects across many targets", "box or violin"),
                          (("main", 2), "a", "other (dose)", "line or curve")),
               2: reading((("main", 2), "b", "effects across many targets", "box or violin"),
                          (("main", 2), "c", "effects across many targets", "bar per group"),
                          (("main", 2), "a", "other (titration)", "line or curve"))}
        m, units = papers.figure_agreement(two, legs, papers.FIGURE_LABELS_FIRST_VERSION)
        self.assertEqual((m["units_covered_by_both"], m["kind_agreement"]), (3, 1.0))
        self.assertEqual((m["kind_agreement_with_other_words"], m["chart_agreement"]), (round(2 / 3, 3),
                                                                                       round(2 / 3, 3)))
        self.assertEqual(m["kind_and_chart_agreement"], round(2 / 3, 3))

    def test_the_legends_read_other_journals_panel_markers_and_labels(self):
        """The review's probes: markers glued to a word, in another case or numbered in roman, inside a sentence and
        out of sequence; Nature's "a," and an Oxford comma; captions labelled "ED Fig.", "Figure EV" and eLife's figure
        supplements, each a legend of its own."""
        def legends(*lines):
            legs = papers.figure_legends("# Results\n\nText.\n\n# Figure and supplementary legends\n\n" +
                                         "\n".join(lines) + "\n")
            return {papers.figure_name(f): ("".join(sorted(v["letters"])), v["title"].strip()) for f, v in legs.items()}
        cases = [
            (["- Figure 2. Effects of guide(s) on cells. a) Schematic of the screen. b) UMAP of cells."],
             {"Fig. 2": ("ab", "Effects of guide(s) on cells.")}),
            (["- Figure 2. GENE1 represses GENE2. a, Schematic of the screen. b, UMAP of cells. c, Heatmap."],
             {"Fig. 2": ("abc", "GENE1 represses GENE2.")}),
            (["- Figure 3. X. (A) Schematic. (B, C, and D) Boxplots of scores. (E) UMAP."],
             {"Fig. 3": ("abcde", "X.")}),
            (["- Extended Data Fig. 2. A rich dataset. a. Expression of the targets. b. The workflow. c–d. UMAP of "
              "markers (c) or genes (d). e. Counts per cell type."],  # Nature's Extended Data style
             {"Extended Data Fig. 2": ("abcde", "A rich dataset.")}),
            (["- Figure 1. Main legend. (A) Schematic. (B) UMAP. (C) Heatmap. (D) Bars.",
              "- Figure 1—figure supplement 1. Supplement. (A) QC."], {"Fig. 1": ("abcd", "Main legend.")}),
            (["- Figure 4. Differentiation. a) Expression of brachyury (T) in cells. b) UMAP. c) Bars."],
             {"Fig. 4": ("abc", "Differentiation.")}),
            (["- Figure 5. Title. (a) Schematic of steps (i) to (iii). (b) UMAP."], {"Fig. 5": ("ab", "Title.")}),
            (["- Figure 1. The model (A) Overview. (B) Fit to the design matrix (X). (C) The fit Y=f(X)."],
             {"Fig. 1": ("abc", "The model")}),
            (["- Figure 6. Title. (a) Schematic. (b) UMAP.", "- ED Fig. 1. QC. (a) Cells. (c) Depth. (h) Reads."],
             {"Fig. 6": ("ab", "Title."), "Extended Data Fig. 1": ("ach", "QC.")}),
            (["- Figure 3. Main. (A) Schematic. (B) UMAP.", "- Figure EV1. Expanded view. (A) Cells per guide. (C) "
              "Depth. (F) Reads.", "- Figure 4. Number of guide(s) per cell in the screen across all donors."],
             {"Fig. 3": ("ab", "Main."), "Extended Data Fig. 1": ("acf", "Expanded view."),
              "Fig. 4": ("", "Number of guide(s) per cell in the screen across all donors.")}),
            (["- Figure 2. Title. (A) UMAP.", "- Supplementary Table S1. Guides. (B) Their targets."],
             {"Fig. 2": ("a", "Title.")})]
        for lines, want in cases:
            with self.subTest(lines[0][:40]):
                self.assertEqual(legends(*lines), want)
        for name, want in (("Figure EV3", [("ext", 3)]), ("Fig. ED3", [("ext", 3)]), ("ED Fig. 3", [("ext", 3)]),
                           ("Figure 1—figure supplement 1", []), ("Fig. 3 and ED Fig. 2", [("main", 3), ("ext", 2)])):
            self.assertEqual(papers.figures(name), want, name)
        text = papers.normal("Cells were sorted (Figure EV3b) and counted (Fig. 3a), as described in Fig. 4.")
        self.assertTrue(papers.figure_in("ext", 3, text))
        self.assertTrue(papers.figure_in("main", 4, text))  # "described " is not "ED "
        self.assertFalse(papers.figure_in("main", 3, papers.normal("as in Figure EV3 and ED Fig. 3")))

    def test_plain_upper_case_panel_markers_keep_cross_references_inside_their_panel(self):
        text = ("# Figure and supplementary legends\n"
                "Fig. 6. A GWAS association at the first locus. B Expression in targeted cells. "
                "C eQTL associations at that locus. D Colocalization of (A) and (C). "
                "E GWAS associations at another locus. F Same expression comparison as B. "
                "G eQTL associations. H Colocalization of (E) and (G). "
                "I GWAS associations at the final locus. J Expression at that locus.\n")
        leg = papers.figure_legends(text)[("main", 6)]
        self.assertEqual(leg["letters"], set("abcdefghij"))
        self.assertIn("Colocalization of (A) and (C)", papers.legend_part(leg, "d"))
        self.assertNotIn("another locus", papers.legend_part(leg, "d"))

    def test_plain_lower_case_markers_keep_shared_panels_and_ignore_inline_references(self):
        text = ("# Figure and supplementary legends\n"
                "Fig. 2. Targeting a regulator changes the phenotype. a Workflow. "
                "b FACS measurements (n = 6). c Experimental workflow. d, g Photos of tumours. "
                "e, h Tumour volumes as in (d) and (g). f, i Tumour weights as in (e) and (h). "
                "j Labeled cells were transferred. k Results from the same mice. l Quantification.\n")
        leg = papers.figure_legends(text)[("main", 2)]
        self.assertEqual(leg["letters"], set("abcdefghijkl"))
        self.assertEqual(leg["title"].strip(), "Targeting a regulator changes the phenotype.")
        self.assertIn("Photos of tumours", papers.legend_part(leg, "g"))
        self.assertNotIn("Tumour volumes", papers.legend_part(leg, "g"))

    def test_plain_panel_detection_does_not_turn_prose_or_a_title_into_panels(self):
        for body in ("A single panel describing B cells and T cells.",
                     "A GWAS association was observed. B cells were then tested."):
            with self.subTest(body=body):
                leg = papers.figure_legends("# Figure and supplementary legends\nFig. 1. " + body)[("main", 1)]
                self.assertEqual(leg["letters"], set())
        leg = papers.figure_legends("# Figure and supplementary legends\n"
                                   "Fig. 1. A GWAS comparison. a UMAP of cells. b Counts.")[("main", 1)]
        self.assertEqual(leg["letters"], set("ab"))
        self.assertEqual(leg["title"].strip(), "A GWAS comparison.")

    def test_a_period_marker_is_a_lower_case_letter_naming_the_next_panel_or_one_named(self):
        """The review of Nature's "a." markers: a letter and a period in a name, an initial or an abbreviation adds no
        panel; a marker where no sentence ends before it, or where the legend's text begins, is a panel's."""
        def legends(line):
            legs = papers.figure_legends("# Figure and supplementary legends\n" + line + "\n")
            return {papers.figure_name(f): ("".join(sorted(v["letters"])), v["title"].strip()) for f, v in legs.items()}
        cases = [
            ("- Figure 1. Title. (A) Growth curves. E. coli strains were used. (B) Counts.", ("ab", "Title.")),
            ("- Figure 1. Title. (A) Schematic. Gift of Dr. J. D. Smith. (B) UMAP.", ("ab", "Title.")),
            ("- Figure 1. Title. (A) Arms: A. control; B. drug. (C) UMAP.", ("ac", "Title.")),
            ("- Extended Data Fig. 1. Title. a. Markers; i. e. CD4. b. UMAP.", ("ab", "Title.")),
            ("- Extended Data Fig. 1. Title. a. Bars. n. s., not significant. b. UMAP.", ("ab", "Title.")),
            ("- Extended Data Fig. 1. Title. a. Scores as in ref. 5. c. Counts. b. Bars.", ("ab", "Title.")),
            ("- Extended Data Fig. 4. Title. a. UMAP. b. Bars. c. Scores after batch correction d. Gene programme "
             "scores per cell.", ("abcd", "Title.")),
            ("- Extended Data Fig. 1. Title. a. UMAP (n = 3) b. Bars.", ("ab", "Title.")),
            ("- Extended Data Fig. 2. a. UMAP of cells (n = 3). b. Counts. c. Bars.", ("abc", "")),
            ("- Extended Data Fig. 1. X. a. UMAP. b. Bars.", ("ab", "X.")),  # a title "X." is no panel
            ("- Extended Data Fig. 1. Title. a and b. UMAP. c. Bars.", ("abc", "Title."))]
        for line, want in cases:
            with self.subTest(line[:60]):
                (got,) = legends(line).values()
                self.assertEqual(got, want)

    def test_a_quote_has_8_to_40_words_no_ellipsis_and_is_in_the_text(self):
        text = papers.normal("Cells that ‘escape’ perturbation [19,21] were removed before the test of each "
                             "target.")
        self.assertEqual(papers.quote_fails("evidence", "Cells that 'escape' perturbation were removed before the test",
                                            text), [])
        self.assertEqual(papers.quote_fails("evidence", "Cells that escape", text),
                         ["evidence has 3 words, not 8 to 40"])
        self.assertEqual(papers.quote_fails("evidence", " ".join(["word"] * 41), text),
                         ["evidence has 41 words, not 8 to 40"])
        self.assertEqual(papers.quote_fails("evidence", "Cells that 'escape' perturbation … removed before the test",
                                            text), ["evidence holds an ellipsis"])

    def test_numbers_are_read_in_the_paper_s_notations(self):
        text = papers.normal("p = 2.2 × 10−16, r = 0.31 and 1,009 moves")
        self.assertEqual(papers.numbers("p<2.2e-16, r=0.31, 1,009 moves, 62%"), ["2.2e-16", "0.31", "1,009", "62"])
        for t in ("2.2e-16", "0.31", "1,009"):
            self.assertTrue(papers.number_in(t, text), t)
        self.assertFalse(papers.number_in("0.3", text))

    def test_a_reason_is_not_stated_a_quote_of_the_text_or_mostly_its_words(self):
        text = papers.normal("We chose GENE1 because it had the most changed genes among the regulators. Cells that "
                             "‘escape’ perturbation [19,21] were removed.")
        self.assertEqual(papers.reason_in("not stated", text), "")
        self.assertEqual(papers.reason_in('"it had the most changed genes"', text), "")
        self.assertEqual(papers.reason_in("\"Cells that 'escape' perturbation were removed\"", text), "")
        self.assertIn("quote not in the text", papers.reason_in('"the strongest hit"', text))
        self.assertEqual(papers.reason_in("the most changed genes among regulators", text), "")
        self.assertIn("mostly not", papers.reason_in("a reviewer asked for an orthogonal validation", text))

    def test_a_methods_row_may_name_several_sections(self):
        self.assertEqual(papers.sections_of("Results: Mixscape classifies cells; Figure and supplementary legends "
                                            "(Extended Data Fig. 2a-b)"),
                         ["Mixscape classifies cells", "Figure and supplementary legends"])
        self.assertEqual(papers.sections_of("not stated"), [])

    def test_code_is_unpacked_inside_its_folder_and_without_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = os.path.join(tmp, "code.zip")
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("repo-abc/analysis.py", "x = 1\n")
                z.writestr("repo-abc/Snakefile", "rule all:\n")
                z.writestr("../evil.py", "import os\n")
                z.writestr("/abs.py", "import os\n")
                z.writestr("repo-abc/data/counts.h5ad", "binary")
                z.writestr("repo-abc/big.txt", "x" * (papers.CODE_FILE_MAX + 1))
            dest = os.path.join(tmp, "code")
            self.assertEqual(papers.unpack(archive, dest), 2)
            kept = sorted(os.path.relpath(os.path.join(d, f), dest).replace(os.sep, "/")
                          for d, _, fs in os.walk(dest) for f in fs)
            self.assertEqual(kept, ["repo-abc/Snakefile", "repo-abc/analysis.py"])
            self.assertFalse(os.path.exists(os.path.join(tmp, "evil.py")))

    def test_repositories_and_names_are_read_from_text(self):
        text = "Code: https://github.com/lab/screen-code.git, and doi:10.5281/zenodo.1234567; zenodo.org/records/42."
        self.assertEqual(papers.repositories(text), ["https://github.com/lab/screen-code",
                                                     "https://zenodo.org/records/1234567",
                                                     "https://zenodo.org/records/42"])
        self.assertEqual([papers.last_name(a) for a in ("Zhao Y", "Van Dijk MA", "de la Cruz A", "Wang")],
                         ["Zhao", "Van Dijk", "de la Cruz", "Wang"])


def listed(text, field):
    lines = text.split("\n")
    start = next(i for i, line in enumerate(lines) if line.startswith(f"- **{field}:**"))
    items = []
    for line in lines[start + 1:]:
        if not line.startswith("  - "):
            break
        items.append(papers.label_of(line[4:].strip()))
    return items


def field_text(text, field):
    """A field's text in a brief, from its line to the next field's."""
    return re.search(rf"^- \*\*{field}:\*\*(.*?)(?=^- \*\*|^\S|\Z)", text, re.M | re.S).group(1)


class TestTheScriptsAndTheBriefsAgree(unittest.TestCase):
    def brief(self, name):
        return papers.read_text(os.path.join(PLUGIN, "agents", f"{name}.md"))

    def test_the_labels_are_the_briefs_labels(self):
        self.assertEqual(tuple(listed(self.brief("moves-reader"), "kind")), papers.KINDS)
        self.assertEqual(tuple(listed(self.brief("methods-reader"), "step")), papers.STEPS)
        self.assertEqual(tuple(listed(self.brief("code-reader"), "step")), papers.STEPS)
        data = re.search(r"- \*\*data:\*\* exactly one of (.*?)\n- \*\*", self.brief("chain-reader"), re.S).group(1)
        self.assertEqual(tuple(re.findall(r"`([^`]+)`", data)), papers.DATA)
        self.assertEqual(tuple(re.findall(r"`([^`]+)`", re.search(r"\*\*as:\*\*(.*?)\n- ", self.brief(
            "methods-reader"), re.S).group(1))), papers.AS)

    def test_the_moves_reader_s_units_relations_and_design_are_the_scripts(self):
        brief = self.brief("moves-reader")
        unit = field_text(brief, "unit")
        self.assertEqual(tuple(re.findall(r"`([^`]+)`", unit.split(":")[0])), papers.UNITS)
        self.assertEqual(tuple(re.findall(r"^  - `([^`]+)`", field_text(brief, "relation"), re.M)), papers.RELATIONS)
        design = brief.split("## The design")[1].split("\n## ")[0]
        listed_design = {m.group(1): tuple(v.strip() for v in m.group(2).split(" | "))
                         for m in re.finditer(r"^- ([a-z ]+): (.+)$", design, re.M)}
        self.assertEqual(listed_design, papers.DESIGN)
        for name in ("moves-reader", "moves-verifier"):
            self.assertIn(QUOTE_WORDS_TEXT, self.brief(name), name)
        self.assertIn("sixteen fields", self.brief("moves-verifier"))
        self.assertEqual(len(papers.FIELDS["moves"]), 16)

    def test_the_verifier_s_verdicts_are_the_scripts(self):
        brief = self.brief("moves-verifier")
        row = re.search(r"`(\| [a-z_ |]+ \|)`", brief).group(1)
        self.assertEqual(tuple(f.strip() for f in row.strip("|").split("|")), papers.VERDICT_FIELDS)
        self.assertEqual(tuple(re.findall(r"`([^`]+)`", field_text(brief, "verdict"))), papers.VERDICTS)
        self.assertIn("`missed:`", brief)

    def test_the_figure_reader_s_lists_are_the_scripts(self):
        brief = self.brief("figure-reader")

        def part(head):
            return brief.split(f"\n## {head}\n", 1)[1].split("\n## ", 1)[0]

        def section(head):  # a list's items are a label and its colon; "Where two kinds meet" names labels too
            return tuple(papers.label_of(m.group(1)) for m in re.finditer(r"^- `([^`]+)`:", part(head), re.M))
        self.assertEqual(section("The evidence"), papers.FIGURE_EVIDENCE)
        self.assertEqual(section("The sources"), papers.FIGURE_SOURCES)
        self.assertEqual(section("The charts"), papers.FIGURE_CHARTS)
        self.assertIn("Take the first kind in this order", part("The evidence"))
        self.assertEqual(section("The marks"), papers.FIGURE_MARKS)
        self.assertEqual(section("The targets shown"), papers.FIGURE_TARGETS)
        self.assertEqual(section("The colour"), papers.FIGURE_COLOURS)
        self.assertEqual(section("Where the colour is named"), papers.FIGURE_COLOUR_NAMED_BY)
        self.assertIn("Never infer a chart", part("The charts"))
        self.assertNotIn(papers.INFERRED.strip(), brief)  # the chart is read from the image
        self.assertIn("`sources/<key>/figures.tsv`", brief)
        self.assertIn(QUOTE_WORDS_TEXT, field_text(brief, "evidence_quote"))
        words = papers.CONCLUSION_WORDS
        self.assertIn(f"{words[0]} to {words[1]} words", field_text(brief, "conclusion_quote"))
        self.assertIn("`rows/<key>/figures_<n>/`", brief)
        self.assertEqual(len(papers.FIELDS["figures"]), 16)
        # what the brief says code drops is what check_figures_reading drops: a row that fails any check
        self.assertIn("a row that fails any check is dropped", " ".join(brief.split()))
        self.assertNotIn("a row whose evidence_quote is not found is dropped", " ".join(brief.split()))

    def test_the_figures_evidence_and_source_split_the_first_version_s_kind(self):
        """The evidence is the first version's kinds without the three that are now sources, in their order and with
        "program" for "programme", with five added: gene programs found just before a program per cell, a trajectory
        just before states, nearby genes (cis) just before one target's effect, and a model fitted to the screen and a
        phenotype just before other; those three are sources (an independent experiment as a follow-up experiment)."""
        now_sources = ("an independent experiment", "the paper's other data", "outside data")
        kept = tuple(k.replace("programme", "program") for k in papers.FIGURE_KINDS_FIRST_VERSION
                     if k not in now_sources)
        added = ("gene programs or modules found", "a trajectory or pseudotime", "a target's effect on nearby genes (cis)",
                 "a model fitted to the screen", "a phenotype")
        evidence = papers.FIGURE_EVIDENCE
        self.assertEqual(tuple(k for k in evidence if k not in added), kept)
        self.assertEqual(evidence[evidence.index(added[0]) + 1], "a program or protein per cell")
        self.assertEqual(evidence[evidence.index(added[1]) + 1], "states or composition")
        self.assertEqual(evidence[evidence.index(added[2]) + 1], "one target's effect on genes")
        self.assertEqual(evidence[-3:], added[3:] + ("other",))
        self.assertLessEqual({"the paper's other data", "outside data", "a follow-up experiment"},
                             set(papers.FIGURE_SOURCES))
        first = list(papers.FIGURES_V1)
        first[4:6] = ["kind"]
        self.assertEqual(tuple(first), papers.FIGURES_FIRST_VERSION)

    def test_the_row_formats_are_the_briefs_formats(self):
        for name, reading in (("moves-reader", "moves"), ("chain-reader", "chain"), ("methods-reader", "methods"),
                              ("code-reader", "code"), ("figure-reader", "figures")):
            row = re.search(r"`(\| [a-z_ |]+ \|)`", self.brief(name)).group(1)
            self.assertEqual(tuple(f.strip() for f in row.strip("|").split("|")), papers.FIELDS[reading], name)

    def test_the_figure_reader_s_brief_and_lists_are_a_fixed_version(self):
        """A change to the figure reader's brief or lists is recorded as a new version in VERSIONS, or no reading
        starts and no row merges."""
        self.assertEqual(papers.version_of("figures")["version"], "v2")
        self.assertEqual(set(papers.BRIEFS), set(papers.READINGS))
        for reading, names in papers.BRIEFS.items():
            for name in names:
                self.assertTrue(os.path.exists(os.path.join(PLUGIN, "agents", name)), name)

    def test_the_module_list_gives_what_the_briefs_say_it_gives(self):
        for name in ("moves-reader", "moves-verifier"):
            self.assertIn("`module_list.md`", self.brief(name), name)
        given = self.brief("moves-reader").split("## A follow-up move")[0]
        for part in ("what it is for", "what it returns", "its choices where the"):
            self.assertIn(part, given)
            self.assertIn(part, papers.MODULE_LIST_INTRO)


IMAGES_JATS = """<article xmlns:xlink="http://www.w3.org/1999/xlink"><body><sec><title>Results</title><p>Text.</p>
<fig id="F1"><label>Figure 1.</label><caption><p>One.</p></caption><graphic xlink:href="paper-f0001.jpg"/></fig>
<fig id="F2"><label>Figure 2</label><caption><p>Two.</p></caption><graphic xlink:href="paper-f0002"/></fig>
<fig id="E1"><label>Extended Data Fig. 1</label><caption><p>More.</p></caption><graphic xlink:href="paper-e0001"/></fig>
</sec></body></article>"""
OPEN_DATA_LISTING = (b"<ListBucketResult><Contents><Key>metadata/PMC123.1.json</Key></Contents>"
                     b"<Contents><Key>metadata/PMC123.2.json</Key></Contents></ListBucketResult>")
IMAGE_BYTES = {"paper-f0001.jpg": b"one", "paper-f0002.jpg": b"two"}


class TestTheMainFiguresImages(Library):
    """The figures reading's second version reads each main figure from its image: the images come from PMC's open
    data, each checked against its md5, with a list of each main figure's file, which the rows' image is checked
    against."""

    def setUp(self):
        super().setUp()
        import hashlib as h
        papers.register(self.lib, ["123"])
        self.folder = os.path.join(self.lib, "sources", "pmid123")
        os.makedirs(self.folder, exist_ok=True)
        papers.write_text(os.path.join(self.folder, "fulltext.xml"), IMAGES_JATS)
        md5 = {k: h.md5(v).hexdigest() for k, v in IMAGE_BYTES.items()}
        record = {"media_urls": [f"s3://pmc-oa-opendata/PMC123.2/{k}?md5={md5[k]}" for k in IMAGE_BYTES] +
                  ["s3://pmc-oa-opendata/PMC123.2/supplement-1.xlsx?md5=00"]}
        self.asked = []

        def get(url, timeout=60):
            self.asked.append(url)
            if "list-type=2" in url:
                return OPEN_DATA_LISTING
            return json.dumps(record).encode() if url.endswith("metadata/PMC123.2.json") else None

        def download(url, path, cap=None, timeout=None):
            name = url.rsplit("/", 1)[-1]
            with open(path, "wb") as fh:
                fh.write(self.bytes.get(name, b""))
            return True
        self.bytes = dict(IMAGE_BYTES)
        self._net = papers.get, papers.download
        papers.get, papers.download = get, download

    def tearDown(self):
        papers.get, papers.download = self._net
        super().tearDown()

    def test_the_main_figures_images_are_fetched_from_the_open_data_and_listed(self):
        self.assertEqual(papers.main_figure_graphics(os.path.join(self.folder, "fulltext.xml")),
                         [(("main", 1), "paper-f0001"), (("main", 2), "paper-f0002")])
        out = papers.fetch(self.lib, ["pmid123"], figure_images=True)
        self.assertEqual(out, ["pmid123: the images of 2 of its 2 main figures, 0.0 MB"])
        self.assertIn(papers.OPEN_DATA + "metadata/PMC123.2.json", self.asked)  # the latest version's record
        self.assertEqual(papers.paper_images(self.lib, "pmid123"),
                         {("main", 1): "paper-f0001.jpg", ("main", 2): "paper-f0002.jpg"})
        for name, data in IMAGE_BYTES.items():
            with open(os.path.join(self.folder, "figures", name), "rb") as fh:
                self.assertEqual(fh.read(), data)
        self.assertEqual(papers.fetch(self.lib, ["pmid123"], figure_images=True),
                         ["pmid123: figure images fetched before"])

    def test_an_image_that_does_not_match_its_md5_is_removed(self):
        self.bytes["paper-f0002.jpg"] = b"changed"
        with self.assertRaises(papers.LibraryError) as e:
            papers.fetch(self.lib, ["pmid123"], figure_images=True)
        self.assertIn("paper-f0002.jpg does not match the md5", str(e.exception))
        self.assertFalse(os.path.exists(os.path.join(self.folder, "figures", "paper-f0002.jpg")))

    def test_a_row_names_the_image_its_figures_list_gives_its_figure(self):
        images, legs = {("main", 1): "f1.jpg"}, {("main", 1): {"letters": {"a", "b"}, "title": "", "body": ""},
                                                 ("ext", 3): {"letters": set(), "title": "", "body": ""}}
        self.assertEqual(papers.image_fails("f1.jpg", ("main", 1), images, False), [])
        self.assertEqual(papers.image_fails("none", ("main", 1), images, False),
                         ["the image of Fig. 1 is f1.jpg, not 'none'"])
        self.assertEqual(papers.image_fails("none", ("ext", 3), images, False), [])
        self.assertEqual(papers.image_fails("f1.jpg", ("ext", 3), images, False),
                         ["image 'f1.jpg' is not one of the paper's figure images; a figure without one is `none`"])
        self.assertEqual(papers.image_fails("page 4", ("main", 2), {}, True), [])
        self.assertEqual(papers.image_fails("page four", ("main", 2), {}, True),
                         ["image 'page four' is not one of the paper's figure images; a page of its PDF is written "
                          "`page N`"])
        self.assertTrue(legs)


if __name__ == "__main__":
    unittest.main()
