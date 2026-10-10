"""Reader concurrency contracts with opaque sentinels; no network or rate fixtures."""

import threading

import pytest

from waterlooworks_flash_next.exchange_rates import (
    ExchangeRates,
    ExchangeRatesUnavailable,
)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_concurrent_readers_share_one_fetch_performed_outside_the_lock() -> None:
    started = threading.Event()
    release = threading.Event()
    calls = 0
    sentinel: dict[str, object] = {"opaque": object()}

    def fetch() -> dict[str, object]:
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(5)
        return sentinel

    rates = ExchangeRates(fetch=fetch)
    results: list[dict[str, object]] = []

    def reader() -> None:
        results.append(rates.read())

    leader = threading.Thread(target=reader)
    leader.start()
    assert started.wait(5)
    # The reader's state lock must be free while the network call is pending.
    assert rates._lock.acquire(timeout=1)
    rates._lock.release()
    followers = [threading.Thread(target=reader) for _ in range(4)]
    for thread in followers:
        thread.start()
    release.set()
    for thread in [leader, *followers]:
        thread.join(5)
    assert calls == 1
    assert len(results) == 5 and all(result is sentinel for result in results)


def test_failures_are_remembered_for_sixty_seconds() -> None:
    clock = Clock()
    calls = 0

    def fetch() -> dict[str, object]:
        nonlocal calls
        calls += 1
        raise OSError("unreachable")

    rates = ExchangeRates(fetch=fetch, clock=clock)
    with pytest.raises(ExchangeRatesUnavailable):
        rates.read()
    clock.now += 59
    with pytest.raises(ExchangeRatesUnavailable):
        rates.read()
    assert calls == 1
    clock.now += 2
    with pytest.raises(ExchangeRatesUnavailable):
        rates.read()
    assert calls == 2


def test_success_is_cached_for_an_hour() -> None:
    clock = Clock()
    calls = 0
    sentinel: dict[str, object] = {"opaque": object()}

    def fetch() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return sentinel

    rates = ExchangeRates(fetch=fetch, clock=clock)
    assert rates.read() is sentinel
    clock.now += 3599
    assert rates.read() is sentinel and calls == 1
    clock.now += 2
    assert rates.read() is sentinel and calls == 2
