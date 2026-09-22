"""`AGT-1`, the resolution bake-off: does an agent resolve identity better?

*"Hand-label the true physical dock count behind the 230 account IDs once, then
run the existing deterministic resolver and an agent resolver on identical
input."* The gate for `AGT-2` and `AGT-3`, both of which are `NEW` and both of
which are waiting on this answer.

## Why it lives in `ml/` and not `app/identity/`

Same shape as `ml/m1/`: an incumbent, a challenger, one held-out judgement, and
a ship/do-not-ship rule. Nothing here is on a path an order travels. Putting a
bake-off inside `app/identity/` would file an evaluation as production code and
- worse - would put an outbound API call inside a foundation package that both
Core and Edge import.

## The one thing that is not code

Ground truth. `identity_truth.csv` is filled in **by a person**, once, and every
number this package produces is a statement about that file. There is no way to
score a resolver against a truth nobody has written down, and generating the
labels with a model would be scoring the challenger against itself.
"""
