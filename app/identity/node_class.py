"""What kind of place a dock is (`docs/ROADMAP_1.5.md` IDN-3).

Seven classes, taken from the roadmap: shop, parts store, dealer, body shop,
warehouse, transfer, municipal.

**Why this is worth a column of its own.** `PRD-1` is the payoff: batch value is
not uniform across node types. The sensitivity finding is +3-7% at
high-frequency shops against **+195-271% at warehouse and transfer nodes** - two
orders of magnitude apart. A batching model that cannot tell a warehouse from a
body shop is averaging over that gap, and the average is wrong everywhere.

**This classifier proposes; it does not know.** The only signal available today
is the account name and the address string - there is no industry code, no
customer-supplied type, nothing authoritative. Name matching is therefore a
first pass that gets the obvious majority and hands the rest to a person, the
same division of labour as IDN-2's merge queue and for the same reason: a wrong
label is invisible once set, while an absent one is visible and chased.

So an inferred label never overwrites a human one, and `node_class_source`
records which kind each is. The done-when - all ~230 classified, unlabelled
below 2% - is measured by `classification_coverage`, not asserted.
"""
import re

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.location import Location
from app.models.shop import Shop

NODE_CLASS_SHOP = "shop"
NODE_CLASS_PARTS_STORE = "parts_store"
NODE_CLASS_DEALER = "dealer"
NODE_CLASS_BODY_SHOP = "body_shop"
NODE_CLASS_WAREHOUSE = "warehouse"
NODE_CLASS_TRANSFER = "transfer"
NODE_CLASS_MUNICIPAL = "municipal"

NODE_CLASSES = (
    NODE_CLASS_SHOP,
    NODE_CLASS_PARTS_STORE,
    NODE_CLASS_DEALER,
    NODE_CLASS_BODY_SHOP,
    NODE_CLASS_WAREHOUSE,
    NODE_CLASS_TRANSFER,
    NODE_CLASS_MUNICIPAL,
)

SOURCE_INFERRED = "inferred"
SOURCE_HUMAN = "human"

# Ordered most specific first, and the order is load-bearing. "Smith Auto Body
# Shop" contains "shop"; it is a body shop, not a generic shop. "NAPA Auto Parts
# Warehouse" contains "parts"; it is a warehouse. The first pattern to match
# wins, so the generic classes have to come last.
#
# Patterns are word-boundary anchored. Substring matching would classify
# "Shopworth Ltd" as a shop and "Bodycote" as a body shop, and a wrong label is
# the expensive kind of error here.
_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        NODE_CLASS_WAREHOUSE,
        (r"\bwarehouse\b", r"\bdistribution\b", r"\bdc\b", r"\bfulfil?lment\b", r"\bdepot\b"),
    ),
    (
        NODE_CLASS_TRANSFER,
        (r"\btransfer\b", r"\bcross[- ]?dock\b", r"\bhub\b", r"\bterminal\b"),
    ),
    (
        NODE_CLASS_BODY_SHOP,
        (r"\bbody\s*shop\b", r"\bcollision\b", r"\bautobody\b", r"\bauto\s+body\b",
         r"\bpaint\s*(?:&|and)\s*body\b"),
    ),
    (
        NODE_CLASS_DEALER,
        (r"\bdealer(?:ship)?\b", r"\bmotors\b", r"\bautomotive\s+group\b",
         r"\bford\b", r"\btoyota\b", r"\bchevrolet\b", r"\bchevy\b", r"\bhonda\b",
         r"\bnissan\b", r"\bkia\b", r"\bhyundai\b", r"\bsubaru\b", r"\bmazda\b",
         r"\bbmw\b", r"\bmercedes\b", r"\bvolkswagen\b", r"\bvw\b"),
    ),
    (
        NODE_CLASS_MUNICIPAL,
        (r"\bcity\s+of\b", r"\bcounty\s+of\b", r"\bmunicipal\b", r"\bpublic\s+works\b",
         r"\bfire\s+dep(?:t|artment)\b", r"\bpolice\s+dep(?:t|artment)\b",
         r"\bschool\s+district\b", r"\bstate\s+of\b", r"\btransit\s+authority\b"),
    ),
    (
        NODE_CLASS_PARTS_STORE,
        (r"\bparts\b", r"\bnapa\b", r"\bautozone\b", r"\bo'?reilly\b",
         r"\badvance\s+auto\b", r"\bcarquest\b", r"\bsupply\b"),
    ),
    (
        NODE_CLASS_SHOP,
        (r"\bshop\b", r"\bgarage\b", r"\bservice\s+cent(?:er|re)\b", r"\brepair\b",
         r"\btire\b", r"\btyre\b", r"\bmuffler\b", r"\btransmission\b", r"\blube\b"),
    ),
)

_COMPILED = tuple(
    (node_class, tuple(re.compile(p, re.IGNORECASE) for p in patterns))
    for node_class, patterns in _RULES
)


def infer_node_class(*texts: str | None) -> tuple[str, str] | None:
    """Best guess at what kind of place this is, with the evidence for it.

    Returns `(node_class, matched_text)` or None when nothing matches. The
    matched text is returned because a reviewer correcting a label needs to see
    what the machine keyed on - "classified dealer on 'ford'" is checkable in a
    second, "classified dealer" is not.

    Pass account names. **Do not pass an address** - see
    `classify_unlabelled_locations`. Multiple names are tried in order and the
    first match wins, so pass them in a deterministic order.
    """
    for text in texts:
        if not text:
            continue
        for node_class, patterns in _COMPILED:
            for pattern in patterns:
                found = pattern.search(text)
                if found:
                    return node_class, found.group(0)
    return None


async def classify_unlabelled_locations(
    session: AsyncSession, *, limit: int = 1000
) -> dict:
    """Label every dock we can from the names of the shops that point at it.

    Only touches docks with no label. A human label is never overwritten, and
    neither is an earlier inferred one - re-running this should be idempotent
    rather than a re-roll, so that a reviewer's correction survives the next run
    and so that two runs cannot disagree.

    Returns counts rather than the rows: this is a batch job, and the useful
    output is how much of the gap it closed.
    """
    unlabelled = list(
        await session.scalars(
            select(Location)
            .where(Location.node_class.is_(None), Location.merged_into_id.is_(None))
            .limit(limit)
        )
    )

    labelled = 0
    for location in unlabelled:
        names = list(
            await session.scalars(
                select(Shop.name)
                .where(Shop.location_id == location.id)
                # Ordered so a dock with two accounts gets the same label every
                # run. Unordered, "Main Street Shop" and "Smith Collision" would
                # label the dock `shop` or `body_shop` depending on heap order.
                .order_by(Shop.created_at, Shop.id)
            )
        )
        # Names only. The address is deliberately NOT classified: the rules
        # contain place-shaped tokens, and a street or city will trip them.
        # "1200 K St NW, Washington, DC" matches `\bdc\b` and would be labelled
        # a warehouse - the class PRD-1 treats as +195-271% batch value against
        # +3-7% for a shop. Napa CA would become a parts store, a street named
        # Ford a dealership. A street name is weak evidence of what trades on it
        # and the downside is not symmetric, so it is not evidence we use.
        guess = infer_node_class(*names)
        if guess is None:
            continue
        location.node_class, location.node_class_evidence = guess
        location.node_class_source = SOURCE_INFERRED
        labelled += 1

    if labelled:
        await session.flush()
    return {
        "considered": len(unlabelled),
        "labelled": labelled,
        "still_unlabelled": len(unlabelled) - labelled,
    }


def set_node_class(location: Location, node_class: str) -> Location:
    """A person's label. Outranks anything inferred, and is never overwritten.

    Raises on an unknown class rather than storing it: the seven classes are
    what `PRD-1` groups by, and an eighth appearing silently would split a group
    without anyone noticing.
    """
    if node_class not in NODE_CLASSES:
        raise ValueError(
            f"{node_class!r} is not one of the seven node classes: {NODE_CLASSES}"
        )
    location.node_class = node_class
    location.node_class_source = SOURCE_HUMAN
    location.node_class_evidence = None
    return location


async def classification_coverage(session: AsyncSession) -> dict:
    """IDN-3's done-when, measured rather than asserted.

    "All ~230 accounts classified; unlabelled below 2%" is about accounts, so
    the denominator is shops that reach a dock - not docks, of which there are
    fewer. A shop with no dock at all (the `N/A` row) cannot be classified and
    is counted separately rather than being quietly dropped from the
    denominator, which would flatter the number.
    """
    total_shops = await session.scalar(select(func.count()).select_from(Shop)) or 0
    without_dock = (
        await session.scalar(
            select(func.count()).select_from(Shop).where(Shop.location_id.is_(None))
        )
        or 0
    )
    classified = (
        await session.scalar(
            select(func.count())
            .select_from(Shop)
            .join(Location, Shop.location_id == Location.id)
            .where(Location.node_class.is_not(None))
        )
        or 0
    )

    unlabelled = total_shops - classified
    return {
        "shops": total_shops,
        "classified": classified,
        "unlabelled": unlabelled,
        "without_dock": without_dock,
        "unlabelled_pct": (unlabelled / total_shops * 100) if total_shops else 0.0,
        "meets_target": total_shops > 0 and (unlabelled / total_shops) < 0.02,
    }
