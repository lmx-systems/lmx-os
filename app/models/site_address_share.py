"""Which other businesses share a site's address.

`docs/ROADMAP_1.5.md` DE-1. The definitions doc asks of every site whether it
shares its address with other businesses, and which ones: Experiment 0 found 15
accounts sharing an address, and the field log has five shippers on one dock.
`Location.shares_address` holds the yes/no (null until somebody checked); this
holds the "which ones", one row per pair, recorded from the site that was
checked. Not symmetric by construction - a technician at one site names the
neighbours they saw.
"""
import uuid

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin


class SiteAddressShare(Base, TimestampMixin):
    __tablename__ = "site_address_shares"

    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("locations.id"), primary_key=True)
    shares_with_location_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("locations.id"), primary_key=True
    )
