"""Subscribable ICS calendars for San Francisco events.

References:
- RFC 5545 iCalendar: https://www.rfc-editor.org/rfc/rfc5545
- RFC 7986 new iCalendar properties: https://www.rfc-editor.org/rfc/rfc7986
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

__version__ = "0.1.0"

TZ = ZoneInfo("America/Los_Angeles")
USER_AGENT = f"sf-cal/{__version__} (github.com/nredd/sf-cal)"
