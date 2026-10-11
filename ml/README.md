# `ml/` — model-building harnesses

Not part of the running service. Nothing in `app/` may import this, and the
service does not need it installed: the ML dependencies live in
`requirements-ml.txt`, separate from `requirements.txt`, so the API container
never carries a gradient boosting library for a model that trains offline.

```bash
uv venv .venv-ml
uv pip install --python .venv-ml/bin/python -r requirements-ml.txt
```

A separate interpreter, not the application venv: the harness, baseline and
conformal bound are all standard library so they keep running where no ML stack
is installed, and the service image has no business carrying a boosting library.

**macOS arm64 needs `libomp` and pip will not tell you.** LightGBM and XGBoost
install cleanly and then fail at `import` — their wheels link against
`@rpath/libomp.dylib`, which is not in the wheel. `brew install libomp`, or
conda-forge's `llvm-openmp`. scikit-learn, numpy and pandas are unaffected.

## Modality — which machine could have made a delivery

`ROADMAP_1.5.md` `DE-8` onwards. Standard library. Reads only what the app exports, because
nothing here may import `app/`:

```bash
python scripts/machine_limits.py export --out ml/out/machine_limits.json   # DE-2
python scripts/freeze_test_sites.py export --out ml/out/test_sites.json    # DE-8
```

| Module | What it is |
|---|---|
| `ml/modality/splits.py` | `DE-8` — train/test on the frozen test sites, never a per-run hash; a training set holding a test site raises |

## Illustrative analytics — `DE-16`

`python scripts/illustrative_pack.py pack <workbook>` writes the pack to `ml/out/illustrative/`. Every chart is
stamped illustrative (D-SIM). `ml/analytics/illustrative.py` holds the arithmetic and the SVG.

## M2 — true urgency

`MODEL_AND_DATA_BRIEF.md` §M2: *P(a consequence occurs | this order is late)*,
calibrated, because the output is traded against cost in the solver.

```bash
python scripts/generate_m2_corpus.py --out ml/out/m2.csv   # a corpus at the brief's scale
python scripts/run_m2_harness.py --leak-demo               # the release gates
```

| Module | What it is |
|---|---|
| `ml/m2/population.py` | The planted truth: node-class urgency, per-dock effects, the dose response |
| `ml/m2/corpus.py` | Deliveries at scale, with the policy confound, the control arm and the censoring |
| `ml/m2/features.py` | Rule (1): every history is expanding-window, shifted by one |
| `ml/m2/splits.py` | Rule (2): chronological for the headline, whole held-out docks for cold start |
| `ml/m2/models.py` | The shrinkage baseline, a stdlib challenger, and isotonic calibration |
| `ml/m2/calibration.py` | Isotonic (PAVA), Brier, reliability, ECE and its noise floor |
| `ml/m2/gates.py` | The five rules as checks that can fail, plus label sizing |

## The real export — PRD-1, PRD-2, M1

```bash
python scripts/analyze_real_export.py --rate 45   # loaded driver cost per hour, no default
```

Reads `lmx-dwell/out/*.csv`, which are gitignored — this repository is public and
those files are the design partner's book. Nothing here embeds them, the loaders
have no field that could hold an account name, and the tests run on invented
fixtures with invented towns.

| Module | What it is |
|---|---|
| `ml/real/export.py` | Loaders, coverage, and a per-model verdict on what this export can support |
| `ml/prd/batch_value.py` | PRD-1 — the hold-window curve by node class, three readings |
| `ml/prd/trip_cost.py` | PRD-2 — loaded driver time against invoice value |
| `ml/m1/features.py` | Dwell features, expanding-window, plus the time and cold-start splits |
| `ml/m1/baseline.py` | PRD-3 — the shrunk quantile baseline, promoted |
| `ml/m1/conformal.py` | Rule (4) — a distribution-free coverage guarantee |
| `ml/m1/evaluate.py` | PRD-4 — warm and cold scored separately, promoted |

### What the export says

**PRD-1 cannot be closed against this data.** Its done-when is reproducing
+3–7% at shops and +195–271% at warehouse and transfer. Classified by account
name, the 229 receivers hold **one** warehouse and **no** transfer nodes, and
the file's own `Transfer` flag is false on all 6,715 rows. The shop end measures
+22% to +40% depending on which of three readings of "buys +X%" you take — not
+3–7% under any of them. Among classes with five or more docks the spread is
1.5×, not the 40× the finding implies.

**PRD-2 works.** 920 billed stops; at $45/hr, 12.3% lose money and 43 fall in
the cheap-part-on-a-long-trip class the roadmap names. Time only — there is no
distance column anywhere in the export — so every figure is a lower bound.

**M1 is one driver's pace.** 1,385 second-precision stops, 142 receivers, one
driver: no driver effect can be separated and there is no held-out-driver split.
The shrunk baseline beats a global quantile on both warm and cold populations,
so PRD-3's claim reproduces. Conformal calibration *tightens* the warm p90 from
14.3 to 12.3 minutes and it holds at 89.8%. Calibrated correctly for cold start
— on held-out receivers — the promise widens to **23.2 minutes for a stop whose
median is two**. Kept 100% of the time and not sellable, which is what "not
enough cold-start data" looks like once the arithmetic is done honestly.

**The two files disagree about what a route is** — 3.7 stops per route against
27, a factor of seven, because one counts manifests and the other driver-days.
Every per-route figure inherits it. That is REC-5.

### The corpus is not evidence

Every coefficient in `population.py` is a guess with a reason attached. A model
trained here has learned our guesses. The corpus exists so the *machinery* can
be scored against a known answer before it is pointed at a real book — leakage,
splits, calibration, the arm. Every row is stamped `synthetic` and every run
prints the banner; a number lifted out of this and put in front of an investor
has had the warning removed by hand.

### What it says today

Run at the brief's scale, five of seven gates pass. The two that fail are the
output, not a problem with the run:

- **The challenger does not beat the shrinkage baseline on both populations.**
  Rule (3) is explicit about what happens then — ship the baseline. The brief
  records the same result on this team's real harness.
- **The control arm is not powered to size the confound it exists for.** At 8%
  of 78,000 deliveries it holds ~630 late orders; the 95% interval on its
  consequence rate is wider than the bias it is estimating. It needs roughly
  twice that.
