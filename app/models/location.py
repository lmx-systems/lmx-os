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
from sqlalchemy import Float, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


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
    merged_into_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("locations.id"), nullable=True, index=True
    )

    @property
    def geocoded(self) -> bool:
        return self.lat is not None and self.lng is not None

    @property
    def is_merged(self) -> bool:
        return self.merged_into_id is not None
