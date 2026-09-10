"""Schema14: bounded usage ledger and a narrow v8 provider-pair CHECK extension."""

import re
import sqlite3

from waterlooworks_flash_next.openai_ledger import migrate_openai_ledger


def migrate_openai_provider(connection: sqlite3.Connection) -> None:
    """Caller disables FK enforcement BEFORE its transaction; verify before commit.

    Do not rename the old parent: SQLite would redirect child foreign keys to it.
    The replacement is copied, verified, then renamed into the original name.
    """
    if (
        not connection.in_transaction
        or connection.execute("PRAGMA foreign_keys").fetchone()[0]
    ):
        raise ValueError(
            "Provider migration requires an offline migration transaction."
        )
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
            raise ValueError("Unsupported provider migration dependency.")
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='inference_jobs'"
    ).fetchone()
    if row is None:
        raise ValueError("Inference schema is missing.")
    sql = row[0]
    replacements = {
        "CREATE TABLE inference_jobs": "CREATE TABLE inference_jobs_v14",
        (
            "model_id TEXT NOT NULL CHECK(model_id='qwen3.8-27b@q4_k_m')"
        ): "model_id TEXT NOT NULL",
        "endpoint TEXT NOT NULL CHECK(endpoint='http://127.0.0.1:1234')": """
            endpoint TEXT NOT NULL CHECK(
            (model_id='qwen3.8-27b@q4_k_m' AND endpoint='http://127.0.0.1:1234') OR
            (model_id='gpt-5.6-luna' AND endpoint='https://api.openai.com/v1/responses'))""",
    }
    for old, new in replacements.items():
        if sql.count(old) != 1:
            raise ValueError("Unsupported inference schema definition.")
        sql = sql.replace(old, new)
    connection.execute(sql)
    connection.execute("INSERT INTO inference_jobs_v14 SELECT * FROM inference_jobs")
    if (
        connection.execute(
            "SELECT * FROM inference_jobs EXCEPT SELECT * FROM inference_jobs_v14"
        ).fetchone()
        or connection.execute(
            "SELECT * FROM inference_jobs_v14 EXCEPT SELECT * FROM inference_jobs"
        ).fetchone()
    ):
        raise ValueError("Inference migration row mismatch.")
    connection.execute("DROP TABLE inference_jobs")
    connection.execute("ALTER TABLE inference_jobs_v14 RENAME TO inference_jobs")
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
    migrate_openai_ledger(connection)
    if (
        connection.execute("PRAGMA foreign_key_check").fetchone()
        or connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
    ):
        raise ValueError("Provider migration integrity check failed.")
