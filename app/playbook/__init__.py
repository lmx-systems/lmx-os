"""The playbook: rules that make the calls a dispatcher made (`DEC-7`..`DEC-9`).

Decision D-SEAT (13 October 2026, `docs/ROADMAP_1.5.md` §2.6): the end state is
nobody running the day, with an on-call person only for what really needs one.
Three pieces:

* `touches.py` counts every write a person makes from the console, so "person
  touches per 100 orders" says how far there is to go (`DEC-7`).
* `rules.py` holds the rules - pure functions from an order's situation to a
  call - and `sweep.py` runs them, records each call, and acts on it when the
  rule's mode allows (`DEC-8`). The first rule is the failed delivery (`DEC-9`).
* `report.py` is what the console's playbook panel reads: the touch rate, and
  each rule's record against the people it is replacing.
"""
