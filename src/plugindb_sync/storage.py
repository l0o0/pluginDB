from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Column
from sqlalchemy import Engine
from sqlalchemy import Integer
from sqlalchemy import MetaData
from sqlalchemy import String
from sqlalchemy import Table
from sqlalchemy import Text
from sqlalchemy import create_engine as sa_create_engine
from sqlalchemy import delete
from sqlalchemy import insert
from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy import update
from sqlalchemy.pool import NullPool


metadata = MetaData()

plugins_table = Table(
    "plugins",
    metadata,
    Column("id", String, primary_key=True),
    Column("plugin_name", Text, nullable=False),
    Column("sanitized_name", Text, nullable=False),
    Column("source_repo", Text, nullable=False),
    Column("source_url", Text, nullable=False),
    Column("homepage_url", Text),
    Column("author", Text),
    Column("update_url", Text),
    Column("synced_at", Text, nullable=False),
)

plugin_releases_table = Table(
    "plugin_releases",
    metadata,
    Column("plugin_id", String, primary_key=True),
    Column("release_key", String, primary_key=True),
    Column("tag", Text, nullable=False),
    Column("prerelease", Integer, nullable=False),
    Column("published_at", Text),
    Column("asset_name", Text, nullable=False),
    Column("asset_url", Text, nullable=False),
    Column("xpi_path", Text, nullable=False),
    Column("md5", String, nullable=False),
    Column("manifest_version", Text, nullable=False),
    Column("manifest_min_zotero_version", Text),
    Column("manifest_max_zotero_version", Text),
    Column("manifest_json", Text, nullable=False),
    Column("synced_at", Text, nullable=False),
)

plugin_locales_table = Table(
    "plugin_locales",
    metadata,
    Column("plugin_id", String, primary_key=True),
    Column("locale", String, primary_key=True),
    Column("field", String, primary_key=True),
    Column("source", String, primary_key=True),
    Column("value", Text, nullable=False),
    Column("synced_at", Text, nullable=False),
)


def create_engine(database_url: str) -> Engine:
    if database_url.startswith("sqlite"):
        return sa_create_engine(database_url, future=True, poolclass=NullPool)
    return sa_create_engine(database_url, future=True)


def ensure_schema(engine: Engine) -> None:
    metadata.create_all(engine)


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
        "synced_at": record["synced_at"],
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
                    prerelease=1 if release.get("prerelease") else 0,
                    published_at=release.get("published_at"),
                    asset_name=release["asset_name"],
                    asset_url=release["asset_url"],
                    xpi_path=release["xpi_path"],
                    md5=release["md5"],
                    manifest_version=release["manifest_version"],
                    manifest_min_zotero_version=release.get("manifest_min_zotero_version"),
                    manifest_max_zotero_version=release.get("manifest_max_zotero_version"),
                    manifest_json=release.get("manifest_json_text")
                    or json.dumps(release.get("manifest_json") or {}, ensure_ascii=False, sort_keys=True),
                    synced_at=record["synced_at"],
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
                    synced_at=record["synced_at"],
                )
            )
