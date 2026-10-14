"""Singer entry point and secret-aware configuration."""
from __future__ import annotations

from singer_sdk import Tap
from singer_sdk.exceptions import ConfigValidationError
from singer_sdk.helpers.capabilities import TapCapabilities, PluginCapabilities

from tap_sanctionskit.streams import AuditStream, ResultsStream


class TapSanctionsKit(Tap):
    """Export retained SanctionsKit account results and human-review audit records."""

    name = "tap-sanctionskit"
    capabilities = [TapCapabilities.CATALOG, TapCapabilities.STATE, TapCapabilities.DISCOVER, PluginCapabilities.ABOUT]
    config_jsonschema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "api_key": {"type": "string", "minLength": 1, "pattern": r"^[!-~]+$", "not": {"pattern": r"\s"}, "secret": True, "writeOnly": True,
                        "description": "Account API key with results:read. Synthetic sandbox and paid production use separate keys."},
            "page_size": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
            "request_timeout": {"type": "integer", "minimum": 1, "maximum": 300, "default": 60,
                                "description": "Read timeout in seconds; connection timeout is 10 seconds."},
            "max_pages": {"type": "integer", "minimum": 1, "maximum": 1000000, "default": 10000,
                          "description": "Safety limit per stream; reaching it with a cursor fails the export instead of truncating it."},
            "audit_query": {"type": "string", "minLength": 1, "maxLength": 200, "secret": True, "writeOnly": True,
                            "description": "Optional retained-name or resource-ID audit search. Held unchanged across pages."},
            "audit_review": {"type": "string", "enum": ["open", "confirmed", "dismissed"],
                             "description": "Optional audit decision filter. unreviewed has no audit records and is not supported."},
        },
        "required": ["api_key"],
    }

    @classmethod
    def append_builtin_config(cls, config_jsonschema: dict) -> None:
        # Keep this extractor limited to its documented settings. Stream maps,
        # arbitrary expressions, remote batch storage and schema flattening are
        # deliberately not exposed by this initial version.
        return None

    def _validate_config(self, *, raise_errors: bool = True) -> list[str]:
        # JSON Schema diagnostics can quote an invalid secret value or whole config.
        try:
            super()._validate_config(raise_errors=True)
        except ConfigValidationError:
            message = "Invalid configuration; check the documented setting names, types and limits. Values are not logged."
            if raise_errors:
                raise ConfigValidationError(message) from None
            return [message]
        return []

    def discover_streams(self):
        return [ResultsStream(self), AuditStream(self)]
