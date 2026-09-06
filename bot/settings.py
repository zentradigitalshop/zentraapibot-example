"""Business settings, read from the database at runtime.

Same split as config.py describes: secrets stay in .env forever, and things
that change in response to the market — your markup, your minimum top-up —
live here, editable from the admin dashboard without a restart or an SSH
session.

FAILURE IS INVISIBLE, on purpose. If the settings table cannot be read, the
overlay is skipped and each setting's own `default_value` stands — never an
exception that stops the bot selling because one query timed out. The
tradeoff is real: a setting can silently serve last-known-good rather than
today's value. That is still the safer failure for a shop.

THE DATABASE VALIDATES. Bounds and type live in the row (see the migration),
not duplicated in this file, so the bot and the dashboard can never enforce
two different rules for the same setting.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import Any

log = logging.getLogger(__name__)


class Settings:
    def __init__(self, db):
        self._db = db
        self.values: dict[str, Any] = {}

    async def refresh(self) -> None:
        try:
            rows = await self._db.live_settings()
        except Exception:  # noqa: BLE001
            log.exception("Could not read settings; keeping last-known values.")
            return

        parsed: dict[str, Any] = {}
        for row in rows:
            raw = row["value"] if row["value"] is not None else row["default_value"]
            parsed[row["key"]] = _parse(row, raw)
        self.values = parsed

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def decimal(self, key: str, default: str) -> Decimal:
        value = self.values.get(key)
        if isinstance(value, Decimal):
            return value
        try:
            return Decimal(str(value if value is not None else default))
        except (InvalidOperation, TypeError):
            return Decimal(default)


def _parse(row: dict, raw: str) -> Any:
    value_type = row["value_type"]
    try:
        if value_type == "bool":
            return str(raw).strip().lower() in {"1", "true", "yes", "on"}
        if value_type == "int":
            return int(raw)
        if value_type == "decimal":
            value = Decimal(str(raw))
            lo, hi = row.get("min_value"), row.get("max_value")
            if lo is not None and value < Decimal(str(lo)):
                value = Decimal(str(lo))
            if hi is not None and value > Decimal(str(hi)):
                value = Decimal(str(hi))
            return value
        return str(raw)
    except (InvalidOperation, ValueError, TypeError):
        # A row that fails to parse falls back to ITS OWN default rather
        # than raising — one malformed value must not break every other
        # setting sharing this refresh.
        log.warning("Setting %r has an unparseable value %r; using default %r",
                   row["key"], raw, row["default_value"])
        return _parse(row, row["default_value"]) if raw != row["default_value"] else row["default_value"]
