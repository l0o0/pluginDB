from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import ARRAY
from sqlalchemy import Boolean
from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import Engine
from sqlalchemy import Integer
from sqlalchemy import JSON
from sqlalchemy import MetaData
from sqlalchemy import String
from sqlalchemy import Table
from sqlalchemy import Text
from sqlalchemy import create_engine as sa_create_engine
from sqlalchemy import delete
from sqlalchemy import insert
from sqlalchemy import inspect
from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy import update
from sqlalchemy.pool import NullPool


metadata = MetaData()

PLUGINS_TABLE_NAME = os.getenv("PLUGINDB_PLUGINS_TABLE", "plugins")
PLUGIN_RELEASES_TABLE_NAME = os.getenv("PLUGINDB_PLUGIN_RELEASES_TABLE", "plugin_releases")
PLUGIN_LOCALES_TABLE_NAME = os.getenv("PLUGINDB_PLUGIN_LOCALES_TABLE", "plugin_locales")

plugins_table = Table(
    PLUGINS_TABLE_NAME,
    metadata,
    Column("id", String, primary_key=True),
    Column("plugin_name", Text, nullable=False),
    Column("sanitized_name", Text, nullable=False),
    Column("source_repo", Text, nullable=False),
    Column("source_url", Text, nullable=False),
    Column("homepage_url", Text),
    Column("author", Text),
    Column("update_url", Text),
    Column("tags", JSON().with_variant(ARRAY(Text), "postgresql"), nullable=False, default=list),
    Column("github_stars", Integer, nullable=False, default=0),
    Column("download_count", Integer, nullable=False, default=0),
    Column("synced_at", DateTime(timezone=True), nullable=False),
)

plugin_releases_table = Table(
    PLUGIN_RELEASES_TABLE_NAME,
    metadata,
    Column("plugin_id", String, primary_key=True),
    Column("release_key", String, primary_key=True),
    Column("tag", Text, nullable=False),
    Column("prerelease", Boolean, nullable=False),
    Column("published_at", DateTime(timezone=True)),
    Column("asset_name", Text, nullable=False),
    Column("asset_url", Text, nullable=False),
    Column("xpi_path", Text, nullable=False),
    Column("md5", String, nullable=False),
    Column("download_count", Integer, nullable=False, default=0),
    Column("manifest_version", Text, nullable=False),
    Column("manifest_min_zotero_version", Text),
    Column("manifest_max_zotero_version", Text),
    Column("manifest_json", JSON, nullable=False),
    Column("synced_at", DateTime(timezone=True), nullable=False),
)

plugin_locales_table = Table(
    PLUGIN_LOCALES_TABLE_NAME,
    metadata,
    Column("plugin_id", String, primary_key=True),
    Column("locale", String, primary_key=True),
    Column("field", String, primary_key=True),
    Column("source", String, primary_key=True),
    Column("value", Text, nullable=False),
    Column("synced_at", DateTime(timezone=True), nullable=False),
)


def create_engine(database_url: str) -> Engine:
    if database_url.startswith("sqlite"):
        return sa_create_engine(database_url, future=True, poolclass=NullPool)
    return sa_create_engine(database_url, future=True)


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _add_column_if_missing(engine: Engine, table_name: str, column_name: str, column_sql: str) -> None:
    with engine.begin() as connection:
        columns = {column["name"] for column in inspect(connection).get_columns(table_name)}
        if column_name in columns:
            return
        connection.execute(
            text(f"ALTER TABLE {_quote_identifier(table_name)} ADD COLUMN {_quote_identifier(column_name)} {column_sql}")
        )


def ensure_schema(engine: Engine) -> None:
    metadata.create_all(engine)
    _add_column_if_missing(engine, PLUGINS_TABLE_NAME, "tags", "TEXT NOT NULL DEFAULT '[]'")
    _add_column_if_missing(engine, PLUGINS_TABLE_NAME, "github_stars", "INTEGER NOT NULL DEFAULT 0")
    _add_column_if_missing(engine, PLUGINS_TABLE_NAME, "download_count", "INTEGER NOT NULL DEFAULT 0")
    _add_column_if_missing(engine, PLUGIN_RELEASES_TABLE_NAME, "download_count", "INTEGER NOT NULL DEFAULT 0")


def fetch_one(engine: Engine, sql: str) -> tuple[Any, ...] | None:
    with engine.connect() as connection:
        row = connection.execute(text(sql)).fetchone()
    return tuple(row) if row is not None else None


def fetch_all(engine: Engine, sql: str) -> list[tuple[Any, ...]]:
    with engine.connect() as connection:
        rows = connection.execute(text(sql)).fetchall()
    return [tuple(row) for row in rows]


def find_cached_release(engine: Engine, source_repo: str, release_key: str) -> dict[str, Any] | None:
    statement = (
        select(
            plugin_releases_table.c.tag,
            plugin_releases_table.c.asset_url,
            plugin_releases_table.c.xpi_path,
            plugin_releases_table.c.md5,
            plugin_releases_table.c.manifest_version,
        )
        .select_from(
            plugins_table.join(
                plugin_releases_table,
                plugins_table.c.id == plugin_releases_table.c.plugin_id,
            )
        )
        .where(
            plugins_table.c.source_repo == source_repo,
            plugin_releases_table.c.release_key == release_key,
        )
        .limit(1)
    )
    with engine.connect() as connection:
        row = connection.execute(statement).mappings().first()
    return dict(row) if row is not None else None


def _plugin_values(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record["id"],
        "plugin_name": record["plugin_name"],
        "sanitized_name": record["sanitized_name"],
        "source_repo": record["source_repo"],
        "source_url": record["source_url"],
        "homepage_url": record.get("homepage_url"),
        "author": record.get("author"),
        "update_url": record.get("update_url"),
        "tags": record.get("tags") or [],
        "github_stars": int(record.get("github_stars") or 0),
        "download_count": int(record.get("download_count") or 0),
        "synced_at": _parse_datetime(record["synced_at"]),
    }


def _upsert_row(connection: Any, table: Table, key_values: dict[str, Any], payload: dict[str, Any]) -> None:
    where_clause = [table.c[key] == value for key, value in key_values.items()]
    exists = connection.execute(table.select().where(*where_clause).limit(1)).fetchone()
    if exists is None:
        connection.execute(insert(table).values(payload))
    else:
        connection.execute(update(table).where(*where_clause).values(payload))


def upsert_plugin_record(engine: Engine, record: dict[str, Any]) -> None:
    with engine.begin() as connection:
        plugin_values = _plugin_values(record)
        _upsert_row(
            connection,
            plugins_table,
            {"id": record["id"]},
            plugin_values,
        )

        connection.execute(
            delete(plugin_releases_table).where(plugin_releases_table.c.plugin_id == record["id"])
        )
        for release_key, release in dict(record["releases"]).items():
            if not release:
                continue
            connection.execute(
                insert(plugin_releases_table).values(
                    plugin_id=record["id"],
                    release_key=release_key,
                    tag=release["tag"],
                    prerelease=bool(release.get("prerelease")),
                    published_at=_parse_datetime(release.get("published_at")),
                    asset_name=release["asset_name"],
                    asset_url=release["asset_url"],
                    xpi_path=release["xpi_path"],
                    md5=release["md5"],
                    download_count=int(release.get("download_count") or 0),
                    manifest_version=release["manifest_version"],
                    manifest_min_zotero_version=release.get("manifest_min_zotero_version"),
                    manifest_max_zotero_version=release.get("manifest_max_zotero_version"),
                    manifest_json=release.get("manifest_json") or {},
                    synced_at=_parse_datetime(record["synced_at"]),
                )
            )

        connection.execute(
            delete(plugin_locales_table).where(plugin_locales_table.c.plugin_id == record["id"])
        )
        for locale_entry in record.get("locales", []):
            connection.execute(
                insert(plugin_locales_table).values(
                    plugin_id=record["id"],
                    locale=locale_entry["locale"],
                    field=locale_entry["field"],
                    source=locale_entry["source"],
                    value=locale_entry["value"],
                    synced_at=_parse_datetime(record["synced_at"]),
                )
            )
