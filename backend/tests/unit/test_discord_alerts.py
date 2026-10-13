"""Discord new-job alert ranking, fixed message format, delivery and dispatch.

Postings are structural records (generic titles, IDs and rates); webhook URLs
use random tokens; every HTTP exchange goes through an injected
``httpx.MockTransport``, so nothing contacts Discord.
"""

import json
import secrets
import sqlite3
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography.fernet import Fernet

from waterlooworks_flash_next.api_models import (
    CompensationRate,
    CountryInference,
    PostingResponse,
)
from waterlooworks_flash_next.compensation_sort import compensation_sort_for
from waterlooworks_flash_next.database import (
    StoredListing,
    open_database,
    save_listings,
    start_collection_run,
)
from waterlooworks_flash_next.discord_alerts import (
    DiscordAlerts,
    build_alert,
    build_test_message,
    configured_public_base_url,
    location_label,
    max_cad_per_hour,
    pay_label,
    rank_by_pay,
    send_webhook,
)
from waterlooworks_flash_next.discord_webhooks import WebhookStore
from waterlooworks_flash_next.refresh import RunOutcome

BASE = "https://flash.example"


def rate(
    maximum: str | None,
    *,
    minimum: str | None = None,
    bound: str = "range",
    currency: str | None = "CAD",
    period: str | None = "hour",
    hours: str | None = None,
) -> CompensationRate:
    return CompensationRate(
        bound_type=bound,  # type: ignore[arg-type]
        minimum=minimum,
        maximum=maximum,
        minimum_inclusive=None,
        maximum_inclusive=None,
        currency=currency,
        period=period,  # type: ignore[arg-type]
        hours_per_week=hours,
        payout_frequency=None,
        eligibility=None,
        source_field="structural",
        evidence_text="structural",
    )


def posting(
    posting_id: str,
    *,
    title: str | None = None,
    organization: str | None = None,
    city: str | None = None,
    rates: list[CompensationRate] | None = None,
    needs_review: bool = False,
    openings: object = None,
    applications: object = None,
    country: str | None = None,
    country_state: str = "accepted",
    arrangement: str | None = None,
) -> PostingResponse:
    return PostingResponse(
        posting_id=posting_id,
        listing=StoredListing(
            id=posting_id,
            title=title,
            organization=organization,
            city=city,
            openings=openings,  # type: ignore[arg-type]
            application_count=applications,  # type: ignore[arg-type]
        ),
        first_seen_at="2026-10-01T00:00:00+00:00",
        last_seen_at="2026-10-01T00:00:00+00:00",
        location=city,
        work_arrangement=arrangement,  # type: ignore[arg-type]
        country_inference=CountryInference(
            state=country_state,  # type: ignore[arg-type]
            country_code="AA" if country else None,
            country_name=country,
        ),
        compensation_sort=compensation_sort_for(rates, needs_review=needs_review)
        if rates is not None
        else None,
    )


def hourly(maximum: str, minimum: str | None = None, **kwargs: Any) -> PostingResponse:
    bound = "fixed" if minimum is None else "range"
    return posting(
        kwargs.pop("posting_id", "1"),
        rates=[
            rate(
                None if bound == "fixed" else maximum,
                minimum=maximum if bound == "fixed" else minimum,
                bound=bound,
                currency=kwargs.pop("currency", "CAD"),
            )
        ],
        **kwargs,
    )


# ----- ranking ---------------------------------------------------------------


def test_max_cad_per_hour_uses_the_highest_comparable_upper_bound() -> None:
    def top(rates: list[CompensationRate], **fx: str) -> Decimal | None:
        sort = compensation_sort_for(rates, needs_review=False)
        return max_cad_per_hour(sort, fx or None)

    assert top([rate(None, minimum="30", bound="fixed")]) == Decimal(30)
    assert top([rate("33.26", minimum="20")]) == Decimal("33.26")
    assert top([rate(None, minimum="1600", bound="fixed", period="week")]) == 40
    assert top([rate("6000", minimum="5000", period="month", hours="37.5")]) == (
        Decimal(6000) * 12 / 52 / Decimal("37.5")
    )
    assert top([rate("52000", minimum="52000", period="year")]) == 25
    assert top([rate("30", minimum="20", currency=None)]) == 30
    assert top([rate("30", minimum="20", currency="USD")]) is None
    assert top([rate("30", minimum="20", currency="USD")], USD="1.4") == 42
    assert top([rate(None, minimum="25", bound="minimum")]) is None
    assert top([rate("200", minimum="150", period="day")]) is None
    assert top([rate("20", minimum="18"), rate("24", minimum="22")]) == 24
    review = compensation_sort_for([rate("40", minimum="30")], needs_review=True)
    assert max_cad_per_hour(review, None) is None
    assert max_cad_per_hour(None, None) is None


def test_rank_by_pay_sorts_unpaid_last_and_breaks_ties_by_posting_id() -> None:
    ranked = rank_by_pay(
        [
            posting("9"),
            hourly("30", posting_id="20"),
            hourly("45", posting_id="3"),
            hourly("30", posting_id="11"),
            posting("2", rates=[rate(None, minimum="10", bound="minimum")]),
            hourly("30", currency="USD", posting_id="5"),
        ],
        {"USD": "1.6"},
    )
    assert [item.posting_id for item in ranked] == ["5", "3", "11", "20", "2", "9"]


# ----- presentation ----------------------------------------------------------


def test_pay_label_mirrors_the_web_hourly_display() -> None:
    def label(rates: list[CompensationRate] | None, review: bool = False) -> str:
        return pay_label(
            compensation_sort_for(rates, needs_review=review)
            if rates is not None
            else None
        )

    assert label(None) == "Not listed"
    assert label([]) == "Not listed"
    assert label([rate(None, minimum="32", bound="fixed")]) == "32 CAD/hr"
    assert label([rate("33.26", minimum="26.63")]) == "26.63–33.26 CAD/hr"
    assert label([rate("33.26", minimum="26.63", currency=None)]) == (
        "26.63–33.26 CAD/hr*"
    )
    assert label([rate("33.255", minimum="20.5")]) == "20.5–33.26 CAD/hr"
    assert label([rate(None, minimum="1600", bound="fixed", period="week")]) == (
        "40 CAD/hr"
    )
    assert label([rate(None, minimum="25", bound="minimum")]) == (
        "25 CAD/hr (lower bound)"
    )
    assert label([rate("25", bound="maximum")]) == "25 CAD/hr (upper bound)"
    assert label([rate("30", minimum="30", currency="USD")]) == "30 USD/hr"
    assert label([rate("200", minimum="200", period="day")]) == "200 CAD/day"
    assert label([rate("1234.5", minimum="1000", period="day")]) == (
        "1,000–1,234.5 CAD/day"
    )
    assert label([rate("20", minimum="18"), rate("30", currency="USD")]) == (
        "Mixed rates"
    )
    assert label([rate("40", minimum="30")], review=True) == "Needs pay review"


def test_location_combines_city_accepted_country_and_arrangement() -> None:
    assert (
        location_label(
            posting("1", city="City A", country="Country A", arrangement="Remote")
        )
        == "City A, Country A · Remote"
    )
    assert location_label(posting("1", city="City A")) == "City A"
    assert location_label(posting("1", country="Country A")) == "Country A"
    assert (
        location_label(
            posting("1", city="City A", country="Country A", country_state="pending")
        )
        == "City A"
    )
    assert location_label(posting("1", arrangement="Hybrid")) == "Hybrid"
    assert location_label(posting("1", city="   ")) == "Not listed"
    assert location_label(posting("1")) == "Not listed"


# ----- message format --------------------------------------------------------


def test_alert_matches_the_fixed_format() -> None:
    first = posting(
        "489801",
        title="Title A",
        organization="Organization A",
        city="City A",
        country="Country A",
        arrangement="Remote",
        rates=[rate(None, minimum="32", bound="fixed")],
        openings=3,
        applications="32",
    )
    second = posting(
        "489802",
        title="Title B",
        organization="Organization B",
        rates=[rate("33.26", minimum="26.63", currency=None)],
        openings=1,
        applications=1,
    )
    payload = build_alert([second, first], 664, base_url=BASE)
    assert payload == {
        "username": "WaterlooWorks Flash",
        "avatar_url": f"{BASE}/discord-avatar.png",
        "content": ":rotating_light: **New job alert**\n**2 new jobs**, ranked by pay",
        "embeds": [
            {
                "color": None,
                "fields": [
                    {
                        "name": "Title B",
                        "value": "**Organization B**\n\n"
                        f"#️⃣ **ID:** [#489802]({BASE}/?job=489802)\n"
                        "📍 **Location:** Not listed\n"
                        "💵 **Pay:** 26.63–33.26 CAD/hr\\*\n\n"
                        "🗓️ 1 opening\n💼 1 application",
                        "inline": True,
                    },
                    {
                        "name": "Title A",
                        "value": "**Organization A**\n\n"
                        f"#️⃣ **ID:** [#489801]({BASE}/?job=489801)\n"
                        "📍 **Location:** City A, Country A · Remote\n"
                        "💵 **Pay:** 32 CAD/hr\n\n"
                        "🗓️ 3 openings\n💼 32 applications",
                        "inline": True,
                    },
                    {
                        "name": "​",
                        "value": f"🔗 [See all new jobs on flash.example]({BASE}/)",
                        "inline": False,
                    },
                ],
                "footer": {"text": "2 new jobs · 664 jobs on the site"},
            }
        ],
        "allowed_mentions": {"parse": []},
    }


def test_alert_lists_only_the_top_five_and_summarises_the_rest() -> None:
    postings = [
        hourly(str(20 + index), posting_id=str(100 + index), title=f"Title {index}")
        for index in range(7)
    ]
    payload = build_alert(postings, 1234, base_url=BASE)
    assert payload["content"] == (
        ":rotating_light: **New job alert**\n**7 new jobs** · top 5 by pay"
    )
    embed = payload["embeds"][0]
    names = [field["name"] for field in embed["fields"]]
    assert names == ["Title 6", "Title 5", "Title 4", "Title 3", "Title 2", "​"]
    assert embed["footer"] == {
        "text": "Showing 5 of 7 new jobs · 1,234 jobs on the site"
    }


def test_single_job_and_missing_details_read_naturally() -> None:
    payload = build_alert([posting("7")], 1, base_url=None)
    assert payload["content"] == ":rotating_light: **New job alert**\n**1 new job**"
    assert "avatar_url" not in payload
    embed = payload["embeds"][0]
    assert embed["fields"] == [
        {
            "name": "Untitled posting",
            "value": "#️⃣ **ID:** #7\n📍 **Location:** Not listed\n"
            "💵 **Pay:** Not listed",
            "inline": True,
        }
    ]
    assert embed["footer"] == {"text": "1 new job · 1 job on the site"}


def test_source_text_is_escaped_and_cannot_mention() -> None:
    payload = build_alert(
        [
            posting(
                "8",
                title="**Bold** _x_ @everyone <@123>\n# [link](https://x.example)",
                organization="Org ~~a~~ `b` | > c",
                city="City: A",
            )
        ],
        1,
        base_url=BASE,
    )
    field = payload["embeds"][0]["fields"][0]
    assert field["name"] == (
        "\\*\\*Bold\\*\\* \\_x\\_ \\@everyone \\<\\@123\\> "
        "\\# \\[link\\]\\(https\\://x.example\\)"
    )
    assert field["value"].startswith("**Org \\~\\~a\\~\\~ \\`b\\` \\| \\> c**\n\n")
    assert "📍 **Location:** City\\: A\n" in field["value"]
    assert payload["allowed_mentions"] == {"parse": []}


def test_long_source_text_stays_within_discord_limits() -> None:
    long = "_" * 5000
    postings = [
        posting(
            str(index),
            title=long,
            organization=long,
            city=long,
            country=long,
            arrangement="Remote",
            rates=[rate("9" * 30, minimum="1" * 30)],
            openings=10**12,
            applications=10**12,
        )
        for index in range(1, 8)
    ]
    payload = build_alert(postings, 10**9, base_url=BASE)
    embed = payload["embeds"][0]
    total = len(embed["footer"]["text"])
    for field in embed["fields"]:
        assert len(field["name"]) <= 256
        assert len(field["value"]) <= 1024
        assert not field["name"].endswith("\\")
        total += len(field["name"]) + len(field["value"])
    assert total <= 6000
    assert len(payload["content"]) <= 2000


def test_test_message_is_fixed() -> None:
    assert build_test_message(BASE) == {
        "username": "WaterlooWorks Flash",
        "avatar_url": f"{BASE}/discord-avatar.png",
        "embeds": [
            {
                "title": "Test alert from WaterlooWorks Flash",
                "description": "This webhook is connected. New job alerts are "
                "sent here after each refresh that finds new jobs, listing the "
                "top 5 by pay.",
                "color": None,
            }
        ],
        "allowed_mentions": {"parse": []},
    }
    assert "avatar_url" not in build_test_message(None)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        ("https://flash.example", "https://flash.example"),
        ("https://flash.example/", "https://flash.example"),
        ("https://flash.example:8443", "https://flash.example:8443"),
        ("http://localhost:5173", "http://localhost:5173"),
        ("http://127.0.0.1:8080", "http://127.0.0.1:8080"),
    ],
)
def test_public_base_url_accepts_origins(value: str | None, expected: str) -> None:
    assert configured_public_base_url(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "flash.example",
        "http://flash.example",
        "https://flash.example/app",
        "https://flash.example?x=1",
        "https://flash.example#x",
        "https://user@flash.example",
        "ftp://flash.example",
        "https://",
    ],
)
def test_public_base_url_rejects_non_origins(value: str) -> None:
    with pytest.raises(ValueError) as error:
        configured_public_base_url(value)
    assert value not in str(error.value)


# ----- delivery --------------------------------------------------------------


def webhook_url(webhook_id: str = "1234567890") -> str:
    return f"https://discord.com/api/webhooks/{webhook_id}/{secrets.token_urlsafe(48)}"


def transport_of(
    *responses: httpx.Response | Exception,
) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    requests: list[httpx.Request] = []
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        result = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(result, Exception):
            raise result
        return result

    return httpx.MockTransport(handler), requests


def test_send_posts_json_without_following_redirects() -> None:
    url = webhook_url()
    transport, requests = transport_of(httpx.Response(204))
    assert send_webhook(url, {"content": "x"}, transport=transport) == "ok"
    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert str(requests[0].url) == url
    assert json.loads(requests[0].content) == {"content": "x"}
    redirect, requests = transport_of(
        httpx.Response(302, headers={"Location": "https://example.com/"})
    )
    assert send_webhook(url, {}, transport=redirect) == "failed"
    assert len(requests) == 1


@pytest.mark.parametrize(
    ("status", "expected"),
    [(200, "ok"), (404, "gone"), (401, "gone"), (400, "failed"), (500, "failed")],
)
def test_send_maps_statuses(status: int, expected: str) -> None:
    transport, _ = transport_of(httpx.Response(status))
    assert send_webhook(webhook_url(), {}, transport=transport) == expected


def test_send_honours_retry_after_once_with_a_cap() -> None:
    sleeps: list[float] = []
    transport, requests = transport_of(
        httpx.Response(429, headers={"Retry-After": "2.5"}), httpx.Response(204)
    )
    assert (
        send_webhook(webhook_url(), {}, transport=transport, sleep=sleeps.append)
        == "ok"
    )
    assert sleeps == [2.5] and len(requests) == 2
    sleeps.clear()
    transport, requests = transport_of(
        httpx.Response(429, headers={"Retry-After": "600"}),
        httpx.Response(429, headers={"Retry-After": "600"}),
    )
    assert (
        send_webhook(webhook_url(), {}, transport=transport, sleep=sleeps.append)
        == "failed"
    )
    assert sleeps == [10.0] and len(requests) == 2
    sleeps.clear()
    transport, _ = transport_of(
        httpx.Response(429, json={"retry_after": 1.25}), httpx.Response(204)
    )
    send_webhook(webhook_url(), {}, transport=transport, sleep=sleeps.append)
    assert sleeps == [1.25]


def test_send_failures_never_raise_or_leak_the_url() -> None:
    url = webhook_url()
    for error in (
        httpx.ConnectError("boom"),
        httpx.ReadTimeout("slow"),
        RuntimeError(url),
    ):
        transport, _ = transport_of(error)
        assert send_webhook(url, {}, transport=transport) == "failed"


# ----- dispatch ---------------------------------------------------------------


@pytest.fixture
def path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WW_CREDENTIALS_KEY", Fernet.generate_key().decode())
    database = tmp_path / "db.sqlite3"
    connection = open_database(database)
    with connection:
        connection.execute("INSERT INTO app_users VALUES ('u1','admin',1)")
        connection.execute("INSERT INTO app_users VALUES ('u2','member',1)")
    connection.close()
    return database


def run_with(path: Path, *ids: str) -> str:
    connection = open_database(path)
    run_id = start_collection_run(connection)
    save_listings(connection, run_id, [StoredListing(id=pid) for pid in ids])
    connection.close()
    return run_id


def alerts_for(
    path: Path,
    postings: Callable[[], list[PostingResponse]],
    transport: httpx.MockTransport,
    messages: list[str] | None = None,
) -> DiscordAlerts:
    log = messages if messages is not None else []
    return DiscordAlerts(
        path,
        postings,
        base_url=BASE,
        transport=transport,
        sleep=lambda _: None,
        spawn=lambda work: work(),
        log=lambda message, level: log.append(f"{level}: {message}"),
    )


def test_dispatch_sends_one_message_per_enabled_webhook_for_new_postings(
    path: Path,
) -> None:
    run_with(path, "1", "2")
    run_id = run_with(path, "2", "3", "4")
    store = WebhookStore(path)
    urls = [webhook_url("11"), webhook_url("22"), webhook_url("33")]
    first = store.add("u1", "One", urls[0])
    second = store.add("u2", "Two", urls[1])
    disabled = store.add("u2", "Off", urls[2])
    store.update("u2", disabled.id, enabled=False)
    snapshot = [hourly(str(20 + int(pid)), posting_id=pid) for pid in "1234"]
    transport, requests = transport_of(httpx.Response(204))
    messages: list[str] = []
    alerts = alerts_for(path, lambda: snapshot, transport, messages)
    assert alerts.dispatch(run_id) == 2
    assert sorted(str(request.url) for request in requests) == sorted(urls[:2])
    body = json.loads(requests[0].content)
    assert body == build_alert(snapshot[2:], 4, base_url=BASE)
    assert [item.last_status for item in store.for_user("u1")] == ["ok"]
    assert [item.last_status for item in store.for_user("u2")] == ["ok", None]
    output = "\n".join(messages)
    for url in urls:
        assert url not in output
        assert url.rsplit("/", 1)[1] not in output
    assert first.hint in output and second.hint in output


def test_dispatch_sends_nothing_without_new_postings_or_on_a_first_run(
    path: Path,
) -> None:
    initial = run_with(path, "1", "2")
    repeat = run_with(path, "1", "2")
    WebhookStore(path).add("u1", "One", webhook_url())
    transport, requests = transport_of(httpx.Response(204))
    alerts = alerts_for(path, lambda: [posting("1"), posting("2")], transport)
    assert alerts.dispatch(repeat) == 0
    assert alerts.dispatch(initial) == 0
    assert requests == []


def test_dispatch_marks_gone_webhooks_disabled_and_survives_failures(
    path: Path,
) -> None:
    run_with(path, "1")
    run_id = run_with(path, "2")
    store = WebhookStore(path)
    store.add("u1", "One", webhook_url("11"))
    transport, _ = transport_of(httpx.Response(404))
    alerts_for(path, lambda: [posting("1"), posting("2")], transport).dispatch(run_id)
    item = store.for_user("u1")[0]
    assert (item.last_status, item.enabled) == ("gone", False)
    store.update("u1", item.id, enabled=True)
    broken, _ = transport_of(httpx.ConnectError("down"))
    alerts_for(path, lambda: [posting("1"), posting("2")], broken).dispatch(run_id)
    assert store.for_user("u1")[0].last_status == "failed"


def test_run_finished_spawns_off_the_refresh_thread_and_never_raises(
    path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_with(path, "1")
    run_id = run_with(path, "2")
    WebhookStore(path).add("u1", "One", webhook_url())
    spawned: list[Callable[[], None]] = []
    transport, requests = transport_of(httpx.Response(204))
    alerts = DiscordAlerts(
        path,
        lambda: [posting("1"), posting("2")],
        base_url=None,
        transport=transport,
        spawn=spawned.append,
    )

    def outcome(status: str) -> RunOutcome:
        return RunOutcome(
            run_id=run_id,
            status=status,  # type: ignore[arg-type]
            error_category=None,
            pacing_stop=None,
            authenticated=True,
        )

    alerts.run_finished(outcome("interrupted"))
    assert spawned == []
    alerts.run_finished(outcome("completed"))
    alerts.run_finished(outcome("failed"))
    assert len(spawned) == 2 and requests == []
    spawned[0]()
    assert len(requests) == 1
    failing = DiscordAlerts(
        path,
        lambda: (_ for _ in ()).throw(RuntimeError("snapshot")),
        base_url=None,
        transport=transport,
        spawn=lambda work: work(),
    )
    failing.run_finished(outcome("completed"))  # must not raise
    monkeypatch.delenv("WW_CREDENTIALS_KEY")
    alerts_for(path, lambda: [posting("1"), posting("2")], transport).run_finished(
        outcome("completed")
    )
    assert len(requests) == 1


def test_send_test_posts_the_fixed_test_message_to_the_callers_webhook(
    path: Path,
) -> None:
    store = WebhookStore(path)
    url = webhook_url()
    added = store.add("u1", "One", url)
    transport, requests = transport_of(httpx.Response(204))
    alerts = alerts_for(path, list, transport)
    result = alerts.send_test("u1", added.id)
    assert result.last_status == "ok"
    assert str(requests[0].url) == url
    assert json.loads(requests[0].content) == build_test_message(BASE)
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT count(*) FROM discord_webhooks").fetchone() == (
        1,
    )
    connection.close()
