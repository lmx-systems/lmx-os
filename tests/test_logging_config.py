"""
_forward_to_sentry (app/logging_config.py) - the structlog processor that
forwards warning/error/critical/exception-level structlog events to
Sentry, closing the gap Sentry's default LoggingIntegration can't (see
that function's own docstring): this codebase's structlog setup never
touches Python's stdlib logging module at all, so that hook would never
fire regardless of Sentry being configured.
"""
from unittest.mock import MagicMock, patch

from app.logging_config import _forward_to_sentry, configure_logging


def test_warning_level_forwards_as_a_captured_message():
    with patch("app.logging_config.sentry_sdk") as mock_sentry:
        event_dict = {"event": "something_happened", "level": "warning", "timestamp": "t"}
        result = _forward_to_sentry(None, "warning", event_dict)

    mock_sentry.capture_message.assert_called_once_with("something_happened", level="warning")
    assert result == event_dict  # unchanged - the pipeline still continues to the renderer


def test_exception_level_forwards_as_a_captured_exception():
    with patch("app.logging_config.sentry_sdk") as mock_sentry:
        _forward_to_sentry(None, "exception", {"event": "handler_failed", "level": "error"})

    mock_sentry.capture_exception.assert_called_once_with()


def test_info_and_debug_levels_are_not_forwarded():
    with patch("app.logging_config.sentry_sdk") as mock_sentry:
        _forward_to_sentry(None, "info", {"event": "routine"})
        _forward_to_sentry(None, "debug", {"event": "very routine"})

    mock_sentry.capture_message.assert_not_called()
    mock_sentry.capture_exception.assert_not_called()


def test_structured_context_fields_become_sentry_extras_but_bookkeeping_fields_dont():
    with patch("app.logging_config.sentry_sdk") as mock_sentry:
        scope = MagicMock()
        mock_sentry.push_scope.return_value.__enter__.return_value = scope
        _forward_to_sentry(
            None, "warning",
            {"event": "msg", "level": "warning", "timestamp": "t", "hub_id": "hub-1", "driver_id": "d-1"},
        )

    scope.set_extra.assert_any_call("hub_id", "hub-1")
    scope.set_extra.assert_any_call("driver_id", "d-1")
    # event/level/timestamp are structlog's own bookkeeping fields, not
    # meaningful context - they shouldn't be duplicated as extras too.
    assert scope.set_extra.call_count == 2


def test_configure_logging_initializes_sentry_when_dsn_configured():
    # structlog.configure mocked out too - it sets *global* structlog
    # state, which would otherwise leak into every other test in the
    # suite regardless of pytest's usual isolation.
    with (
        patch("app.logging_config.settings") as mock_settings,
        patch("app.logging_config.sentry_sdk") as mock_sentry,
        patch("app.logging_config.structlog.configure"),
    ):
        mock_settings.sentry_dsn = "https://fake@sentry.example.com/1"
        mock_settings.environment = "production"
        mock_settings.sentry_traces_sample_rate = 0.1
        mock_settings.log_level = "INFO"
        configure_logging()

    mock_sentry.init.assert_called_once()
    _, kwargs = mock_sentry.init.call_args
    assert kwargs["dsn"] == "https://fake@sentry.example.com/1"
    assert kwargs["environment"] == "production"
    assert kwargs["traces_sample_rate"] == 0.1


def test_configure_logging_skips_sentry_init_when_unconfigured():
    with (
        patch("app.logging_config.settings") as mock_settings,
        patch("app.logging_config.sentry_sdk") as mock_sentry,
        patch("app.logging_config.structlog.configure"),
    ):
        mock_settings.sentry_dsn = None
        mock_settings.log_level = "INFO"
        configure_logging()

    mock_sentry.init.assert_not_called()
