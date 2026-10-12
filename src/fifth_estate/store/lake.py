"""Append-only Parquet data lake, partitioned by Taipei date and queried with DuckDB.

Layout: {data_dir}/{table}/date=YYYY-MM-DD/{run_id}.parquet

Rows are never rewritten. Tables whose rows can be superseded (annotations, flags)
are read with "latest row per key wins" via `latest()`.
"""

from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from ..config import TAIPEI, data_dir
from .schemas import SCHEMAS


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def taipei_today() -> date:
    return now_utc().astimezone(TAIPEI).date()


def new_run_id() -> str:
    return now_utc().strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)


def table_dir(table: str) -> Path:
    return data_dir() / table


def write(table: str, rows: Iterable[dict[str, Any]], run_id: str, day: date | None = None) -> Path | None:
    """Write rows as one new Parquet file. Unknown keys are dropped, missing keys become null."""
    rows = list(rows)
    if not rows:
        return None
    schema = SCHEMAS[table]
    cols = {name: [r.get(name) for r in rows] for name in schema.names}
    tbl = pa.Table.from_pydict(cols, schema=schema)
    d = day or taipei_today()
    out = table_dir(table) / f"date={d.isoformat()}" / f"{run_id}-{secrets.token_hex(2)}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".parquet.tmp")
    pq.write_table(tbl, tmp, compression="zstd")
    tmp.rename(out)
    return out


def has_data(table: str) -> bool:
    return any(table_dir(table).glob("date=*/*.parquet"))


def source(table: str, days: int | None = None) -> str:
    """SQL table expression for `table`, optionally limited to the last `days` partitions."""
    glob = (table_dir(table) / "date=*" / "*.parquet").as_posix()
    if not has_data(table):
        # Empty relation with the right columns.
        cols = ", ".join(f"CAST(NULL AS {_duck_type(f.type)}) AS {f.name}" for f in SCHEMAS[table])
        return f"(SELECT {cols}, CAST(NULL AS DATE) AS date WHERE FALSE)"
    expr = f"read_parquet('{glob}', hive_partitioning = true, union_by_name = true)"
    if days is not None:
        since = (taipei_today() - timedelta(days=days)).isoformat()
        expr = f"(SELECT * FROM {expr} WHERE date >= DATE '{since}')"
    return expr


def latest(table: str, key: str = "article_id", days: int | None = None, where: str | None = None) -> str:
    """SQL expression keeping only the newest row (by created_at) per key."""
    cond = f"WHERE {where}" if where else ""
    return (
        f"(SELECT * FROM {source(table, days)} {cond} "
        f"QUALIFY row_number() OVER (PARTITION BY {key} ORDER BY created_at DESC) = 1)"
    )


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    return con


def rows(sql: str, params: list | None = None, con: duckdb.DuckDBPyConnection | None = None) -> list[dict]:
    c = con or connect()
    res = c.execute(sql, params or [])
    names = [d[0] for d in res.description]
    return [dict(zip(names, r)) for r in res.fetchall()]


def arrow(sql: str, params: list | None = None, con: duckdb.DuckDBPyConnection | None = None) -> pa.Table:
    c = con or connect()
    return c.execute(sql, params or []).to_arrow_table()


def _duck_type(t: pa.DataType) -> str:
    if pa.types.is_string(t) or pa.types.is_large_string(t):
        return "VARCHAR"
    if pa.types.is_int32(t):
        return "INTEGER"
    if pa.types.is_int64(t):
        return "BIGINT"
    if pa.types.is_float32(t):
        return "FLOAT"
    if pa.types.is_float64(t):
        return "DOUBLE"
    if pa.types.is_boolean(t):
        return "BOOLEAN"
    if pa.types.is_timestamp(t):
        return "TIMESTAMPTZ"
    if pa.types.is_date(t):
        return "DATE"
    if pa.types.is_binary(t):
        return "BLOB"
    if pa.types.is_list(t):
        return _duck_type(t.value_type) + "[]"
    raise TypeError(f"unsupported type {t}")
