"""The knowledge base (docs/adr/0032, amendment 2; docs/build_goal_knowledge_base.md): one folder that the agent and
the user read, every entry with an id, the index made from the files."""
import os
import tempfile
import unittest

from pertura import knowledge

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# the ids of the draft the user reviewed (docs/build/knowledge_where_it_is_used/the_modules_and_their_knowledge.md)
REVIEWED = (["U%d" % i for i in range(1, 7)] + ["R%d" % i for i in range(1, 6)] + ["G%d" % i for i in range(1, 6)]
            + ["S1", "S2", "S3"] + ["PG%d" % i for i in range(1, 7)] + ["ES1", "ES2", "ES3"]
            + ["V%d" % i for i in range(1, 6)] + ["PF%d" % i for i in range(1, 20)]
            + ["T%d" % i for i in range(1, 8)] + ["O%d" % i for i in range(1, 7)] + ["L%d" % i for i in range(1, 6)]
            + ["C%d" % i for i in range(1, 6)] + ["X1", "X2", "X3", "X4"]
            + ["P1.%d" % i for i in range(1, 10)] + ["P6.%d" % i for i in range(1, 16)])


class TestTheBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = knowledge.load()

    def test_it_is_the_plugins_folder_and_every_id_the_user_reviewed_is_an_entry(self):
        # the same folder, however it is reached (the analysis server reaches it through a linked path too)
        self.assertEqual(os.path.realpath(knowledge.folder()),
                         os.path.realpath(os.path.join(ROOT, "plugin", "skills", "pertura-knowledge")))
        self.assertEqual([i for i in REVIEWED if self.base.get(i) is None], [])

    def test_the_index_in_skill_md_is_the_one_made_from_the_files(self):
        self.assertEqual(knowledge.index_in_skill(), self.base.index(),
                         "run python -m pertura.knowledge write-index")

    def test_every_file_holds_entries_and_every_entry_has_its_text(self):
        files = {e["file"] for e in self.base.entries.values()}
        for folder, _, names in os.walk(knowledge.folder()):
            for name in names:
                if name.endswith(".md") and name != "SKILL.md":
                    rel = os.path.relpath(os.path.join(folder, name), knowledge.folder()).replace(os.sep, "/")
                    self.assertIn(rel, files)
        for eid, e in self.base.entries.items():
            self.assertTrue(e["title"] and e["text"].strip(), eid)
            self.assertIn(eid, e["text"].split("\n", 1)[0], eid)  # the text opens with its own id

    def test_a_card_step_carries_its_fields_and_the_cards_paragraph_on_it(self):
        step = self.base.get("P6.5")
        self.assertEqual(step["kind"], "card step")
        self.assertIn("Part: module:E.combinations", step["text"])
        self.assertIn("**P6.4 and P6.5.**", step["text"])
        self.assertNotIn("**P6.1.**", step["text"])

    def test_read_names_the_ids_no_entry_holds_and_find_ranks_by_the_words(self):
        found, missing = self.base.read(["R3", "Q9", "PF9"])
        self.assertEqual([e["id"] for e in found], ["R3", "PF9"])
        self.assertEqual(missing, ["Q9"])
        top = [i for i, _, _ in self.base.find("shared response")[:3]]
        self.assertIn("R3", top)
        self.assertIn("D.common_response", top)
        self.assertEqual(self.base.find(""), [])


def _source_strings():
    """Every quoted string in the package's code: the names a module's result can hold."""
    import re
    found = set()
    src = os.path.join(ROOT, "src", "pertura")
    for folder, _, names in os.walk(src):
        for name in names:
            if name.endswith(".py"):
                text = open(os.path.join(folder, name), encoding="utf-8").read()
                found |= set(re.findall(r"\"([^\"\n]{1,60})\"", text)) | set(re.findall(r"'([^'\n]{1,60})'", text))
    return found


class TestTheModulesEntries(unittest.TestCase):
    """Step 2: an entry for every module, its ways its contract's, each with the ids to read."""

    @classmethod
    def setUpClass(cls):
        from pertura import question_modules as qm
        cls.qm = qm
        cls.base = knowledge.load()

    def test_every_module_has_its_entry_with_its_contracts_ways(self):
        entries = self.base.modules()
        self.assertEqual(sorted(entries), sorted(self.qm.REGISTRY))
        for name, m in self.qm.REGISTRY.items():
            self.assertEqual([w["way"] for w in entries[name]["ways"]], [how for how, _ in m.contract.fails_when],
                             name)

    def test_every_way_names_what_to_read_and_do_and_every_id_resolves(self):
        for name, e in self.base.modules().items():
            for w in e["ways"]:
                self.assertTrue(w["read"] or w["read_text"].startswith(knowledge.NO_KNOWLEDGE), (name, w["way"]))
                self.assertTrue(w["do"] and w["shows_in"], (name, w["way"]))
                for i in w["read"]:
                    self.assertIsNotNone(self.base.get(i), (name, i))
            for i in list(e["calls_for"]) + e["also_read"]:
                self.assertIsNotNone(self.base.get(i), (name, i))

    def test_what_a_way_shows_in_is_a_name_the_code_writes_or_is_said_to_be_missing(self):
        import re
        strings = _source_strings()
        for name, e in self.base.modules().items():
            for w in e["ways"]:
                if w["shows_in"].startswith("not in the result yet"):
                    continue
                for token in re.findall(r"`([^`]+)`", w["shows_in"]):
                    if token in self.qm.REGISTRY or "(" in token or token.startswith("perturbation."):
                        continue  # a module, a block call, a key of the design
                    self.assertIn(token, strings, (name, w["way"], token))

    def test_every_moment_names_a_tool_there_is_and_entries_that_resolve(self):
        from pertura import analysis_service, blocks
        moments = self.base.moments()
        self.assertGreaterEqual(len(moments), 7)
        for mid, m in moments.items():
            tool = m["tool"] or ""
            if tool.startswith("pt."):
                self.assertTrue(callable(getattr(blocks, tool[3:], None)), (mid, tool))
            else:
                self.assertIn(tool, analysis_service.TOOL_NAMES, mid)
            self.assertTrue(m["read"] and m["why"], mid)
            for i in m["read"]:
                self.assertIsNotNone(self.base.get(i), (mid, i))


class TestThePointers(unittest.TestCase):
    """Step 3: what a result is read beside, the moves by their ids, the tools' moments and read_knowledge."""



    def test_every_move_has_its_entry_and_the_module_that_calls_it_names_it(self):
        from pertura import moves
        base = knowledge.load()
        for name, mid in moves.MOVES.items():
            text = moves.text(name)
            self.assertTrue(text["run"] and text["papers"], name)
            self.assertIn(mid, base.get(moves.CALLED_BY[mid])["calls_for"], (name, mid))


    def test_read_knowledge_gives_entries_a_modules_entry_and_ids_for_words_within_its_cap(self):
        out = knowledge.read_reply(ids=["R3", "Q9"])
        self.assertEqual([e["id"] for e in out["entries"]], ["R3"])
        self.assertEqual(out["no_entry"], ["Q9"])
        self.assertIn("## R3. The shift every perturbation shares", out["entries"][0]["text"])
        out = knowledge.read_reply(module="C.programmes")  # an earlier name finds the module
        self.assertEqual(out["entries"][0]["id"], "C.programs")
        self.assertIn("R3", [f["id"] for f in knowledge.read_reply(words="shared response")["found"]])
        self.assertIn("holds", knowledge.read_reply())
        out = knowledge.read_reply(ids=["P1", "P6", "R3", "PF9"], cap=1500)
        self.assertEqual(len(out["entries"]) + len(out["left_out"]), 4)
        self.assertTrue(out["left_out"])


class TestTheQuestions(unittest.TestCase):
    """The base by questions (docs/build/converging_pertura_what_was_discussed.md, section 9): the questions an
    analysis meets, by the step of the work, each id a short word, each short answer marked as Pertura's reading and
    naming in every sentence the entries it rests on, and every entry in a question; nothing else of the base moved."""

    STEPS = ("The design", "Cells and guides", "Effects", "States", "Programs and gene sets", "Across perturbations",
             "Findings and validation")

    @classmethod
    def setUpClass(cls):
        cls.base = knowledge.load()
        cls.questions = cls.base.questions()

    def test_each_question_has_a_short_word_for_its_id_a_step_and_a_short_answer_marked_as_pertura_s(self):
        self.assertGreaterEqual(len(self.questions), 30)
        for qid, q in self.questions.items():
            self.assertRegex(qid, r"^[a-z]+(-[a-z]+)?$")
            self.assertIn(q["step"], self.STEPS, qid)
            self.assertTrue(q["short"], qid)
            self.assertIn(f"**{knowledge.SHORT}:**", q["text"], qid)
        self.assertEqual(sorted({q["step"] for q in self.questions.values()}), sorted(self.STEPS))

    def test_every_sentence_of_a_short_answer_names_the_entries_it_rests_on_and_each_is_read(self):
        import re
        for qid, q in self.questions.items():
            for sentence in re.split(r"(?<=\.)\s+(?=[A-Z`])", q["short"]):
                cited = knowledge.citations(sentence)
                self.assertTrue(cited and cited[-1][0] == len(sentence) - 1, (qid, sentence))
                for _, ids in cited:
                    self.assertEqual([i for i in ids if i not in q["read"]], [], (qid, sentence))
            for i in q["read"]:
                self.assertIsNotNone(self.base.get(i), (qid, i))
                self.assertNotEqual(self.base.get(i)["kind"], "question", (qid, i))
        self.assertEqual(knowledge.citations("the S and G2/M genes (mouse, donor) (PF2, `C.genes`)."),
                         [(len("the S and G2/M genes (mouse, donor) (PF2, `C.genes`)"), ["PF2", "C.genes"])])

    def test_every_entry_but_a_cards_step_is_in_a_question(self):
        left = [i for i, e in self.base.entries.items()
                if e["kind"] not in ("question", "card step") and i not in self.base.gathered]
        self.assertEqual(left, [])
        self.assertIn("units", self.base.gathered["PF2"])

    def test_read_knowledge_gives_a_question_with_its_entries_and_lists_the_questions_by_step(self):
        out = knowledge.read_reply(ids=["units"])
        q = out["entries"][0]
        self.assertEqual((q["id"], q["kind"], q["step"]), ("units", "question", "The design"))
        self.assertIn({"id": "PF2", "title": "Cells as the observations can give excess false positives",
                       "kind": "pitfall"}, q["rests_on"])
        self.assertIn("draft", out["reading"])
        self.assertIn("rests on", out["how"])
        self.assertNotIn("how", knowledge.read_reply(ids=["PF2"]))  # an entry's reply as before
        listed = knowledge.read_reply()["questions"]
        self.assertEqual(list(listed), list(self.STEPS))
        self.assertIn(["calibration", self.base.get("calibration")["title"]], listed["Effects"])
        found = knowledge.read_reply(words="clones")
        self.assertEqual(found["questions"][0]["id"], "clones")
        self.assertTrue(found["found"] and all(f["kind"] != "question" for f in found["found"]))

    def test_the_users_page_reads_the_questions_as_the_package_does(self):
        import sys
        status = os.path.join(ROOT, "plugin", "status")
        if not os.path.isdir(status):
            self.skipTest("no plugin folder here")
        sys.path.insert(0, status)
        import knowledge_page
        page = [(q["id"], q["title"], q["step"], q["short"], q["read"]) for q in knowledge_page.questions()]
        here = [(k, q["title"], q["step"], q["short"], q["read"]) for k, q in self.questions.items()]
        self.assertEqual(page, here)


class TestWhatPublishedScreensDidNext(unittest.TestCase):
    """Step 5: the counts of what published screens did next are made by code from the catalogue of moves, with the
    excluded papers left out; the base names papers and what they did, so it holds none of the counts until the moves
    reading has a fixed version."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        path = os.path.join(ROOT, "analysis", "end_to_end", "follow_up_moves_of_screens",
                            "what_published_screens_did_next.py")
        if not os.path.exists(path):
            raise unittest.SkipTest("no catalogue here")
        if not os.path.exists(os.path.join(ROOT, "analysis", "sources_kept_out_of_the_knowledge_base.json")):
            raise unittest.SkipTest("the list of sources kept out stays on the laptop")
        spec = importlib.util.spec_from_file_location("what_published_screens_did_next", path)
        cls.nx = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.nx)

    def test_the_base_holds_no_count_of_what_published_screens_did_next(self):
        self.assertFalse(self.nx.COUNTS_IN_THE_BASE)
        for module in self.nx.MODULES:
            text = open(os.path.join(knowledge.folder(), "modules", f"{module}.md"), encoding="utf-8").read()
            self.assertNotIn(self.nx.LINE, text, module)
        text = " ".join(open(os.path.join(knowledge.folder(), "follow_up_moves.md"), encoding="utf-8").read().split())
        self.assertIn(self.nx.BASE_INTRO, text)
        self.assertIsNone(self.nx.INTRO.search(text))
        self.assertTrue(self.nx.line("C.programs"))  # the counts are still made, for the developer



class TestTheParts(unittest.TestCase):
    def test_ids_are_read_from_text_without_parts_of_other_words(self):
        self.assertEqual(knowledge.ids_in("G3, PF7; P6.15 and P6.1."), ["G3", "PF7", "P6.15", "P6.1"])
        self.assertEqual(knowledge.ids_in("PMC11569274, GSE216800, R² and B.efficacy"), [])
        self.assertEqual(knowledge.ids_in("no knowledge yet (a card on regulons)"), [])

    def test_fields_run_on_over_their_indented_lines_and_keep_their_items(self):
        block = ("- **Run:** `F.guides_agree`, whose table\n  names the targets\n- **Published screens:**\n"
                 "  - Xia 2023 (Fig. 4b): the guides\n    clustered\n  - Liang 2023\nA paragraph after.\n")
        f = knowledge.fields(block)
        self.assertEqual(f["Run"], "`F.guides_agree`, whose table names the targets")
        self.assertEqual(f["Published screens items"], ["Xia 2023 (Fig. 4b): the guides clustered", "Liang 2023"])

    def test_an_id_held_twice_is_refused_and_the_index_is_written_between_its_markers(self):
        with tempfile.TemporaryDirectory() as d:
            for name, text in (("pitfalls.md", "# P\n\n## PF1. One\n\ntext\n"),
                               ("follow_up_moves.md", "# M\n\n### PF1. Again\n\ntext\n")):
                with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
                    fh.write(text)
            with self.assertRaises(knowledge.KnowledgeError):
                knowledge.load(d)
            os.remove(os.path.join(d, "follow_up_moves.md"))
            with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as fh:
                fh.write(f"# Base\n\n{knowledge.INDEX_START}made by code -->\nold\n{knowledge.INDEX_END}\nafter\n")
            self.assertTrue(knowledge.write_index(d))
            self.assertEqual(knowledge.index_in_skill(d), knowledge.load(d).index())
            self.assertIn("| PF1 | One | `pitfalls.md` |", knowledge.index_in_skill(d))
            self.assertFalse(knowledge.write_index(d))
            self.assertTrue(open(os.path.join(d, "SKILL.md"), encoding="utf-8").read().endswith("after\n"))


if __name__ == "__main__":
    unittest.main()
