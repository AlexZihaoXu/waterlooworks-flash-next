"""SQLite v6 compact listing/content evidence; revision events keep their IDs."""

import sqlite3
from collections import OrderedDict
from typing import Literal

from waterlooworks_flash_next.compact_codec import (
    MAX_BYTES,
    HistoryError,
    apply_delta,
    compress,
    decompress,
    make_delta,
    verify_snapshot,
)

HistoryKind = Literal["listing", "content"]
MAX_CHAIN_DEPTH = 32
CACHE_BYTES = 16 * 1024 * 1024
MAX_DELTA_SNAPSHOT_BYTES = 1024 * 1024
MAX_PATCH_BYTES = 4 * MAX_DELTA_SNAPSHOT_BYTES


class HistoryReader:
    """Verified reconstruction with bounded chain and per-reader LRU byte budget."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.cache: OrderedDict[int, tuple[str, str, str, int]] = OrderedDict()
        self.cache_bytes = 0

    def _remember(self, payload_id: int, item: tuple[str, str, str, int]) -> None:
        size = len(item[2].encode("utf-8"))
        if size > CACHE_BYTES:
            return
        while self.cache and self.cache_bytes + size > CACHE_BYTES:
            _, removed = self.cache.popitem(last=False)
            self.cache_bytes -= len(removed[2].encode("utf-8"))
        self.cache[payload_id] = item
        self.cache_bytes += size

    def read(self, payload_id: int, expected_hash: str, kind: HistoryKind) -> str:
        chain: list[sqlite3.Row] = []
        cursor_id = payload_id
        encoded_bytes = 0
        seen: set[int] = set()
        result: tuple[str, str, str, int] | None = None
        while True:
            if cursor_id in seen or len(chain) > MAX_CHAIN_DEPTH:
                raise HistoryError("History dependency limit exceeded.")
            seen.add(cursor_id)
            if cursor_id in self.cache:
                result = self.cache[cursor_id]
                self.cache.move_to_end(cursor_id)
                break
            row = self.connection.execute(
                "SELECT payload_id,kind,content_hash,base_id,depth,"
                "encoding,raw_size,data "
                "FROM history_payloads WHERE payload_id=?",
                (cursor_id,),
            ).fetchone()
            if row is None or row[1] != kind or not 0 <= row[4] <= MAX_CHAIN_DEPTH:
                raise HistoryError("History payload identity missing or invalid.")
            encoded_bytes += len(row[7])
            if encoded_bytes > MAX_BYTES:
                raise HistoryError("History chain encoded size limit exceeded.")
            if row[3] is not None and row[6] > MAX_PATCH_BYTES:
                raise HistoryError("History patch size limit exceeded.")
            chain.append(row)
            if row[3] is None:
                break
            if row[3] >= row[0]:
                raise HistoryError("Invalid history dependency order.")
            cursor_id = row[3]
        for row in reversed(chain):
            decoded = decompress(row[7], row[6])
            if row[3] is None:
                if row[4] != 0 or row[5] != "zlib-json-v1":
                    raise HistoryError("Invalid history checkpoint.")
                text = verify_snapshot(decoded, row[2])
            else:
                if (
                    result is None
                    or result[0] != kind
                    or row[4] != result[3] + 1
                    or row[5] != "zlib-delta-v1"
                ):
                    raise HistoryError("Invalid history delta dependency.")
                if len(result[2].encode("utf-8")) > MAX_DELTA_SNAPSHOT_BYTES:
                    raise HistoryError("History delta base size limit exceeded.")
                text = verify_snapshot(apply_delta(result[2], decoded), row[2])
                if len(text.encode("utf-8")) > MAX_DELTA_SNAPSHOT_BYTES:
                    raise HistoryError("History delta target size limit exceeded.")
            result = (row[1], row[2], text, row[4])
            self._remember(row[0], result)
        if result is None or result[0] != kind or result[1] != expected_hash:
            raise HistoryError("History revision hash or kind mismatch.")
        return result[2]


def store_payload(
    connection: sqlite3.Connection,
    kind: HistoryKind,
    text: str,
    content_hash: str,
    base_id: int | None = None,
) -> int:
    """Caller owns transaction. Deduplicate states, not immutable revision events."""
    verify_snapshot(text, content_hash)
    reader = HistoryReader(connection)
    existing = connection.execute(
        "SELECT payload_id FROM history_payloads WHERE kind=? AND content_hash=?",
        (kind, content_hash),
    ).fetchone()
    if existing is not None:
        if reader.read(existing[0], content_hash, kind) != text:
            raise HistoryError("History payload identity conflict.")
        return int(existing[0])
    data = compress(text)
    raw_size = len(text.encode("utf-8"))
    encoding = "zlib-json-v1"
    depth = 0
    selected_base = None
    if base_id is not None:
        base = connection.execute(
            "SELECT content_hash,depth FROM history_payloads WHERE payload_id=?",
            (base_id,),
        ).fetchone()
        if base is None:
            raise HistoryError("History base missing.")
        before = reader.read(base_id, base[0], kind)
        if (
            base[1] < MAX_CHAIN_DEPTH
            and raw_size <= MAX_DELTA_SNAPSHOT_BYTES
            and len(before.encode("utf-8")) <= MAX_DELTA_SNAPSHOT_BYTES
        ):
            try:
                patch = make_delta(before, text)
                packed = compress(patch)
            except HistoryError:
                # Valid snapshots can have a larger/deeper patch than codec limits.
                packed = data
                patch = ""
            if (
                len(packed) < len(data)
                and len(patch.encode("utf-8")) <= MAX_PATCH_BYTES
            ):
                # Test reversibility before the patch becomes durable evidence.
                if (
                    apply_delta(before, patch) != text
                    or apply_delta(text, patch, reverse=True) != before
                ):
                    raise HistoryError("History delta failed verification.")
                data, raw_size = packed, len(patch.encode("utf-8"))
                encoding, depth, selected_base = "zlib-delta-v1", base[1] + 1, base_id
    result = connection.execute(
        "INSERT INTO history_payloads "
        "(kind,content_hash,base_id,depth,encoding,raw_size,data) "
        "VALUES(?,?,?,?,?,?,?)",
        (kind, content_hash, selected_base, depth, encoding, raw_size, data),
    )
    assert result.lastrowid is not None
    return result.lastrowid


def revision_snapshot(
    connection: sqlite3.Connection, kind: HistoryKind, revision_id: int
) -> str:
    """Internal SQL JSON columns were removed in v6; use verified reconstruction."""
    table = "posting_revisions" if kind == "listing" else "posting_content_revisions"
    row = connection.execute(
        f"SELECT payload_id,content_hash FROM {table} WHERE revision_id=?",
        (revision_id,),
    ).fetchone()
    if row is None:
        raise HistoryError("History revision missing.")
    return HistoryReader(connection).read(row[0], row[1], kind)


def migrate_compact_history(connection: sqlite3.Connection) -> None:
    """Transactional ALTERs preserve event IDs, incoming FKs, indexes and ordering."""
    connection.execute(f"""
        CREATE TABLE history_payloads (
            payload_id INTEGER PRIMARY KEY,
            kind TEXT NOT NULL CHECK(kind IN ('listing','content')),
            content_hash TEXT NOT NULL CHECK(length(content_hash)=64
                AND content_hash NOT GLOB '*[^0-9a-f]*'),
            base_id INTEGER REFERENCES history_payloads(payload_id) ON DELETE RESTRICT,
            depth INTEGER NOT NULL CHECK(depth BETWEEN 0 AND {MAX_CHAIN_DEPTH}),
            encoding TEXT NOT NULL CHECK(encoding IN ('zlib-json-v1','zlib-delta-v1')),
            raw_size INTEGER NOT NULL CHECK(raw_size BETWEEN 0 AND {MAX_BYTES}),
            data BLOB NOT NULL CHECK(length(data) BETWEEN 1 AND {MAX_BYTES}),
            UNIQUE(kind,content_hash),
            CHECK((base_id IS NULL AND depth=0 AND encoding='zlib-json-v1')
                OR (base_id IS NOT NULL AND base_id<payload_id
                    AND depth>0 AND encoding='zlib-delta-v1'))
        ) STRICT
    """)
    for operation in ("UPDATE", "DELETE"):
        connection.execute(f"""
            CREATE TRIGGER immutable_payload_{operation.lower()}
            BEFORE {operation} ON history_payloads
            BEGIN SELECT RAISE(ABORT,'History payloads are append-only'); END
        """)
    for kind in ("listing", "content"):
        _migrate_revisions(connection, kind)


def _migrate_revisions(connection: sqlite3.Connection, kind: HistoryKind) -> None:
    listing = kind == "listing"
    table = "posting_revisions" if listing else "posting_content_revisions"
    current = "postings" if listing else "posting_contents"
    column = "listing_json" if listing else "content_json"
    prefix = "revision" if listing else "content_revision"
    connection.execute(f"DROP TRIGGER {prefix}_matches_current")
    connection.execute(f"DROP TRIGGER immutable_{prefix}_update")
    connection.execute(
        f"ALTER TABLE {table} ADD COLUMN payload_id INTEGER "
        "REFERENCES history_payloads(payload_id) ON DELETE RESTRICT"
    )
    last_revision: int | None = None
    previous: dict[str, int] = {}
    while rows := connection.execute(
        f"SELECT revision_id,posting_id,{column},content_hash FROM {table} "
        "WHERE (? IS NULL OR revision_id>?) ORDER BY revision_id LIMIT 100",
        (last_revision, last_revision),
    ).fetchall():
        for row in rows:
            payload_id = store_payload(
                connection, kind, row[2], row[3], previous.get(row[1])
            )
            connection.execute(
                f"UPDATE {table} SET payload_id=? WHERE revision_id=?",
                (payload_id, row[0]),
            )
            previous[row[1]] = payload_id
            last_revision = row[0]
    connection.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    extra = "" if listing else "AND extractor_version=NEW.extractor_version"
    connection.execute(f"""
        CREATE TRIGGER {prefix}_matches_current BEFORE INSERT ON {table}
        WHEN NOT EXISTS (SELECT 1 FROM {current} WHERE posting_id=NEW.posting_id
            AND content_hash=NEW.content_hash AND last_seen_run_id=NEW.run_id
            AND last_seen_at=NEW.observed_at {extra})
            OR NOT EXISTS (SELECT 1 FROM history_payloads
                WHERE payload_id=NEW.payload_id
                AND content_hash=NEW.content_hash AND kind='{kind}')
        BEGIN SELECT RAISE(ABORT,'Revision must match compact evidence'); END
    """)
    connection.execute(f"""
        CREATE TRIGGER immutable_{prefix}_update BEFORE UPDATE ON {table}
        BEGIN SELECT RAISE(ABORT,'Revisions are append-only'); END
    """)
