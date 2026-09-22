"""IDN-2's real duplicate signals, as found in the design partner's export.

The first detector compared normalized addresses and coordinates. On the real
book that finds almost nothing - address normalisation collapses 230 accounts
to 229, and the export carries no coordinates at all. These are the signals
that do fire, and the shapes here are the shapes the file actually contains.

No customer data. The account ids follow the real grammar and the names are
invented, because a fixture that carried the design partner's account book
would put it in a public repository (CLAUDE.md's naming rule).
"""
import pytest

from app.identity.account_signals import (
    NAME_SIMILARITY_THRESHOLD,
    RARE_TOKEN_THRESHOLD,
    TIER_HIGH,
    TIER_REVIEW,
    TIER_WEAK,
    parse_account_ref,
    rare_token_overlap,
    why_these_accounts_might_be_one_place,
    zip_of,
)


class TestAccountRef:
    """`ROOT/BRANCH[-SUFFIX]` - 230 of them in the real file, four shapes."""

    @pytest.mark.parametrize(
        "raw,root,branch,suffix",
        [
            ("1234/5", "1234", "5", None),
            ("1234/5-A1", "1234", "5", "A1"),
            ("1234/5-A12", "1234", "5", "A12"),
            ("123/4", "123", "4", None),
            ("123456/5-A1", "123456", "5", "A1"),
            ("  1234 / 5 - A1  ", "1234", "5", "A1"),
        ],
    )
    def test_the_real_shapes_parse(self, raw, root, branch, suffix):
        ref = parse_account_ref(raw)
        assert (ref.root, ref.branch, ref.suffix) == (root, branch, suffix)

    @pytest.mark.parametrize("raw", ["", None, "not-an-id", "1234", "1234-5", "abc/def"])
    def test_anything_else_is_refused_rather_than_guessed(self, raw):
        """Two ids we cannot parse are two ids we know nothing about.

        Treating an unparseable pair as matching would merge on the absence of
        evidence, which is the one thing a reference table must never do.
        """
        assert parse_account_ref(raw) is None


class TestZip:
    @pytest.mark.parametrize(
        "address,expected",
        [
            ("Some Shop, Springfield, 99001", "99001"),
            ("Some Shop, Springfield, 99001-1234", "99001"),
            ("Some Shop, Springfield", None),
            ("", None),
            (None, None),
        ],
    )
    def test_reads_the_trailing_postcode(self, address, expected):
        assert zip_of(address) == expected

    def test_a_house_number_is_not_a_postcode(self):
        """Anchored to the end for exactly this reason."""
        assert zip_of("12345 Main Street, Springfield") is None


class TestRareTokenOverlap:
    def test_shared_boilerplate_counts_for_nothing(self):
        """Two unrelated businesses both called "... Auto Parts Inc"."""
        assert rare_token_overlap("Northside Auto Parts Inc", "Southside Auto Parts Inc") == 0.0

    def test_a_shared_distinctive_word_counts(self):
        assert rare_token_overlap("Kowalczyk Auto", "Kowalczyk Automotive") == pytest.approx(1.0)


class TestWhyTheseMightBeOnePlace:
    def _verdict(self, **kwargs):
        base = dict(
            ref_a=None, name_a=None, address_a=None,
            ref_b=None, name_b=None, address_b=None,
        )
        return why_these_accounts_might_be_one_place(**{**base, **kwargs})

    def test_same_stem_differing_suffix_is_high(self):
        """The distributor's own id says these are one place."""
        tier, reason = self._verdict(
            ref_a="1234/5", name_a="Kowalczyk Auto", address_a="x, 99001",
            ref_b="1234/5-A1", name_b="Kowalczyk Auto Annex", address_b="x, 99001",
        )
        assert tier == TIER_HIGH
        assert "root and branch" in reason

    def test_a_catch_all_stem_is_demoted_rather_than_called_high(self):
        """Same stem, and the names share nothing at all.

        `AGT-1` closed this resolver's proposals transitively and found a dock
        holding a municipal DPW, a county department and an unrelated business.
        One stem in the real book is a **catch-all bucket** for miscellaneous
        accounts, so the distributor's id is not a statement that these are one
        place — and every edge in the resulting chain looked defensible to the
        reviewer who saw only that pair.

        Demoted, not refused: the stem is real evidence and a business does get
        renamed. It belongs in front of a person, just not at the top of the
        list and not without saying what to check.
        """
        tier, reason = self._verdict(
            ref_a="1960/0-A30", name_a="Arturo", address_a="x, 99001",
            ref_b="1960/0-A45", name_b="Vosberg County Admin", address_b="y, 99002",
        )
        assert tier == TIER_REVIEW
        assert "catch-all" in reason

    def test_a_shared_word_keeps_a_differing_suffix_high(self):
        """A renamed or re-spelled dock still reads as one place.

        The demotion above must not catch the ordinary case, which is most of
        them: one stem, two spellings, a word in common.
        """
        tier, _ = self._verdict(
            ref_a="1841/1", name_a="Kowalczyk Road Auto Body", address_a="x, 99001",
            ref_b="1841/1-A1", name_b="Kowalczyk Road Body Shop", address_b="y, 99002",
        )
        assert tier == TIER_HIGH

    def test_the_trade_itself_is_not_a_distinctive_word(self):
        """`repair` was missing from `_COMMON_TOKENS` and the cost was nine docks.

        `distinctive_tokens("T & J Auto Repair")` was `{"repair"}`: the initials
        are one character and dropped, `auto` is common, and the one surviving
        token is the trade. Every *"X & Y Auto Repair"* therefore had 1.00
        distinctive-word overlap with every other, and the transitive closure
        welded nine unrelated shops in seven towns into one place.
        """
        assert rare_token_overlap("T & J Auto Repair", "V & M Auto Repair") == 0.0
        assert self._verdict(
            ref_a="1360/0", name_a="T & J Auto Repair", address_a="Ardenhoe, 99001",
            ref_b="1402/0", name_b="V & M Auto Repair", address_b="Brillmoor, 99002",
        ) is None

    def test_the_municipal_category_words_identify_nobody_either(self):
        """Two towns' public works departments are not one dock."""
        assert self._verdict(
            ref_a="1960/0-A53", name_a="Ardenhoe DPW", address_a="Ardenhoe, 99001",
            ref_b="2490/0-A1", name_b="Brillmoor DPW", address_b="Brillmoor, 99002",
        ) is None

    def test_identical_name_in_one_postcode_is_high(self):
        tier, _ = self._verdict(
            ref_a="1111/1", name_a="Kowalczyk Auto", address_a="a, 99001",
            ref_b="2222/2", name_b="KOWALCZYK  AUTO", address_b="b, 99001",
        )
        assert tier == TIER_HIGH

    def test_same_root_different_branch_is_review_not_high(self):
        """The case §2.2(c) exists for.

        A second branch may be a second dock of the same business or the same
        dock invoiced twice, and nothing in the data settles it. Calling this
        HIGH would invite a reviewer to wave through a merge that quietly
        fuses two real places.
        """
        tier, reason = self._verdict(
            ref_a="1234/5", name_a="Kowalczyk Auto", address_a="a, 99001",
            ref_b="1234/6", name_b="Kowalczyk Auto North", address_b="b, 99002",
        )
        assert tier == TIER_REVIEW
        assert "may be a second dock" in reason

    def test_identical_name_in_a_different_postcode_is_review(self):
        tier, reason = self._verdict(
            ref_a="1111/1", name_a="Kowalczyk Auto", address_a="a, 99001",
            ref_b="2222/2", name_b="Kowalczyk Auto", address_b="b, 99002",
        )
        assert tier == TIER_REVIEW
        assert "chain" in reason

    def test_a_single_loose_signal_is_not_enough(self):
        """The fix that took the real queue from 192 candidates to 78.

        These two share a distinctive word and read nothing alike. Overlap
        alone would have queued them; requiring similarity as well does not.
        """
        assert self._verdict(
            ref_a="1111/1", name_a="Kowalczyk Brothers Towing", address_a="a, 99001",
            ref_b="2222/2", name_b="Kowalczyk", address_b="b, 99002",
        ) is None

    def test_two_agreeing_signals_are_enough(self):
        tier, reason = self._verdict(
            ref_a="1111/1", name_a="Kowalczyk Automotive", address_a="a, 99001",
            ref_b="2222/2", name_b="Kowalczyk Automotiv", address_b="b, 99002",
        )
        assert tier == TIER_WEAK
        assert "alike" in reason

    def test_unrelated_accounts_are_not_proposed(self):
        assert self._verdict(
            ref_a="1111/1", name_a="Northside Auto Parts", address_a="a, 99001",
            ref_b="2222/2", name_b="Delgado Collision", address_b="b, 99002",
        ) is None

    def test_missing_ids_fall_through_to_the_name_signals(self):
        """An account book without structured ids still gets a queue."""
        tier, _ = self._verdict(
            name_a="Kowalczyk Auto", address_a="a, 99001",
            name_b="Kowalczyk Auto", address_b="b, 99001",
        )
        assert tier == TIER_HIGH

    def test_the_thresholds_are_what_the_reasons_claim(self):
        """Guards the two numbers the queue size is most sensitive to."""
        assert NAME_SIMILARITY_THRESHOLD == 0.75
        assert RARE_TOKEN_THRESHOLD == 0.40
