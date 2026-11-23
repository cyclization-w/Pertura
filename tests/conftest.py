"""The fast and the slow product tests (2026-10-12, ADR 0017 §8). `python -m pytest tests` runs every test, as
before; `-m "not slow"` (scripts/test_fast.sh) leaves out the ones listed here: whole files, and single tests that
took over 5 s on the laptop (--durations=0 on 2026-10-12, after the trying loop's tests moved to tests_research).
One list, here, rather than marks in each file.

The tests wait for a background measurement to finish: a session's agent asks measure again while it runs, but a
test calls it once, and on a loaded server the measurement (two R demultiplexers, Cell Ranger's guide calls) can take
longer than the first call's wait."""
import pytest

from pertura import design_service as _design_service

_design_service.MEASURE_WAIT = _design_service.MAX_MEASURE_WAIT = 900


def earlier_form_as_keys(args):
    """check_design's arguments as the tests' fixtures wrote them before the design confirmation was rewritten
    (2026-11-19): a whole nested proposal (proposal) or changes by dotted key (changes, with why), taken to
    check_design's {dotted key: {value, source, note}}. A fact whose status was unresolved is left out, as no value;
    keys the agent does not give (units.n_units) are left out too."""
    from pertura.design_audit.checks import facts_of
    from pertura.design_shapes import KEYS
    why = args.get("why") or "the test's proposal"

    def entry(n):
        if not (isinstance(n, dict) and ("value" in n or "status" in n)):
            return {"value": n, "source": why}
        return {"value": n.get("value"), "source": n.get("source") or why, **({"note": n["note"]} if n.get("note")
                                                                             else {})}
    if "proposal" in args:  # a whole proposal replaced the one checked last: the keys it lacks go (null)
        p = args["proposal"]
        given = {k: entry(n) for k, n in facts_of(p if isinstance(p, dict) else {})
                 if k in KEYS and n.get("status") != "unresolved"}
        design = {k: given.get(k) for k in KEYS}
    else:
        design = {k: (None if v is None else entry(v)) for k, v in (args.get("changes") or {}).items()}
    return {"design": design, **({"why": args["why"]} if args.get("why") else {})}


_real_call = _design_service.DesignService.call


def _call(self, tool, args=None, elicit=None):
    if tool == "check_design" and isinstance(args, dict) and ("proposal" in args or "changes" in args):
        args = earlier_form_as_keys(args)
    return _real_call(self, tool, args, elicit)


_design_service.DesignService.call = _call

SLOW_FILES = {
    "test_bridge_calibration.py",  # 72 s
    "test_notebook.py",  # 69 s
    "test_notebook_concurrency.py",  # 33 s
    "test_disguised_decoys.py",  # 10 s: the research test of analysis/disguised_decoys
    "test_agent_session_end_to_end.py",  # 20 s: M1a, a whole session through the server
}
SLOW_TESTS = {  # "file.py::Class::test" or "file.py::test"
    "test_exploration.py::TestBridge::test_a_method_is_learned_on_one_half_and_tested_on_the_other",  # 5.7 s
    "test_swap_halves.py::test_arms_on_a_synthetic_screen_with_guide_effects",  # 5.4 s
    "test_e2_wall_clock_deadline.py::TestWallClock::test_a_real_process_and_its_child_are_stopped_at_the_deadline",
    # the lean agent's lock tests, 3-6 s each (each lock runs its tests in a sandbox child)
    "test_agent_service.py::TestAgentService::test_an_interrupted_lock_resumes_without_a_form_and_grades_nothing_twice",
    "test_agent_service.py::TestAgentService::test_the_lock_is_the_users_grades_once_and_keeps_testing_apart_from_outcome",
    "test_agent_service.py::TestAgentService::test_the_screen_has_one_certified_lock_whatever_the_session",
    "test_agent_service.py::TestAgentService::test_a_lock_interrupted_while_grading_reuses_the_test_that_ran",
    "test_agent_service.py::TestAgentService::test_a_target_with_no_held_out_guide_is_a_candidate",
    "test_agent_service.py::TestAgentService::test_two_windows_on_one_session_share_its_state",
    "test_agent_service.py::TestAgentService::test_a_program_claim_is_graded_by_its_tests_key",
    "test_agent_service.py::TestAgentService::test_a_candidate_is_recorded_freely_and_never_run_on_the_hidden_side",
    "test_agent_service.py::TestAgentService::test_a_failing_test_gives_not_graded_and_the_lock_finishes",
    "test_agent_service.py::TestAgentService::test_a_refused_lock_leaves_the_screens_lock_free",
    "test_agent_service.py::TestAgentService::test_after_the_lock_later_claims_are_candidates_and_say_so",
    "test_agent_service.py::TestAgentService::test_the_screen_allows_mode_a_and_nothing_hidden_reaches_the_agent",
    "test_agent_workers.py::TestWorkers::test_two_workers_have_their_own_notebooks_within_the_limit",  # 12 s: kernels
    "test_agent_methods.py::TestMethodsInTheNotebook::test_the_library_runs_inside_the_confined_kernel",  # 6 s
    "test_agent_report.py::TestReport::test_a_number_from_nowhere_writes_no_report",  # a lock each
    "test_agent_report.py::TestReport::test_the_count_of_certified_findings_must_be_the_records",
    "test_agent_report.py::TestReport::test_the_report_shows_the_tree_and_each_claims_testing_and_outcome",
    "test_agent_workers.py::TestWorkers::test_after_the_lock_a_branch_does_no_more_work_and_its_notebook_closes",  # a lock
}


def pytest_collection_modifyitems(config, items):
    for item in items:
        path, _, rest = item.nodeid.partition("::")
        name = path.replace("\\", "/").rsplit("/", 1)[-1]
        if name in SLOW_FILES or f"{name}::{rest}" in SLOW_TESTS:
            item.add_marker(pytest.mark.slow)
