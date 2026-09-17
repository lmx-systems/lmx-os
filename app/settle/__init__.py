"""Settlement: what we tell a customer we saved them, and what we will not.

`docs/ROADMAP_1.5.md` §7 names this package for `STL-1` and `STL-2`. STL-1 is
done when *"a customer can read it without a call"*, which is a bar about prose
as much as arithmetic: a number a customer has to ring up to understand is a
number they will dispute.

Classified EDGE in `tests/test_architecture_boundaries.py`. Its shape is
negotiated with someone outside - the customer reading it - which is exactly
what Edge means here.
"""
from app.settle.statement import (
    MINIMUM_ARM_DROPS,
    ArmComparison,
    SavingsStatement,
    build_statement,
    render_statement,
)

__all__ = [
    "MINIMUM_ARM_DROPS",
    "ArmComparison",
    "SavingsStatement",
    "build_statement",
    "render_statement",
]
