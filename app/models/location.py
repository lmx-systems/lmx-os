"""A physical dock: one row per place a driver actually goes.

`docs/ROADMAP_1.5.md` IDN-1, and §2.2(b) is the decision this implements.

**A Shop is a customer account. A Location is a physical dock.** The whole
entity-resolution problem exists because those two have been the same field.
The design partner's export has 230 customer IDs resolving to 224 names and an
unknown, smaller number of actual places: five records for one body shop across
two ID roots, one shop entered twice with its city spelled using zeros for the
letter O, one record whose city, state and zip are literally `N/A`. Until that
collapses to one row per dock, every per-dock number we compute is wrong, and
one such number has already reached investor materials and been withdrawn.

So the relationship is many-to-one: several `Shop` rows may point at the same
`Location`. Nothing is merged the other way - a Location never absorbs a Shop's
commercial identity, because two accounts at one dock may bill differently, sit
under different clients, and end at different times.

**What this table deliberately does not hold yet.** No node class (IDN-3), no
dwell/hours/access/autonomy profile (IDN-4). Both hang off `Location` when they
are built - §2.2(b) is explicit that the receiver profile attaches here and
never to `Shop` - but neither has a caller today, and the repo's rule is no
abstraction without two live callers.

**Resolution key.** `normalized_address`, produced by the same conservative
normalizer the geocode cache uses (`app/geocoding/base.py::normalize_address`).
That function case-folds and collapses whitespace but refuses to canonicalize
"St" against "Street", and the asymmetry behind that choice applies here with
more force than it does to a cache: under-normalizing costs a duplicate dock
that IDN-2's merge queue will catch and a human will collapse, while
over-normalizing silently fuses two real places that differ by a house number,
and nothing downstream can detect it. A duplicate is visible; a bad merge is
not. Per §2.2(c) the founding set is merged by a person for exactly this reason.
"""
import uuid

from sqlalchemy import Boolean, Float, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin

# The data engine's kinds of site (`docs/DATA_DICTIONARY.md`): taxonomy v0.1's
# five, and two the Experiment 0 workbook found outside it. Every writer of
# `kind_of_site` - the import, the desk, the door survey - draws from this.
SITE_KINDS = (
    "independent_repair_or_tire",
    "body_shop",
    "dealership_counter",
    "parts_store_counter",
    "fleet_or_transport_yard",
    "municipal_fleet_garage",
    "gas_station_with_bays",
)
# `ck_locations_setting` (migration 0069).
SETTINGS = ("city_core", "suburb")
# The three site facts a record's end needs besides its door, each with its own
# source in `site_fact_sources`.
SITE_FACTS = ("kind_of_site", "setting", "shares_address")


class Location(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "locations"

    # The identity key. Unique, so two concurrent resolutions of the same
    # address are a conflict the database refuses rather than two docks that
    # then have to be merged back together.
    # `unique=True` alone: Postgres backs a unique constraint with its own index,
    # so adding `index=True` would build a second index on the same column that
    # serves no query the first does not.
    normalized_address: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True
    )

    # The address as somebody actually wrote it, kept for display and for the
    # case where a resolution looks wrong and the question is whether the input
    # or the normalizer was at fault. First writer wins; later Shops resolving
    # to this dock do not overwrite it, because there is no basis for preferring
    # one spelling of the same place over another.
    address: Mapped[str] = mapped_column(String(255), nullable=False)

    # Null together when we have no coordinates for the dock. A Location exists
    # whether or not it geocoded - identity is not conditional on a third-party
    # service answering, and the `N/A` address in the export still names a real
    # place somebody delivered to.
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Set when this dock has been declared the same place as another (IDN-2).
    # The row is kept rather than deleted, and that is the alias map: its
    # `normalized_address` stays a live lookup key, so the next shop that arrives
    # spelled this way lands on the canonical dock instead of recreating this one.
    #
    # Deleting the absorbed row would undo the merge on the next import, which is
    # how de-duplication efforts usually fail.
    merged_into_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("locations.id"), nullable=True, index=True
    )

    # What kind of place this is (IDN-3): shop, parts store, dealer, body shop,
    # warehouse, transfer, municipal. Nullable because an unlabelled dock is an
    # honest state - IDN-3's target is under 2% unlabelled, not zero.
    #
    # It lives on Location rather than Shop because it describes the place, not
    # the billing relationship: two accounts at one dock are the same kind of
    # place. PRD-1 groups by this, and the gap it is there to expose is large -
    # +3-7% batch value at high-frequency shops against +195-271% at warehouse
    # and transfer nodes.
    node_class: Mapped[str | None] = mapped_column(String(24), nullable=True, index=True)

    # Whether a person set this or the classifier guessed it. An inferred label
    # is never allowed to overwrite a human one, so the two have to be
    # distinguishable.
    node_class_source: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # For an inferred label, the text the classifier matched on. A reviewer
    # correcting a label needs to see what it keyed on - "dealer, on 'ford'" is
    # checkable at a glance, "dealer" is not. Null for a human label, which is
    # its own evidence.
    node_class_evidence: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # --- The golden record's Site layer (`docs/ROADMAP_1.5.md` DE-1, D-GR) ----
    # Kind of site is the data engine's taxonomy code - nine kinds, from a
    # listing and confirmed on site. It sits beside `node_class` rather than
    # replacing it: node class is the dispatch-side coarse class IDN-3 infers
    # from names, and the two vocabularies have not been reconciled.
    kind_of_site: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # city_core | suburb, from the density rule. With kind of site it makes the
    # "group" accuracy is reported per.
    setting: Mapped[str | None] = mapped_column(String(12), nullable=True)
    # Whose test pool the site may count toward: records from NJ never count
    # toward a Texas pool (decision D-NJ).
    region: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Null until somebody checked; which neighbours, in `site_address_shares`.
    shares_address: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Where the site fields came from: listing, technician, desk, import.
    site_source: Mapped[str | None] = mapped_column(String(24), nullable=True)
    # Where each site fact came from - kind from the import, setting from the
    # desk, the shared-address answer from the door survey - as {fact: source}.
    # Every writer sets its fact's entry, even when the value is unchanged
    # (`app/identity/site_facts.py::set_site_fact`); a fact written before that,
    # with no entry, has `site_source`. A record whose site counts only thanks
    # to a fact whose entry is `desk` is graded bronze at most (DE-15, migration
    # 0080), so a person confirming the desk's value lifts the cap.
    site_fact_sources: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    # Who gave each fact's answer at the door, and later answers that disagreed
    # (migration 0081), as {fact: {value, by, at, stop_id, disputes: [...]}}.
    # An answer fills only an open fact - unknown, or only the desk's - so a
    # disagreeing one is kept here for a person to settle, never applied.
    site_fact_answers: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), nullable=True)

    @property
    def geocoded(self) -> bool:
        return self.lat is not None and self.lng is not None

    @property
    def is_merged(self) -> bool:
        return self.merged_into_id is not None
