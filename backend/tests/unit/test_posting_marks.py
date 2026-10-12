"""Per-user posting marks (state + viewed) against structural ID-only rows.

The posting rows carry only an ID: no title, organization, content or any
other platform value is invented.
"""

from pathlib import Path

import pytest

from waterlooworks_flash_next.database import (
    StoredListing,
    open_database,
    save_listings,
    start_collection_run,
)
from waterlooworks_flash_next.posting_marks import MarkStore, UnknownPosting


def _prepare(path: Path, posting_ids: tuple[str, ...]) -> None:
    connection = open_database(path)
    with connection:
        connection.execute("INSERT INTO app_users VALUES ('owner','admin',1)")
        connection.execute("INSERT INTO app_users VALUES ('member','member',2)")
    run_id = start_collection_run(connection)
    save_listings(connection, run_id, [StoredListing(id=pid) for pid in posting_ids])
    connection.close()


def test_state_and_viewed_are_independent_and_empty_rows_vanish(
    tmp_path: Path,
) -> None:
    path = tmp_path / "db.sqlite3"
    _prepare(path, ("1", "2"))
    store = MarkStore(path)
    assert store.read_all("owner") == {}
    mark = store.set_state("owner", "1", "interested", now="2026-10-12T01:00:00Z")
    assert (mark.posting_id, mark.state, mark.viewed_at) == ("1", "interested", None)
    assert mark.updated_at == "2026-10-12T01:00:00+00:00"
    mark = store.set_viewed("owner", "1", True, now="2026-10-12T01:00:05Z")
    assert mark.state == "interested"
    assert mark.viewed_at == "2026-10-12T01:00:05+00:00"
    # Re-marking viewed keeps the first view time.
    again = store.set_viewed("owner", "1", True, now="2026-10-12T02:00:00Z")
    assert again.viewed_at == "2026-10-12T01:00:05+00:00"
    assert store.set_state("owner", "1", "applied").state == "applied"
    assert store.set_state("owner", "1", "applied").viewed_at is not None
    cleared = store.set_state("owner", "1", None)
    assert cleared.state is None and cleared.viewed_at is not None
    gone = store.set_viewed("owner", "1", False)
    assert gone.state is None and gone.viewed_at is None
    assert store.read_all("owner") == {}
    connection = open_database(path)
    assert connection.execute("SELECT count(*) FROM posting_marks").fetchone()[0] == 0
    connection.close()
    # Clearing an absent mark is a harmless no-op that reports the empty mark.
    assert store.set_viewed("owner", "2", False).viewed_at is None


def test_marks_are_per_user_and_unknown_postings_are_refused(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    _prepare(path, ("1", "2"))
    store = MarkStore(path)
    store.set_state("owner", "1", "not_interested")
    store.set_viewed("member", "2", True)
    assert set(store.read_all("owner")) == {"1"}
    assert set(store.read_all("member")) == {"2"}
    assert store.read_all("owner")["1"].state == "not_interested"
    assert store.read_all("member")["2"].state is None
    for state in ("applied", None):
        with pytest.raises(UnknownPosting):
            store.set_state("owner", "3", state)
    for viewed in (True, False):
        with pytest.raises(UnknownPosting):
            store.set_viewed("owner", "3", viewed)
    with pytest.raises(ValueError):
        store.set_state("owner", "1", "maybe")  # type: ignore[arg-type]
    assert set(store.read_all("owner")) == {"1"}
