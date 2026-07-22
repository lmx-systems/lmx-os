"""
app/schemas/driver_app.py validation - security-review hardening (S6):
CompleteStopBody.method and DriverAvailabilityUpdate.status used to be
unconstrained `str` fields, documented as one of a few values in a comment
only; SendMessageBody.body was an unbounded string stored in a Text column
and forwarded straight to the SMS client. None of these were exploitable
beyond data-integrity/cost concerns (see the review), but should be
rejected at the API boundary rather than merely documented.
"""
import pytest
from pydantic import ValidationError

from app.schemas.driver_app import CompleteStopBody, DriverAvailabilityUpdate, SendMessageBody


@pytest.mark.parametrize("method", ["photo", "signature", "pin"])
def test_complete_stop_body_accepts_real_methods(method):
    CompleteStopBody(method=method)  # must not raise


def test_complete_stop_body_rejects_an_unknown_method():
    with pytest.raises(ValidationError):
        CompleteStopBody(method="whatever")


@pytest.mark.parametrize("status", ["available", "off_shift", "on_break", "en_route"])
def test_driver_availability_update_accepts_real_statuses(status):
    DriverAvailabilityUpdate(status=status)  # must not raise


def test_driver_availability_update_rejects_an_unknown_status():
    with pytest.raises(ValidationError):
        DriverAvailabilityUpdate(status="whatever")


def test_send_message_body_accepts_a_normal_message():
    SendMessageBody(body="Running about 10 minutes behind, sorry!")  # must not raise


def test_send_message_body_rejects_an_oversized_message():
    with pytest.raises(ValidationError):
        SendMessageBody(body="x" * 1601)
