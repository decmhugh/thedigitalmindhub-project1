import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_runner():
    spec = importlib.util.spec_from_file_location("run_evals", ROOT / "scripts" / "run_evals.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_evals"] = module
    spec.loader.exec_module(module)
    return module


def test_cases_file_has_at_least_five_valid_cases():
    cases = json.loads((ROOT / "evals" / "cases.json").read_text(encoding="utf-8"))
    assert len(cases) >= 5
    for case in cases:
        assert case["name"] and case["prompt"]
        assert case.get("expect_contains") or case.get("expect_blocked")


def test_check_case_substring_is_case_insensitive():
    runner = load_runner()
    assert runner.check_case({"expect_contains": ["Paris"]}, {"response": "it is paris"})
    assert not runner.check_case({"expect_contains": ["Paris"]}, {"response": "London"})


def test_check_case_expect_blocked():
    runner = load_runner()
    assert runner.check_case({"expect_blocked": True}, {"response": "", "blocked": True})
    assert not runner.check_case({"expect_blocked": True}, {"response": "hi"})


def test_run_publishes_metrics_and_returns_failure_count():
    runner = load_runner()
    cases = [
        {"name": "a", "prompt": "p1", "expect_contains": ["yes"]},
        {"name": "b", "prompt": "p2", "expect_contains": ["yes"]},
    ]
    answers = iter([{"response": "yes"}, {"response": "no"}])
    published = []

    failures = runner.run(cases, lambda prompt: next(answers), lambda ns, metrics: published.append((ns, metrics)), "p-dev/agent-evals")

    assert failures == 1
    namespace, metrics = published[0]
    assert namespace == "p-dev/agent-evals"
    values = {metric["MetricName"]: metric["Value"] for metric in metrics}
    assert values == {"Passed": 1, "Failed": 1, "PassRate": 50.0}


def test_invoke_error_counts_as_failure():
    runner = load_runner()

    def boom(prompt):
        raise RuntimeError("down")

    failures = runner.run([{"name": "a", "prompt": "p", "expect_contains": ["x"]}], boom, lambda ns, metrics: None, "ns")
    assert failures == 1
