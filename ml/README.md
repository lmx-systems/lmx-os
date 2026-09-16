# `ml/` — model-building harnesses

Not part of the running service. Nothing in `app/` may import this, and the
service does not need it installed: the ML dependencies live in
`requirements-ml.txt`, separate from `requirements.txt`, so the API container
never carries a gradient boosting library for a model that trains offline.

```bash
pip install -r requirements-ml.txt   # only for the boosted models; the harness runs on stdlib
```

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
