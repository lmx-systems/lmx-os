"""
Historical baseline analysis: what the operation did before LMX OS decided anything.

Edge, not core (see `tests/test_architecture_boundaries.py`). This package reads flat
files somebody else exported and reduces them to the control-group numbers the W9
shadow scorecard is measured against. Nothing in the dispatch engine may import it -
for the same reason `app/shadow/` is edge: a comparison harness must never become a
dispatch dependency.

Entry point: `scripts/analyze_baseline.py`.
"""
from app.baseline.metrics import Baseline, Metric, compute
from app.baseline.report import render_json, render_text

__all__ = ["Baseline", "Metric", "compute", "render_json", "render_text"]
