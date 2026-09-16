"""Model-building harnesses. Not part of the running service.

Deliberately outside `app/`. `tests/test_architecture_boundaries.py` governs
`app/` and requires every package there to be classified CORE or EDGE; a
training harness is neither, because it is not in the request path at all. It
reads data, fits things, and prints numbers. Nothing here may be imported by
`app/`, and nothing here needs to be installed to run the service - which is
why the ML dependencies live in `requirements-ml.txt` rather than
`requirements.txt`.

The separation is not tidiness. An API container that carries a gradient
boosting library because a training script once needed it is a larger attack
surface, a slower deploy and a dependency somebody will eventually import from
the wrong place.
"""
