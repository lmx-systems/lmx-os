"""AGT-1's harness, against invented places in invented towns.

`ml/agt1/book.py` is the one loader in this repository that must carry customer
names, because *"are these two the same dock"* is a question about names and the
export holds no street address to ask it with. The discipline that keeps
`ml/real/export.py` clean therefore moves out one level: the real file stays in
gitignored `lmx-dwell/`, and **every fixture here is invented** - invented
businesses, invented towns, invented postcodes.

What is being tested is the measurement, not the resolvers. A harness that
scores wrong makes a good resolver look bad and a bad one shippable, and it is
the only thing in `AGT-1` that nobody will check by eye.
"""
from __future__ import annotations

import csv

import pytest

from ml.agt1.agent import AgentResolver, Unavailable, availability
from ml.agt1.book import Account, load_book
from ml.agt1.gold import GoldLabels, read_labels, write_label_file
from ml.agt1.pool import MAX_BLOCK_SIZE, build_pool, pair_key, pool_for_bakeoff
from ml.agt1.report import render
from ml.agt1.resolvers import DeterministicResolver, Proposal, full_space_proposals
from ml.agt1.score import Scorecard, disagreements, score


def account(receiver_id, name, city="Arden", zip_code="90210", stops=1):
    return Account(
        receiver_id=receiver_id, name=name, city=city, zip_code=zip_code, stops=stops
    )


def write_book(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["receiver_id", "receiver_name", "city", "zip"]
        )
        writer.writeheader()
        writer.writerows(rows)
    return path


class TestTheBook:
    def test_one_row_per_account_with_its_stop_count(self, tmp_path):
        path = write_book(
            tmp_path / "book.csv",
            [
                {"receiver_id": "10/0", "receiver_name": "Larkspur Panel", "city": "Arden", "zip": "90210"},
                {"receiver_id": "10/0", "receiver_name": "Larkspur Panel", "city": "Arden", "zip": "90210"},
                {"receiver_id": "11/0", "receiver_name": "Quillon Tyres", "city": "Arden", "zip": "90210"},
            ],
        )

        book = load_book(path)

        assert [a.receiver_id for a in book] == ["10/0", "11/0"]
        assert [a.stops for a in book] == [2, 1]

    def test_the_commonest_spelling_wins_not_the_first(self, tmp_path):
        # A name mistyped on one manifest out of two hundred must not become the
        # account's identity - every downstream comparison is against this string.
        path = write_book(
            tmp_path / "book.csv",
            [{"receiver_id": "10/0", "receiver_name": "Larskpur Panel", "city": "Arden", "zip": "90210"}]
            + [
                {"receiver_id": "10/0", "receiver_name": "Larkspur Panel", "city": "Arden", "zip": "90210"}
                for _ in range(3)
            ],
        )

        assert load_book(path)[0].name == "Larkspur Panel"

    def test_a_tie_resolves_the_same_way_every_run(self, tmp_path):
        # The bake-off is re-run. A book that reshuffles between runs would move
        # both resolvers' scores for reasons that are not about either resolver.
        path = write_book(
            tmp_path / "book.csv",
            [
                {"receiver_id": "10/0", "receiver_name": "Beta Body", "city": "Arden", "zip": "90210"},
                {"receiver_id": "10/0", "receiver_name": "Alpha Body", "city": "Arden", "zip": "90210"},
            ],
        )

        assert load_book(path)[0].name == "Alpha Body"

    def test_the_postcode_ends_the_address(self, tmp_path):
        # `account_signals.zip_of` anchors to the end of the string. Town first
        # or the postcode is never read.
        assert account("10/0", "Larkspur Panel").address == "Arden, 90210"

    def test_an_account_with_neither_town_nor_postcode_has_an_empty_address(self):
        assert account("10/0", "Larkspur Panel", city="", zip_code="").address == ""


class TestThePool:
    def test_a_shared_account_root_pools_a_pair(self):
        pool = build_pool([account("10/0", "Larkspur Panel"), account("10/1", "Larkspur Body")])

        assert pair_key("10/0", "10/1") in pool.pairs

    def test_a_shared_postcode_pools_a_pair(self):
        pool = build_pool(
            [account("10/0", "Larkspur Panel"), account("77/3", "Quillon Tyres")]
        )

        assert pair_key("10/0", "77/3") in pool.pairs

    def test_two_accounts_with_nothing_in_common_are_not_pooled(self):
        pool = build_pool(
            [
                account("10/0", "Larkspur Panel", city="Arden", zip_code="90210"),
                account("77/3", "Quillon Tyres", city="Brill", zip_code="80111"),
            ]
        )

        assert pool.pairs == set()

    def test_a_rare_shared_word_pools_a_pair_across_towns(self):
        pool = build_pool(
            [
                account("10/0", "Larkspur Panel", city="Arden", zip_code="90210"),
                account("77/3", "Larkspur Repairs", city="Brill", zip_code="80111"),
            ]
        )

        assert pair_key("10/0", "77/3") in pool.pairs

    def test_a_trade_word_does_not_pool_a_pair(self):
        # "auto" and "parts" are in `_COMMON_TOKENS`. Two businesses sharing them
        # share nothing that identifies either.
        pool = build_pool(
            [
                account("10/0", "Arden Auto Parts", city="Arden", zip_code="90210"),
                account("77/3", "Brill Auto Parts", city="Brill", zip_code="80111"),
            ]
        )

        assert pool.pairs == set()

    def test_a_block_bigger_than_a_link_is_refused_and_named(self):
        # One warehouse postcode against four hundred accounts would otherwise
        # turn the pool back into the full quadratic and eat a week of somebody's
        # life without saying so.
        crowd = [account(f"{i}/0", f"Business {i}") for i in range(MAX_BLOCK_SIZE + 5)]

        pool = build_pool(crowd)

        assert pool.oversized
        assert all("postcode 90210" not in reason for reason in pool.reasons.values())

    def test_an_account_with_no_location_is_reported_not_dropped(self):
        pool = build_pool(
            [account("10/0", "Larkspur Panel", city="", zip_code=""), account("77/3", "Quillon Tyres")]
        )

        assert pool.unlocatable == ["10/0"]

    def test_the_reason_a_pair_was_pooled_is_kept(self):
        # A reviewer reading "same account root" is doing a different job from
        # one reading "both in 90210".
        pool = build_pool([account("10/0", "Larkspur Panel"), account("10/1", "Larkspur Body")])

        reasons = pool.reasons[pair_key("10/0", "10/1")]
        assert any("account root" in reason for reason in reasons)


class TestTheIncumbent:
    def test_it_proposes_a_shared_stem(self):
        book = [account("10/0", "Larkspur Panel"), account("10/0-A1", "Larkspur Panel")]
        by_id = {a.receiver_id: a for a in book}

        verdicts = DeterministicResolver().propose(by_id, [pair_key("10/0", "10/0-A1")])

        assert verdicts[pair_key("10/0", "10/0-A1")].tier == "HIGH"

    def test_the_pool_would_miss_proposals_the_blocks_cannot_reach(self):
        # Why `pool_for_bakeoff` exists. Two names alike enough for the weak
        # tier, different account roots, different postcodes, and their shared
        # word made ordinary by thirteen other accounts carrying it - so no
        # block links them and the incumbent proposes them anyway. On the real
        # book this is twenty pairs.
        #
        # The crowd makes both shared words ordinary: fifteen accounts carry
        # `larkspur` and fifteen carry `panel`, so neither is rare enough to
        # block on, which is exactly how a real trade word behaves.
        crowd = [
            account(f"{100 + i}/0", f"Larkspur Panel Depot {i}", city="Crale", zip_code=f"7{i:04d}")
            for i in range(13)
        ]
        odd_couple = [
            account("10/0", "Larkspur Panel Beating", city="Arden", zip_code="90210"),
            account("77/3", "Larkspur Panel Beaters", city="Brill", zip_code="80111"),
        ]
        book = crowd + odd_couple
        pair = pair_key("10/0", "77/3")

        assert pair in full_space_proposals(book)
        assert pair not in build_pool(book).pairs
        assert pair in pool_for_bakeoff(book).pairs

    def test_the_union_records_why_such_a_pair_is_being_asked_about(self):
        book = [
            account("10/0", "Larkspur Panel", city="Arden", zip_code="90210"),
            account("10/0-A1", "Larkspur Panel", city="Arden", zip_code="90210"),
        ]

        pool = pool_for_bakeoff(book)

        assert pool.reasons[pair_key("10/0", "10/0-A1")]


class TestTheLabelFile:
    def _pooled(self):
        book = [account("10/0", "Larkspur Panel"), account("10/1", "Larkspur Body")]
        return book, build_pool(book)

    def test_it_never_shows_what_a_resolver_concluded(self, tmp_path):
        # The whole measurement rests on this. A reviewer shown the incumbent's
        # answer agrees with it, and the truth set drifts toward the incumbent
        # until its precision approaches 1.000 by construction.
        book, pool = self._pooled()
        path = tmp_path / "truth.csv"

        write_label_file(path, accounts=book, pool=pool)

        header = path.read_text().splitlines()[0]
        assert "deterministic" not in header
        assert "tier" not in header
        assert "same_place" in header

    def test_it_refuses_to_overwrite_a_days_work(self, tmp_path):
        book, pool = self._pooled()
        path = tmp_path / "truth.csv"
        write_label_file(path, accounts=book, pool=pool)

        with pytest.raises(FileExistsError):
            write_label_file(path, accounts=book, pool=pool)

    def test_the_order_is_the_same_every_time(self, tmp_path):
        # A half-finished pass must survive the file being regenerated.
        book = [account(f"{i}/0", f"Larkspur {i}") for i in range(20)]
        pool = build_pool(book)

        first = tmp_path / "a.csv"
        second = tmp_path / "b.csv"
        write_label_file(first, accounts=book, pool=pool)
        write_label_file(second, accounts=book, pool=pool)

        assert first.read_text() == second.read_text()

    def test_a_blank_verdict_is_unlabelled_not_a_no(self, tmp_path):
        book, pool = self._pooled()
        path = tmp_path / "truth.csv"
        write_label_file(path, accounts=book, pool=pool)

        labels = read_labels(path)

        assert labels.unlabelled == {pair_key("10/0", "10/1")}
        assert labels.different == set()
        assert labels.coverage == 0.0

    def test_an_unreadable_verdict_raises_rather_than_counting_as_no(self, tmp_path):
        # Silently reading "maybe" as "different places" turns a reviewer's
        # uncertainty into a resolver's false positive.
        path = tmp_path / "truth.csv"
        path.write_text(
            "same_place,account_a,account_b\nmaybe,10/0,10/1\n", encoding="utf-8"
        )

        with pytest.raises(ValueError, match="maybe"):
            read_labels(path)

    def test_it_takes_the_spellings_a_tired_reviewer_types(self, tmp_path):
        path = tmp_path / "truth.csv"
        path.write_text(
            "same_place,account_a,account_b\n"
            "Y,10/0,10/1\nyes,11/0,11/1\nN,12/0,12/1\n?,13/0,13/1\n",
            encoding="utf-8",
        )

        labels = read_labels(path)

        assert labels.same == {pair_key("10/0", "10/1"), pair_key("11/0", "11/1")}
        assert labels.different == {pair_key("12/0", "12/1")}
        assert labels.undecidable == {pair_key("13/0", "13/1")}
        assert labels.coverage == 1.0


class TestScoring:
    def _book(self):
        return [
            account("10/0", "Larkspur Panel", stops=400),
            account("10/1", "Larkspur Body", stops=30),
            account("77/3", "Quillon Tyres", stops=5),
            account("88/0", "Marlow Castings", stops=3),
        ]

    def test_precision_and_recall_on_merge_decisions(self):
        labels = GoldLabels(
            same={pair_key("10/0", "10/1"), pair_key("77/3", "88/0")},
            different={pair_key("10/0", "77/3")},
            undecidable=set(),
            unlabelled=set(),
        )
        proposals = {
            pair_key("10/0", "10/1"): Proposal("HIGH", ""),   # right
            pair_key("10/0", "77/3"): Proposal("WEAK", ""),   # wrong
        }                                                      # 77/3~88/0 missed

        card = score(resolver="x", proposals=proposals, labels=labels, accounts=self._book())

        assert card.precision == 0.5
        assert card.recall == 0.5
        assert card.f1 == 0.5

    def test_a_pair_the_reviewer_could_not_decide_scores_against_nobody(self):
        labels = GoldLabels(
            same=set(), different=set(), undecidable={pair_key("10/0", "77/3")}, unlabelled=set()
        )

        card = score(
            resolver="x",
            proposals={pair_key("10/0", "77/3"): Proposal("WEAK", "")},
            labels=labels,
            accounts=self._book(),
        )

        assert card.false_positives == set()
        assert card.on_undecidable == {pair_key("10/0", "77/3")}
        assert card.precision is None

    def test_the_dock_count_follows_the_transitive_closure(self):
        # A resolver saying 10/0=10/1 and 10/1=77/3 has said there is one dock,
        # whether or not it ever mentioned 10/0=77/3. Pairwise scores cannot see
        # this and the roadmap item is about the dock count.
        labels = GoldLabels(same=set(), different=set(), undecidable=set(), unlabelled=set())

        card = score(
            resolver="x",
            proposals={
                pair_key("10/0", "10/1"): Proposal("HIGH", ""),
                pair_key("10/1", "77/3"): Proposal("HIGH", ""),
            },
            labels=labels,
            accounts=self._book(),
        )

        assert card.implied_dock_count == 2  # {10/0, 10/1, 77/3} and {88/0}

    def test_splitting_and_welding_have_opposite_signs(self):
        labels = GoldLabels(
            same={pair_key("10/0", "10/1")}, different=set(), undecidable=set(), unlabelled=set()
        )

        missed = score(resolver="x", proposals={}, labels=labels, accounts=self._book())
        welded = score(
            resolver="y",
            proposals={
                pair_key("10/0", "10/1"): Proposal("HIGH", ""),
                pair_key("77/3", "88/0"): Proposal("HIGH", ""),
            },
            labels=labels,
            accounts=self._book(),
        )

        assert missed.dock_count_error > 0   # left split
        assert welded.dock_count_error < 0   # welded two real places together

    def test_an_error_is_weighed_by_the_stops_it_moves(self):
        # A wrong merge between a 400-stop dock and a 30-stop one corrupts a
        # per-dock statistic. The same wrong merge between two 3-stop accounts is
        # a rounding error, and a count of errors cannot tell them apart.
        labels = GoldLabels(
            same=set(),
            different={pair_key("10/0", "10/1"), pair_key("77/3", "88/0")},
            undecidable=set(),
            unlabelled=set(),
        )

        big = score(
            resolver="x",
            proposals={pair_key("10/0", "10/1"): Proposal("HIGH", "")},
            labels=labels,
            accounts=self._book(),
        )
        small = score(
            resolver="y",
            proposals={pair_key("77/3", "88/0"): Proposal("HIGH", "")},
            labels=labels,
            accounts=self._book(),
        )

        assert len(big.false_positives) == len(small.false_positives) == 1
        assert big.stops_wrongly_merged == 30   # the smaller side is what moves
        assert small.stops_wrongly_merged == 3


class TestHeadToHead:
    def test_declining_a_pair_correctly_counts_as_getting_it_right(self):
        # The done-when asks for "the cases each gets right that the other does
        # not". A pair one resolver correctly refused while the other proposed it
        # wrongly is exactly that, and a report scoring only proposals misses it.
        labels = GoldLabels(
            same=set(), different={pair_key("10/0", "77/3")}, undecidable=set(), unlabelled=set()
        )
        book = [account("10/0", "Larkspur Panel"), account("77/3", "Quillon Tyres")]

        careless = score(
            resolver="careless",
            proposals={pair_key("10/0", "77/3"): Proposal("WEAK", "")},
            labels=labels,
            accounts=book,
        )
        careful = score(resolver="careful", proposals={}, labels=labels, accounts=book)

        split = disagreements(careless, careful, labels)

        assert split.only_right_correct == {pair_key("10/0", "77/3")}
        assert split.only_left_correct == set()

    def test_both_wrong_is_reported_separately(self):
        labels = GoldLabels(
            same={pair_key("10/0", "77/3")}, different=set(), undecidable=set(), unlabelled=set()
        )
        book = [account("10/0", "Larkspur Panel"), account("77/3", "Quillon Tyres")]

        left = score(resolver="a", proposals={}, labels=labels, accounts=book)
        right = score(resolver="b", proposals={}, labels=labels, accounts=book)

        assert disagreements(left, right, labels).both_wrong == {pair_key("10/0", "77/3")}


class TestTheChallenger:
    def test_it_will_not_run_without_the_data_release(self):
        # Every pair sent is two of the design partner's customers, by name and
        # town, leaving our infrastructure for a third party.
        blocked = availability(release_approved=False)

        assert isinstance(blocked, Unavailable)
        assert "0.2" in blocked.reason or "data-rights" in blocked.reason

    def test_it_will_not_run_without_a_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        blocked = availability(release_approved=True)

        assert blocked is not None and "ANTHROPIC_API_KEY" in blocked.reason

    def test_it_is_never_shown_what_the_incumbent_said(self):
        sent = []

        def transport(body):
            sent.append(body)
            return {"content": [{"type": "text", "text": "[]"}]}

        book = {a.receiver_id: a for a in [account("10/0", "Larkspur Panel"), account("10/1", "Larkspur Body")]}
        AgentResolver(transport=transport).propose(book, [pair_key("10/0", "10/1")])

        payload = str(sent[0])
        assert "HIGH" not in payload and "deterministic" not in payload

    def test_only_same_is_a_proposal(self):
        def transport(body):
            return {
                "content": [
                    {
                        "type": "text",
                        "text": '[{"id": 0, "verdict": "same", "why": "one place"},'
                        ' {"id": 1, "verdict": "unclear", "why": "cannot tell"}]',
                    }
                ]
            }

        book = {
            a.receiver_id: a
            for a in [account("10/0", "A"), account("10/1", "B"), account("77/3", "C")]
        }
        verdicts = AgentResolver(transport=transport).propose(
            book, [pair_key("10/0", "10/1"), pair_key("10/1", "77/3")]
        )

        assert set(verdicts) == {pair_key("10/0", "10/1")}

    def test_a_fenced_reply_still_parses(self):
        def transport(body):
            return {
                "content": [
                    {"type": "text", "text": '```json\n[{"id": 0, "verdict": "same"}]\n```'}
                ]
            }

        book = {a.receiver_id: a for a in [account("10/0", "A"), account("10/1", "B")]}

        assert AgentResolver(transport=transport).propose(book, [pair_key("10/0", "10/1")])

    def test_an_unparseable_batch_costs_that_batch_and_not_the_run(self):
        # The conservative direction: the agent is credited with nothing there,
        # which costs it recall. Abandoning the run would lose the batches that
        # did parse.
        def transport(body):
            return {"content": [{"type": "text", "text": "I think they might be..."}]}

        book = {a.receiver_id: a for a in [account("10/0", "A"), account("10/1", "B")]}

        assert AgentResolver(transport=transport).propose(book, [pair_key("10/0", "10/1")]) == {}


class TestTheReport:
    def test_a_contestant_that_could_not_run_gets_a_row_not_an_omission(self):
        # `ml/m1/` learned this the expensive way. A report that silently prints
        # one column when it was built to print two is how an unrun challenger
        # becomes a challenger that lost.
        book = [account("10/0", "Larkspur Panel"), account("10/1", "Larkspur Body")]

        text = render(
            accounts=book,
            pool=build_pool(book),
            labels=GoldLabels(same=set(), different=set(), undecidable=set(), unlabelled=set()),
            cards=[Scorecard(resolver="deterministic")],
            unavailable={"agent": "ANTHROPIC_API_KEY is not set"},
        )

        assert "DID NOT RUN" in text
        assert "ANTHROPIC_API_KEY" in text

    def test_an_unfinished_truth_file_says_so_above_the_numbers(self):
        book = [account("10/0", "Larkspur Panel"), account("10/1", "Larkspur Body")]

        text = render(
            accounts=book,
            pool=build_pool(book),
            labels=GoldLabels(
                same=set(), different=set(), undecidable=set(), unlabelled={pair_key("10/0", "10/1")}
            ),
            cards=[Scorecard(resolver="deterministic")],
            unavailable={},
        )

        assert "labelled part only" in text


class TestChaining:
    """The failure pairwise scoring cannot see, and the reason clusters are scored.

    Found on the real book before a single label existed: the incumbent's 97
    pairwise proposals close transitively into a dock holding **14 accounts** -
    a municipal catch-all suffix bucket chained into two county departments and
    an unrelated business - and a second holding **nine** repair shops in seven
    different towns. Every edge in both chains is individually defensible. The
    closure is not, and `precision` was blind to all of it.
    """

    def test_a_defensible_chain_closes_into_an_indefensible_dock(self):
        book = [account(f"{i}/0", f"Business {i}", stops=1) for i in range(4)]
        labels = GoldLabels(same=set(), different=set(), undecidable=set(), unlabelled=set())

        card = score(
            resolver="chainy",
            proposals={
                pair_key("0/0", "1/0"): Proposal("HIGH", ""),
                pair_key("1/0", "2/0"): Proposal("HIGH", ""),
                pair_key("2/0", "3/0"): Proposal("HIGH", ""),
            },
            labels=labels,
            accounts=book,
        )

        assert card.largest_implied_cluster == 4
        assert card.implied_dock_count == 1

    def test_the_report_raises_it_above_two(self):
        # Two accounts merging is the ordinary case and needs no alarm. Three is
        # a chain, and a chain is the thing worth a sentence.
        book = [account(f"{i}/0", f"Business {i}") for i in range(3)]

        text = render(
            accounts=book,
            pool=build_pool(book),
            labels=GoldLabels(same=set(), different=set(), undecidable=set(), unlabelled=set()),
            cards=[Scorecard(resolver="chainy", largest_implied_cluster=3)],
            unavailable={},
        )

        assert "largest implied dock holds 3 accounts" in text


class TestPartialTruth:
    def test_the_dock_delta_is_withheld_until_the_file_is_finished(self):
        # `docks` closes over every proposal; the truth closes over the labelled
        # yeses. On a part-labelled file the gap between them is mostly pairs
        # nobody has reached, and printing it as Δdocks reads as a resolver's
        # error rather than as unfinished work.
        book = [account("10/0", "Larkspur Panel"), account("10/1", "Larkspur Body")]

        text = render(
            accounts=book,
            pool=build_pool(book),
            labels=GoldLabels(
                same={pair_key("10/0", "10/1")},
                different=set(),
                undecidable=set(),
                unlabelled={pair_key("10/0", "77/3")},
            ),
            cards=[Scorecard(resolver="deterministic", implied_dock_count=1, true_dock_count=1)],
            unavailable={},
        )

        assert "only when the\nfile is finished" in text or "file is finished" in text
        assert "     ?" in text


class TestTheIncumbentsVocabulary:
    """The resolver reads the book it is judging, and can still be asked not to.

    `IDN-2`'s detector now derives this customer's place words from this
    customer's addresses, rather than carrying a hardcoded list of towns that
    would silently stop working for the next one. The bake-off has to be able to
    score both readings against the same labels — otherwise "the incumbent"
    means whichever version happened to be checked out.
    """

    def test_it_derives_the_place_words_from_the_book_it_is_given(self):
        book = [
            account("900/0", "Ardenhoe Auto Body", city="Ardenhoe", zip_code="99001"),
            account("901/0", "Ardenhoe Auto Bodyworks", city="Ardenhoe", zip_code="99001"),
        ]

        # `ardenhoe` is in both addresses, so it identifies neither business.
        assert full_space_proposals(book) == {}

    def test_a_name_the_addresses_never_use_still_identifies(self):
        book = [
            account("900/0", "Arturo Auto Body", city="Ardenhoe", zip_code="99001"),
            account("901/0", "Arturo Auto Bodyworks", city="Ardenhoe", zip_code="99001"),
        ]

        assert pair_key("900/0", "901/0") in full_space_proposals(book)

    def test_the_older_reading_can_still_be_scored(self):
        # Passing the static vocabulary explicitly runs the incumbent as it was
        # before the corpus reading existed — the same labels, the other
        # contestant, which is the only way a change like this is measurable
        # rather than asserted.
        from app.identity.account_signals import STATIC_VOCABULARY

        book = [
            account("900/0", "Ardenhoe Auto Body", city="Ardenhoe", zip_code="99001"),
            account("901/0", "Ardenhoe Auto Bodyworks", city="Ardenhoe", zip_code="99001"),
        ]
        by_id = {a.receiver_id: a for a in book}
        pair = pair_key("900/0", "901/0")

        older = DeterministicResolver(vocabulary=STATIC_VOCABULARY)

        assert pair in older.propose(by_id, [pair])
        assert pair not in DeterministicResolver().propose(by_id, [pair])
