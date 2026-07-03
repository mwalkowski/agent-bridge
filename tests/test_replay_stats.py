import importlib.util
from collections import Counter
from pathlib import Path


def _load_example(module_name, relative_path):
    repo_root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(module_name, repo_root / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_synthetic_log_reproduces_paper_figures(tmp_path, capsys):
    generator = _load_example("generate_ocf26_log", "examples/replay/generate_ocf26_log.py")
    aggregator = _load_example("aggregate_stats", "examples/replay/aggregate_stats.py")

    root = str(tmp_path / "ocf26-replay")
    generator.main(["--root", root])
    capsys.readouterr()  # discard generator/CLI output

    per_channel, per_type, total = aggregator.aggregate(Path(root))
    assert total == 219
    assert dict(per_channel) == generator.CHANNELS
    assert dict(per_type) == generator.TYPES
    # both marginals independently sum to the same reported total
    assert sum(generator.CHANNELS.values()) == 219
    assert sum(generator.TYPES.values()) == 219
