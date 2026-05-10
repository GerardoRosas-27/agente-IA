from __future__ import annotations

from pathlib import Path

from harness.benchmarks import benchmark_summary, run_benchmarks


def test_run_benchmarks_writes_results(tmp_path: Path) -> None:
    (tmp_path / "webhook.py").write_text("def webhook_handler():\n    pass\n", encoding="utf-8")
    output = tmp_path / "benchmarks.json"

    results = run_benchmarks(root=tmp_path, output_path=output)
    summary = benchmark_summary(results)

    assert output.is_file()
    assert all(result.passed for result in results)
    assert "Benchmarks:" in summary
