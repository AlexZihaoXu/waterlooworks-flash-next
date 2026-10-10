"""Bounded, public Bank of Canada rates; never sends posting data."""

import json
import re
import threading
import time
import urllib.request
from collections.abc import Callable
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


class ExchangeRatesUnavailable(Exception):
    """Fixed-message failure; never carries upstream response or error details."""

    def __init__(self) -> None:
        super().__init__("Reference exchange rates are unavailable.")


def fetch_rates() -> dict[str, object]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    with opener.open(URL, timeout=8) as response:
        raw = response.read(262145)
    if len(raw) > 262144:
        raise ValueError("Exchange-rate response too large")
    return parse_rates(json.loads(raw), date.today())


class ExchangeRates:
    """Single-flight reader: one network fetch at a time, outside the state lock.

    Successes are reused for an hour and failures for a minute, so an outage
    answers immediately instead of queueing every caller behind the timeout.
    """

    SUCCESS_TTL = 3600.0
    FAILURE_TTL = 60.0
    FOLLOWER_WAIT = 15.0

    def __init__(
        self,
        fetch: Callable[[], dict[str, object]] = fetch_rates,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetch = fetch
        self._clock = clock
        self._lock = threading.Lock()
        self._cached: dict[str, object] | None = None
        self._expires = 0.0
        self._failed_until = 0.0
        self._inflight: threading.Event | None = None

    def read(self) -> dict[str, object]:
        while True:
            with self._lock:
                now = self._clock()
                if self._cached is not None and now < self._expires:
                    return self._cached
                if now < self._failed_until:
                    raise ExchangeRatesUnavailable
                pending = self._inflight
                if pending is None:
                    pending = self._inflight = threading.Event()
                    leader = True
                else:
                    leader = False
            if not leader:
                if not pending.wait(self.FOLLOWER_WAIT):
                    raise ExchangeRatesUnavailable
                continue
            failed = True
            try:
                result = self._fetch()
                failed = False
            except Exception:
                raise ExchangeRatesUnavailable from None
            finally:
                with self._lock:
                    now = self._clock()
                    if failed:
                        self._failed_until = now + self.FAILURE_TTL
                    else:
                        self._cached = result
                        self._expires = now + self.SUCCESS_TTL
                        self._failed_until = 0.0
                    self._inflight = None
                pending.set()
            return result
