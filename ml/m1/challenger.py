"""The model that has to beat the lookup table, or not ship (`PRD-5`).

*"Beats `PRD-3` on both familiar and unseen docks, or it does not ship."* The
harness (`PRD-4`) and the baseline (`PRD-3`) are already here; this is the
challenger they were built to judge.

## It is not the library the brief specifies, and that is a finding

`MODEL_AND_DATA_BRIEF.md` §M1 names **LightGBM**, chosen over CatBoost for a
procurement reason the founders settled without argument, and over XGBoost
because `receiver_id` is high-cardinality categorical and LightGBM handles that
natively with `max_cat_threshold` and `cat_smooth`.

Neither LightGBM nor XGBoost can run on the machine this was written on. Both
install from PyPI and both fail at import: their macOS arm64 wheels link against
`@rpath/libomp.dylib` and the OpenMP runtime is not present, with no Homebrew to
install it from. That is an environment fact rather than a code one, and the fix
is one package - but it had gone unverified through four separate mentions of
`requirements-ml.txt`, which is exactly how an unverified dependency becomes a
surprise on the day somebody needs it.

So the challenger here is `sklearn.ensemble.GradientBoostingRegressor` with
quantile loss: a genuine gradient-boosted quantile model, available, and enough
to run the release gate today.

**Read a loss carefully, though.** scikit-learn has no native categorical
support, so `receiver_id` cannot enter the model the way LightGBM would take it
- it enters only through the history features, which is the same information the
baseline already uses. This challenger is therefore handicapped on precisely the
feature LightGBM was chosen for. If it *beats* the baseline that is strong
evidence; if it loses, that is weaker evidence than a LightGBM loss would be,
and the gate should be re-run once `libomp` is available.

## Optional by construction

Imported lazily and reported as unavailable rather than raising. `ml/m1/`'s
harness, baseline and conformal bound are all standard library, and a release
gate that only runs where an ML stack is installed is a gate that stops running
the day CI's dependency cache changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.identity.node_class import NODE_CLASSES
from ml.m1.features import DwellRow

# The library the brief actually specifies, and why it is not what runs here.
INTENDED_LIBRARY = "lightgbm"
BLOCKED_REASON = (
    "lightgbm and xgboost install but cannot load on macOS arm64 without "
    "libomp.dylib; no Homebrew on this machine to install it from"
)


def is_available() -> bool:
    """Whether a challenger can be built at all in this environment."""
    try:
        import sklearn  # noqa: F401
    except ImportError:
        return False
    return True


def feature_vector(row: DwellRow) -> list[float]:
    """The design matrix, one row.

    `receiver_id` is absent on purpose rather than by omission: scikit-learn has
    no native categorical handling, and one-hot encoding 142 docks onto ~1,400
    rows would hand the model a column per dock with ten observations behind it.
    The dock enters through its history instead - which is the same signal the
    shrinkage baseline uses, and the reason this challenger is handicapped
    against the one the brief specifies.
    """
    prior_mean = row.recv_prior_mean if row.recv_prior_mean is not None else -1.0
    prior_p90 = row.recv_prior_p90 if row.recv_prior_p90 is not None else -1.0
    gap = row.recv_days_since_last if row.recv_days_since_last is not None else -1.0
    return [
        float(row.hour),
        float(row.day_of_week),
        float(row.stop_seq),
        float(row.stops_on_route),
        float(row.minutes_into_route),
        float(row.is_first_stop),
        float(row.is_last_stop),
        float(row.is_repeat_visit_today),
        float(row.pieces),
        float(row.revenue),
        float(row.recv_prior_n),
        float(prior_mean),
        float(prior_p90),
        float(gap),
        # A cold dock is a different regime, not a dock with a missing number,
        # and a tree can only split on that if it is told.
        float(row.recv_prior_n == 0),
    ] + [1.0 if row.node_class == c else 0.0 for c in NODE_CLASSES]


@dataclass
class GradientBoostedQuantile:
    """Quantile gradient boosting, scored by the same harness as the baseline."""

    q: float = 0.5
    n_estimators: int = 300
    max_depth: int = 3
    learning_rate: float = 0.05
    # Small data - 1,385 usable stops - so leaves have to stay honest. The
    # analysis project's LightGBM config used min_data_in_leaf=40 for the same
    # reason; this is the scikit-learn spelling of that decision.
    min_samples_leaf: int = 40
    random_state: int = 17
    model: object = field(default=None, repr=False)

    @property
    def name(self) -> str:
        return f"sklearn-gbm-q{int(self.q * 100)}"

    def fit(self, rows: list[DwellRow]) -> GradientBoostedQuantile:
        from sklearn.ensemble import GradientBoostingRegressor

        if not rows:
            return self
        self.model = GradientBoostingRegressor(
            loss="quantile",
            alpha=self.q,
            n_estimators=self.n_estimators,
            max_depth=self.max_depth,
            learning_rate=self.learning_rate,
            min_samples_leaf=self.min_samples_leaf,
            random_state=self.random_state,
        ).fit([feature_vector(r) for r in rows], [r.dwell_min for r in rows])
        return self

    def predict(self, rows: list[DwellRow]) -> list[float]:
        if self.model is None or not rows:
            return [0.0] * len(rows)
        # A dwell cannot be negative, and a quantile model is free to predict
        # one. Clamping here rather than in the metric so the number that gets
        # scored is the number that would be promised.
        return [max(0.0, float(v)) for v in self.model.predict(
            [feature_vector(r) for r in rows]
        )]
