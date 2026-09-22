"""What actually says two accounts are one dock, in the data we have.

IDN-2's first detector compared normalized addresses and coordinates. Run
against the design partner's real export, that finds almost nothing: address
normalisation collapses 230 accounts to 229, and the export carries no
coordinates at all. The signal is somewhere else.

**Where it is.** The distributor's account ids are structured -
`ROOT/BRANCH[-SUFFIX]` - and the structure carries meaning. Two accounts
sharing a root and branch and differing only by suffix are near-certainly one
place. Two sharing a root but not a branch are the interesting case: it may be
a second dock of the same business, or the same dock invoiced twice, and only a
person can tell. Beyond that, an identical name in the same postcode is strong,
and a name similarity that rests on a *rare* token is worth a look.

These rules are not invented here. They are what an earlier ad-hoc pass over
this export actually found duplicates with - 93 candidate pairs across 48
clusters - and this module is that analysis turned into code that runs on new
data instead of on one afternoon's CSV.

**Tiers, and why they are in the reason string.** A reviewer facing 93 pairs
needs to do the confident ones first; a flat queue gets cleared rather than
read. The tier is prefixed onto `LocationMerge.reason` rather than given its own
column, because the column would be a migration and the reason is already the
thing a reviewer reads. If the queue ever needs sorting or filtering by tier in
SQL, that is the moment to promote it.
"""
import re
from difflib import SequenceMatcher

TIER_HIGH = "HIGH"
TIER_REVIEW = "REVIEW"
TIER_WEAK = "WEAK"

# `1234/5`, `1234/5-A1`, `123456/5-A12`. Whitespace tolerated because export
# columns are not trimmed consistently.
_ACCOUNT_REF = re.compile(
    r"^\s*(?P<root>\d+)\s*/\s*(?P<branch>\d+)\s*(?:-\s*(?P<suffix>[A-Za-z0-9]+))?\s*$"
)

# A US zip at the end of an address string, five digits with an optional plus-4.
# Anchored to the end so a house number cannot be read as a postcode.
_ZIP = re.compile(r"(?<!\d)(\d{5})(?:-\d{4})?\s*$")

# Words that carry no identifying weight. A shared "auto" or "inc" between two
# names says nothing; a shared rare token says a great deal, and the difference
# is what makes name similarity usable rather than noise.
#
# **The second block was measured, not guessed.** `AGT-1`'s harness closed this
# resolver's proposals transitively and found nine repair shops in seven towns
# welded into one dock. The cause was `repair` - the commonest trade word in a
# body-shop book - missing from the first block, so
# `distinctive_tokens("T & J AUTO REPAIR")` is `{"repair"}`: the initials are one
# character and dropped, `auto` is common, and the one surviving token is the
# trade itself. Every "X & Y AUTO REPAIR" therefore had 1.00 distinctive-word
# overlap with every other, which is precisely the signal `rare_token_overlap`
# exists to avoid.
#
# The rest of the block comes from ranking every token in the real book by how
# many accounts carry it, the same discipline `IDN-3` used to exhaust its rules.
# `repair` 19 accounts, `body` 12, `county` 12, `dpw` 7, `boro` 6, `dba` 5,
# `collision` 4, `township` 4, `care` 4 - trade words and municipal category
# words, none of which identifies anybody.
_COMMON_TOKENS = frozenset(
    {
        "auto", "automotive", "parts", "supply", "service", "services", "center",
        "centre", "inc", "llc", "ltd", "co", "corp", "company", "the", "and",
        "of", "shop", "garage", "sales", "group", "enterprises", "brothers",
        "bros", "son", "sons", "motor", "motors", "car", "cars", "truck",
        # Measured against the real book - see above.
        "repair", "repairs", "body", "collision", "care", "dba",
        "county", "township", "boro", "borough", "dpw",
    }
)

# **A known remaining weakness, deliberately not fixed with this list.** The same
# ranking shows town names used inside business names - `englewood` on 14
# accounts, `bergen` 9, `teaneck` 8, `hudson` 7, `hackensack` 6. They identify
# no better than `auto` does, and they are not on this list because they must
# not be: the towns are this customer's towns, and a hardcoded list of them
# would silently stop working for the next customer while looking like it
# worked. The right fix is corpus frequency - which is what `ml/agt1/pool.py`'s
# `RARE_TOKEN_MAX_ACCOUNTS` already does for blocking - and it needs this
# function to see the book rather than one pair.

NAME_SIMILARITY_THRESHOLD = 0.75
RARE_TOKEN_THRESHOLD = 0.40


class AccountRef:
    """A parsed distributor account id."""

    __slots__ = ("root", "branch", "suffix")

    def __init__(self, root: str, branch: str, suffix: str | None):
        self.root, self.branch, self.suffix = root, branch, suffix

    @property
    def stem(self) -> str:
        """Root and branch together - the part that names a place."""
        return f"{self.root}/{self.branch}"


def parse_account_ref(raw: str | None) -> AccountRef | None:
    """Split `ROOT/BRANCH-SUFFIX`, or None when the id is not that shape.

    Returning None rather than guessing matters: two ids we cannot parse are
    two ids we know nothing about, and treating an unparseable pair as matching
    would merge on the absence of evidence.
    """
    if not raw:
        return None
    found = _ACCOUNT_REF.match(raw)
    if not found:
        return None
    return AccountRef(found["root"], found["branch"], found["suffix"])


def zip_of(address: str | None) -> str | None:
    """The five-digit postcode at the end of an address, if there is one."""
    if not address:
        return None
    found = _ZIP.search(address.strip())
    return found.group(1) if found else None


def name_tokens(name: str | None) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z0-9]+", (name or "").casefold()))


def distinctive_tokens(name: str | None) -> frozenset[str]:
    """Tokens that actually identify. Digits kept - a branch number is rare."""
    return frozenset(t for t in name_tokens(name) if t not in _COMMON_TOKENS and len(t) > 2)


def rare_token_overlap(left: str | None, right: str | None) -> float:
    """Jaccard over distinctive tokens only.

    Two body shops both called "... Auto Parts Inc" overlap heavily on common
    words and share nothing that identifies them. This measures the part that
    does.
    """
    a, b = distinctive_tokens(left), distinctive_tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def normalised_name(name: str | None) -> str:
    """Case- and punctuation-insensitive, for exact-name comparison."""
    return " ".join(sorted(name_tokens(name)))


def why_these_accounts_might_be_one_place(
    *,
    ref_a: str | None,
    name_a: str | None,
    address_a: str | None,
    ref_b: str | None,
    name_b: str | None,
    address_b: str | None,
) -> tuple[str, str] | None:
    """`(tier, reason)` if this pair is worth a person's attention, else None.

    Ordered by confidence, first match wins, so the reason a reviewer reads is
    the strongest one that applies rather than an arbitrary one.
    """
    parsed_a, parsed_b = parse_account_ref(ref_a), parse_account_ref(ref_b)
    zip_a, zip_b = zip_of(address_a), zip_of(address_b)
    same_zip = zip_a is not None and zip_a == zip_b
    names_agree = bool(name_a) and normalised_name(name_a) == normalised_name(name_b)

    if parsed_a and parsed_b and parsed_a.stem == parsed_b.stem:
        if names_agree:
            return TIER_HIGH, "same account root and branch, names agree"
        if distinctive_tokens(name_a) & distinctive_tokens(name_b):
            # Differing only by suffix, names differ but overlap. Still the same
            # stem, which is the distributor's own statement that these are one
            # place, and a shared word says the two spellings are of it.
            return TIER_HIGH, "same account root and branch, differing suffix"
        # Same stem, and the two names share **nothing**. `AGT-1` found why this
        # matters: one stem in the real book is a catch-all bucket for
        # miscellaneous accounts, holding a municipal DPW, a county department
        # and an unrelated business. Calling those HIGH chained four
        # organisations into one dock by transitive closure, and every edge
        # looked defensible to the reviewer who saw only that pair.
        #
        # Demoted rather than refused. The stem is real evidence and a business
        # does get renamed, so this belongs in front of a person - just not at
        # the top of their list, and not without saying what to check.
        return TIER_REVIEW, (
            "same account root and branch, but the names share no word - "
            "may be a catch-all account rather than one place"
        )

    if names_agree and same_zip:
        return TIER_HIGH, "identical name in the same postcode"

    if parsed_a and parsed_b and parsed_a.root == parsed_b.root:
        # The genuinely ambiguous case, and the one §2.2(c) exists for: a second
        # branch may be a second dock of the same business, or the same dock
        # invoiced twice. Nothing in the data settles it.
        return TIER_REVIEW, "same account root, different branch - may be a second dock"

    if names_agree:
        return TIER_REVIEW, "identical name, different postcode - may be a chain"

    # Two independent signals, both required. Rare-token overlap alone puts 131
    # pairs in front of a reviewer on the real book, most of them two unrelated
    # businesses that happen to share one uncommon word - and a queue that is
    # two-thirds noise gets cleared rather than read. Whole-string similarity
    # and distinctive-token overlap fail in different ways, so requiring both
    # is a real filter rather than a tighter threshold on the same thing.
    overlap = rare_token_overlap(name_a, name_b)
    similarity = SequenceMatcher(None, (name_a or "").casefold(), (name_b or "").casefold()).ratio()
    if overlap >= RARE_TOKEN_THRESHOLD and similarity >= NAME_SIMILARITY_THRESHOLD:
        tier = TIER_REVIEW if same_zip else TIER_WEAK
        where = "same postcode" if same_zip else "no postcode agreement"
        return tier, (
            f"names {similarity:.2f} alike, {overlap:.2f} distinctive-word overlap, {where}"
        )

    return None
