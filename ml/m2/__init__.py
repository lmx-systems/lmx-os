"""`M2` - true urgency: the corpus, and (next) the harness that trains on it.

`MODEL_AND_DATA_BRIEF.md` §M2 predicts *P(a consequence occurs | this order is
late)*, calibrated, because the output is traded against cost in the solver and
a class label cannot be traded against anything.

Nothing here is importable from `app/`, and nothing here is evidence. The
corpus is a test instrument: it has a planted answer so the machinery can be
scored against something known before it is ever pointed at a real book.
"""
from ml.m2.corpus import ARM_CONTROL, ARM_TREATMENT, Corpus, Delivery, generate_corpus
from ml.m2.population import Location, probability_of_consequence

__all__ = [
    "ARM_CONTROL",
    "ARM_TREATMENT",
    "Corpus",
    "Delivery",
    "Location",
    "generate_corpus",
    "probability_of_consequence",
]
