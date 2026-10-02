# State-specific overtime rules: research for docs/ROADMAP.md A9

## What's already built (engineering, done)

- Real on-duty hours reconstructed from the shift-event log
  (`app/payroll/hours.py`), federal FLSA overtime (1.5x past 40 hrs in a
  Monday-Sunday workweek) for `w2` drivers, monthly/weekly pay periods,
  all on the hub's own clock (see "The workweek is on the hub's clock"
  below),
  and a `PayrollProvider` interface (`app/payroll/`, Rippling once a real
  account exists).
- A pluggable per-state overtime rule mechanism
  (`app/payroll/overtime_rules.py`) and the daily-hours bucketing it
  needs (`app/payroll/hours.py`'s `daily_hours_worked_from_shift_events`)
  - see "Engineering extension points" below. **No state-specific rule is
    registered.** Every driver, in every hub, gets the federal-only rule
    today, byte-for-byte the same behavior as before this pass.
- `Hub.state_code` (migration `0017`) - a real, neutral fact (which US
  state a hub is physically in), currently unset for every existing hub.

## What's not code - the actual decision this needs

Several US states require overtime pay beyond the federal 40hr/week
threshold. This is genuinely a business/legal decision, not an
engineering one, because:

1. **Which states LMX actually operates in matters, and that's not
   engineering's call.** `Hub.state_code` is unset for every hub today -
   nobody has populated it, because there's no Hub creation/edit API or
   UI yet (hubs are seed/DB-provisioned only, confirmed by grep - no
   `POST`/`PUT /hubs` endpoint exists anywhere). Populating it isn't a
   business decision (a hub's state is a fact, not a judgment call), but
   it hasn't been done, and doing it is worth pairing with the actual
   policy decision below rather than done blind.
2. **Whether to apply a stricter state rule at all is a cost/compliance
   tradeoff**, not something engineering should default into. A state's
   *legal minimum* isn't automatically what a payroll system should
   compute if, e.g., legal/finance decide a flat policy across all hubs
   is simpler to administer and already meets or exceeds every
   applicable state minimum.
3. **The exact rule needs real legal sign-off, not a good-faith
   engineering guess.** Overtime law has real nuance that a plausible-
   looking implementation can get wrong in ways that matter (back pay,
   penalties): what counts as a "workday" (calendar day vs. a
   employer-defined fixed 24-hour period), whether a 7th-consecutive-day
   rule applies, alternative-workweek-schedule exceptions, and whether a
   state's rule preempts or stacks with federal FLSA. This needs an
   employment-law consultation before any state's rule is turned on, the
   same category of "verify against the real thing before it's live" this
   codebase already applies to the Epicor adapter and Rippling client
   (both flagged as unverified best-effort guesses at their published API
   shape, not fabricated but not confirmed either).

### States most commonly cited as having daily and/or non-federal overtime rules

Noted here as background for whoever makes the actual call - **not
verified against current statute text, not legal advice, and not
implemented**:

- **California** - daily overtime past 8 hrs/day (1.5x), double time past
  12 hrs/day, and 1.5x for the first 8 hours on a 7th consecutive workday
  in a workweek (double time past 8 hrs on that 7th day). Also has a
  separate meal/rest-break premium-pay requirement that is not an
  overtime multiplier at all and would need its own, entirely separate
  mechanism if ever in scope - explicitly not addressed by anything in
  this pass.
- **Alaska, Nevada** - daily overtime past 8 hrs/day in some
  circumstances (Nevada's applies mainly to workers paid under a certain
  hourly-wage threshold; Alaska's has its own qualifying conditions).
- **Colorado** - daily overtime past 12 hrs/day (or past 12 consecutive
  hours regardless of calendar day).

Every one of these has real qualifying conditions and exceptions beyond
a bare hour threshold. None of that detail is modeled here - this list
exists only to scope how big the eventual legal-research task is, not to
pre-empt it.

## Engineering extension points built in this pass

Decision-independent groundwork, so that once the business/legal call
above is actually made, turning on a real state rule is small and
isolated:

- **`app/payroll/overtime_rules.py`** - an `OvertimeRule` interface
  (`apply(daily_hours: dict[date, float]) -> (regular_hours,
  overtime_hours)`) and a `STATE_OVERTIME_RULES: dict[str, OvertimeRule]`
  registry keyed by two-letter state code, with `FederalWeeklyOvertimeRule`
  as the always-available default for any hub with no state-specific
  entry. Adding a real state's rule later is writing one `OvertimeRule`
  subclass and registering it - not touching `app/payroll/hours.py`'s
  hour-reconstruction logic at all.
- **`app/payroll/hours.py`'s `daily_hours_worked_from_shift_events`** -
  the same on-duty reconstruction `hours_worked_from_shift_events` always
  used, now also available bucketed per calendar day (splitting a span
  that crosses midnight at the boundary). Nothing in this codebase
  computed per-day totals before this existed; a daily-threshold rule
  needs it, and it's now there to plug into rather than something a
  future rule's author would have to build from scratch.
- **`Hub.state_code`** (migration `0017`) - nullable, unpopulated for
  every existing hub. Unset means "use the federal-only default,"
  identical to today's behavior.

## The workweek is on the hub's clock (decided 2 October 2026)

Pay periods, the overtime workweek and the per-day split used to be cut at
UTC midnight, which is 4 or 5pm at a Los Angeles hub. A stub dated "1-30
September" covered 5pm on 31 August to 5pm on 30 September, and the
workweek began at 5pm on Sunday, so a Sunday-evening shift was split
across two weeks' 40-hour thresholds. In the test for it, 38 hours from
Monday plus a Sunday 4pm-8pm shift is 42 hours in one week, 40 regular
and 2 overtime. On the UTC cut, the week saw 39 and the next week 3, so
no overtime was paid.

**Decided: payroll uses the hub's clock** (#150). A workweek starts at
Monday midnight on the hub's clock, monthly and weekly pay periods start
at the hub's midnight, and the per-day split a daily rule will need happens
at the hub's midnight, so a "day" is the calendar day the driver worked.
Nothing had been paid when this was decided, so there is no history on the
UTC weeks to reconcile.

**One thing to confirm, in the same consultation as step 2 below.**
29 CFR 778.105 defines a workweek as a fixed and regularly recurring
period of 168 hours, which may begin on any day and at any hour. A week
running from Monday midnight to Monday midnight on a local clock is
**167 hours across the spring clock change and 169 across the autumn
one.** Both choices are mechanical to switch between. The alternative is
a fixed 168-hour week anchored to one instant, which drifts an hour
against the local clock for half the year. Which one LMX uses is a
compliance call, not an engineering one.

## Known limitation not addressed by this pass

Every rule researched above still applies a 1.5x multiplier, just at a
different (daily, not just weekly) threshold - `app/payroll/hours.py`
still hardcodes a single `FEDERAL_OVERTIME_MULTIPLIER` for all overtime
hours. California's double-time-past-12-hrs/day is a genuinely different
*multiplier* tier, not just a different threshold, and isn't modeled by
the current `(regular_hours, overtime_hours)` return shape. This is a
real gap in the mechanism, left unaddressed on purpose - building an
unused double-time bucket now, before any state that needs one is even
confirmed in scope, would be speculative work with nothing exercising it.
If a state requiring double time is ever actually turned on, `OvertimeRule.apply`'s
return shape needs a third bucket (or a list of `(hours, multiplier)`
pairs) at that point, alongside `hours_and_pay_for_period`'s pay
calculation.

## Recommended next steps (not engineering)

1. Decide which hubs need `Hub.state_code` populated, and populate it
   (a factual data-entry task, not a policy one).
2. Get real employment-law guidance on which of those states' overtime
   rules actually need to be turned on, and their precise terms - and
   confirm the workweek definition above, including the two weeks a year
   it is 167 or 169 hours.
3. Only then: implement the specific `OvertimeRule` subclass(es) against
   verified statute text, register them in `STATE_OVERTIME_RULES`, and -
   if double time is in scope - extend the return shape as noted above.
