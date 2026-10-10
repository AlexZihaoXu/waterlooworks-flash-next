"""Schema16: Pi AI settings, encrypted provider credentials and relaxed job CHECKs.

Existing inference jobs, cache rows and results stay readable: the rebuilt
`inference_jobs` table keeps every historical (model, endpoint) pair and is
verified row-for-row before the original is replaced.
"""

import re
import sqlite3

PI_MODEL_PREFIX = "pi:"


def _rebuild_inference_jobs(connection: sqlite3.Connection) -> None:
    objects = [
        tuple(row)
        for row in connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE sql IS NOT NULL ORDER BY type,name"
        )
    ]
    for _kind, name, _, sql in objects:
        if re.search(r"\binference_jobs\b", sql, re.I) and name not in (
            "inference_jobs",
            "inference_job_items",
            "idx_inference_jobs_status",
        ):
            raise ValueError("Unsupported inference migration dependency.")
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='inference_jobs'"
    ).fetchone()
    if row is None:
        raise ValueError("Inference schema is missing.")
    sql = row[0]
    legacy_pairs = (
        "(model_id='qwen3.8-27b@q4_k_m' AND endpoint='http://127.0.0.1:1234') OR\n"
        "            (model_id='gpt-5.6-luna' AND "
        "endpoint='https://api.openai.com/v1/responses'))"
    )
    # A table renamed by the schema14 rebuild is stored with a quoted name.
    sql, renamed = re.subn(
        r'^CREATE TABLE "?inference_jobs"? \(',
        "CREATE TABLE inference_jobs_v16 (",
        sql,
        count=1,
    )
    if renamed != 1:
        raise ValueError("Unsupported inference schema definition.")
    replacements = {
        legacy_pairs: (
            "(model_id='qwen3.8-27b@q4_k_m' AND endpoint='http://127.0.0.1:1234') OR\n"
            "            (model_id='gpt-5.6-luna' AND "
            "endpoint='https://api.openai.com/v1/responses') OR\n"
            "            (substr(model_id,1,3)='pi:' AND length(model_id)<=300 "
            "AND length(endpoint) BETWEEN 8 AND 2048 "
            "AND (substr(endpoint,1,7)='http://' OR substr(endpoint,1,8)='https://')))"
        ),
        "CHECK(max_tokens BETWEEN 1 AND 4096)": "CHECK(max_tokens BETWEEN 1 AND 65536)",
        "CHECK(concurrency BETWEEN 1 AND 4)": "CHECK(concurrency BETWEEN 1 AND 8)",
    }
    for old, new in replacements.items():
        if sql.count(old) != 1:
            raise ValueError("Unsupported inference schema definition.")
        sql = sql.replace(old, new)
    connection.execute(sql)
    connection.execute("INSERT INTO inference_jobs_v16 SELECT * FROM inference_jobs")
    if (
        connection.execute(
            "SELECT * FROM inference_jobs EXCEPT SELECT * FROM inference_jobs_v16"
        ).fetchone()
        or connection.execute(
            "SELECT * FROM inference_jobs_v16 EXCEPT SELECT * FROM inference_jobs"
        ).fetchone()
        or connection.execute("SELECT count(*) FROM inference_jobs").fetchone()
        != connection.execute("SELECT count(*) FROM inference_jobs_v16").fetchone()
    ):
        raise ValueError("Inference migration row mismatch.")
    connection.execute("DROP TABLE inference_jobs")
    connection.execute("ALTER TABLE inference_jobs_v16 RENAME TO inference_jobs")
    index = next(
        sql for _, name, _, sql in objects if name == "idx_inference_jobs_status"
    )
    connection.execute(index)
    remaining = [
        tuple(row)
        for row in connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE sql IS NOT NULL AND name<>'inference_jobs' "
            "ORDER BY type,name"
        )
    ]
    if remaining != [row for row in objects if row[1] != "inference_jobs"]:
        raise ValueError("Inference migration changed dependent schema.")


def migrate_ai_settings(connection: sqlite3.Connection) -> None:
    """Caller disables FK enforcement BEFORE its transaction; verify before commit."""
    if (
        not connection.in_transaction
        or connection.execute("PRAGMA foreign_keys").fetchone()[0]
    ):
        raise ValueError("AI migration requires an offline migration transaction.")
    _rebuild_inference_jobs(connection)
    connection.execute("""
        CREATE TABLE ai_settings (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            device_id TEXT NOT NULL CHECK(length(device_id)=36),
            draft_json TEXT NOT NULL CHECK(json_valid(draft_json)
                AND length(draft_json)<=16384),
            active_json TEXT CHECK(active_json IS NULL OR (json_valid(active_json)
                AND length(active_json)<=16384)),
            activated_at TEXT,
            updated_at TEXT NOT NULL,
            CHECK((active_json IS NULL)=(activated_at IS NULL))
        ) STRICT
    """)
    connection.execute("""
        CREATE TABLE ai_credentials (
            slot TEXT PRIMARY KEY NOT NULL
                CHECK(slot IN ('chatgpt_signin','codex_device','custom_token')),
            ciphertext BLOB NOT NULL CHECK(length(ciphertext) BETWEEN 100 AND 65536),
            metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)
                AND length(metadata_json)<=1024),
            updated_at TEXT NOT NULL
        ) STRICT
    """)
    if (
        connection.execute("PRAGMA foreign_key_check").fetchone()
        or connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
    ):
        raise ValueError("AI migration integrity check failed.")
