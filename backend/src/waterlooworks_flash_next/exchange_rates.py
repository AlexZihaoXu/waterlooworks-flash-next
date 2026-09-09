"""Bounded, public Bank of Canada rates; never sends posting data."""

import json
import re
import threading
import time
import urllib.request
from datetime import date
from decimal import Decimal

URL = (
    "https://www.bankofcanada.ca/valet/observations/group/FX_RATES_DAILY/json?recent=1"
)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: object, fp: object, code: int, msg: str, headers: object, newurl: str
    ) -> None:
        return None


def parse_rates(value: object, today: date) -> dict[str, object]:
    if not isinstance(value, dict) or not isinstance(value.get("observations"), list):
        raise ValueError("Invalid exchange-rate data")
    observations = value["observations"]
    rows = [
        row
        for row in observations
        if isinstance(row, dict) and isinstance(row.get("d"), str)
    ]
    latest = max(rows, key=lambda row: row["d"])
    observed = date.fromisoformat(latest["d"])
    if not 0 <= (today - observed).days <= 10:
        raise ValueError("Exchange-rate date unavailable")
    rates = {"CAD": "1"}
    for key, item in latest.items():
        if re.fullmatch(r"FX[A-Z]{3}CAD", key) and isinstance(item, dict):
            number = item.get("v")
            if (
                isinstance(number, str)
                and re.fullmatch(r"\d+(?:\.\d+)?", number)
                and len(number) <= 30
                and Decimal(number) > 0
            ):
                rates[key[2:5]] = number
    if "USD" not in rates:
        raise ValueError("USD reference rate unavailable")
    return {"as_of": observed.isoformat(), "cad_per_unit": rates}


class ExchangeRates:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cached: dict[str, object] | None = None
        self._expires = 0.0

    def read(self) -> dict[str, object]:
        with self._lock:
            if self._cached is not None and time.monotonic() < self._expires:
                return self._cached
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}), _NoRedirect()
            )
            with opener.open(URL, timeout=8) as response:
                raw = response.read(262145)
            if len(raw) > 262144:
                raise ValueError("Exchange-rate response too large")
            result = parse_rates(json.loads(raw), date.today())
            self._cached = result
            self._expires = time.monotonic() + 3600
            return result
