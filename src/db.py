"""SQLite 读写层——SQL 不是本项目的学习重点，这层直接给你用。

表结构（M0 升级后）:
  champions(id, name, title, tags, blurb, version, spells)
      spells 是 JSON 字符串，形如 [{"key":"P","name","description"}, {"key":"Q",...}, ...]
  items(id, name, gold, plaintext, tags, version, description)
      description 是去掉 HTML 标签/实体后的纯文本效果说明
  meta(key, value)   # 存当前数据版本号

为什么新列加在最后？因为旧版 data/lol.db 已经存在，`ALTER TABLE ADD COLUMN`
只能往最后追加；这样"新建库"和"旧库迁移"得到的列顺序完全一致，
下面的 INSERT 用显式列名，不会因为顺序变化而错位。
"""
from __future__ import annotations

import json
import sqlite3

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS champions(
  id      TEXT PRIMARY KEY,
  name    TEXT,
  title   TEXT,
  tags    TEXT,
  blurb   TEXT,
  version TEXT,
  spells  TEXT
);
CREATE TABLE IF NOT EXISTS items(
  id          INTEGER PRIMARY KEY,
  name        TEXT,
  gold        INTEGER,
  plaintext   TEXT,
  tags        TEXT,
  version     TEXT,
  description TEXT
);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""

# 旧库没有的列：{表名: [(列名, 列类型), ...]}
MIGRATIONS = {
    "champions": [("spells", "TEXT")],
    "items": [("description", "TEXT")],
}


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_columns(c: sqlite3.Connection) -> None:
    """给旧库补列：表已存在但缺 spells/description 时自动 ALTER TABLE。

    这样 fetch_data.py 不用先删 data/lol.db，也能原地升级到新版本数据。
    """
    for table, columns in MIGRATIONS.items():
        existing = {row[1] for row in c.execute(f"PRAGMA table_info({table})")}
        for column, column_type in columns:
            if column not in existing:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")


def init_db() -> None:
    with _conn() as c:
        c.executescript(SCHEMA)
        _ensure_columns(c)


def save_champions(version: str, champions: list[dict]) -> None:
    """全量写入英雄：先清空再插入，避免旧版本残留的行混进新库。"""
    with _conn() as c:
        c.execute("DELETE FROM champions")
        c.executemany(
            "INSERT OR REPLACE INTO champions"
            "(id, name, title, tags, blurb, version, spells) VALUES(?,?,?,?,?,?,?)",
            [
                (
                    x["id"],
                    x["name"],
                    x.get("title", ""),
                    json.dumps(x.get("tags", []), ensure_ascii=False),
                    x.get("blurb", ""),
                    version,
                    json.dumps(x.get("spells", []), ensure_ascii=False),
                )
                for x in champions
            ],
        )
        c.execute("INSERT OR REPLACE INTO meta VALUES('version',?)", (version,))


def save_items(version: str, items: list[dict]) -> None:
    """全量写入装备：先清空再插入。

    这一步是必要的：旧 data/lol.db 里可能存着上一版没过滤的 863 件装备，
    只 INSERT OR REPLACE 的话，新数据里不存在的行会一直留着变成脏数据。
    """
    with _conn() as c:
        c.execute("DELETE FROM items")
        c.executemany(
            "INSERT OR REPLACE INTO items"
            "(id, name, gold, plaintext, tags, version, description) "
            "VALUES(?,?,?,?,?,?,?)",
            [
                (
                    x["id"],
                    x["name"],
                    x.get("gold", 0),
                    x.get("plaintext", ""),
                    json.dumps(x.get("tags", []), ensure_ascii=False),
                    version,
                    x.get("description", ""),
                )
                for x in items
            ],
        )


def query_champions() -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT id,name,title,tags,blurb,spells FROM champions"
        ).fetchall()
    return [
        dict(
            id=r[0],
            name=r[1],
            title=r[2],
            tags=json.loads(r[3] or "[]"),
            blurb=r[4] or "",
            spells=json.loads(r[5] or "[]"),
        )
        for r in rows
    ]


def query_items() -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT id,name,gold,plaintext,tags,description FROM items"
        ).fetchall()
    return [
        dict(
            id=r[0],
            name=r[1],
            gold=r[2],
            plaintext=r[3] or "",
            tags=json.loads(r[4] or "[]"),
            description=r[5] or "",
        )
        for r in rows
    ]


def get_meta_version() -> str | None:
    with _conn() as c:
        row = c.execute("SELECT value FROM meta WHERE key='version'").fetchone()
    return row[0] if row else None
