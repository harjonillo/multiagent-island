"""End-to-end run on the mock backend. Works with pytest or plain `python tests/test_mock_run.py`."""

import tempfile
from pathlib import Path

from llmabm.agent import parse_action, parse_probe
from llmabm.analysis import build_viewer, exposure, load_run, message_index, roots_of, summary
from llmabm.llm import load_backends
from llmabm.scenario import load_scenario
from llmabm.world import Simulation

ROOT = Path(__file__).resolve().parent.parent


def test_parse_action_tolerates_noise():
    raw = '<think>hmm</think>Sure!\n```json\n{"beliefs": "b", "messages": {"to": "x", "content": "hi"}}\n```'
    action, err = parse_action(raw)
    assert err is None and action["beliefs"] == "b" and action["messages"][0]["to"] == "x"
    assert parse_action("no json here")[0] is None
    assert parse_probe("I'd say: Unsure.", ["yes", "no", "unsure"]) == "unsure"


def test_mock_run_end_to_end():
    sc = load_scenario(ROOT / "scenarios/class_suspension.yaml", seed=3)
    assert len(sc.agents) == 16 and "kagawad_reyes" in sc.graph
    with tempfile.TemporaryDirectory() as d:
        run_dir = Simulation(sc, load_backends(None), Path(d) / "run", seed=3, backend_override="mock", verbose=False).run(8)
        run = load_run(run_dir)
        msgs = run.of("message")
        assert msgs, "mock agents should forward something"
        idx, memo = message_index(run), {}
        assert all(roots_of(m["id"], idx, memo) for m in msgs), "every mock message descends from a seed"
        exp = exposure(run)
        assert len(exp["rumor"]) >= 1 and "kagawad_reyes" in exp["advisory"]
        assert run.of("probe")
        assert "seed 'rumor'" in summary(run)
        assert build_viewer(run, Path(d) / "v.html").stat().st_size > 1000


def test_same_seed_same_run():
    def edges(seed):
        return sorted(map(sorted, load_scenario(ROOT / "scenarios/class_suspension.yaml", seed=seed).graph.edges))
    assert edges(1) == edges(1)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
