"""Local, transactional keyword inbox and append-only usage history.

Independent from app_state.json: frequent settings saves cannot overwrite it.
Each operation owns a connection, including publications on worker threads.
"""
from __future__ import annotations

import csv
import io
import re
import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def clean_keyword(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value))).strip()


def keyword_key(value: str) -> str:
    return clean_keyword(value).casefold()


def read_keyword_file(path: Path) -> list[str]:
    data = Path(path).read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp949")
    if Path(path).suffix.lower() != ".csv":
        return text.splitlines()
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return []
    headers = [cell.strip().casefold() for cell in rows[0]]
    column = next((i for i, cell in enumerate(headers) if cell in {"keyword", "키워드"}), 0)
    start = 1 if any(cell in {"keyword", "키워드"} for cell in headers) else 0
    return [row[column] for row in rows[start:] if len(row) > column]


class KeywordLibrary:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS keywords (
                    id INTEGER PRIMARY KEY, key TEXT NOT NULL UNIQUE,
                    keyword TEXT NOT NULL, added_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'used', 'removed'))
                );
                CREATE TABLE IF NOT EXISTS usage (
                    id INTEGER PRIMARY KEY,
                    keyword_id INTEGER NOT NULL REFERENCES keywords(id),
                    keyword TEXT NOT NULL, used_at TEXT NOT NULL,
                    platform TEXT NOT NULL, url TEXT NOT NULL, title TEXT NOT NULL,
                    event_key TEXT NOT NULL,
                    UNIQUE(keyword_id, event_key)
                );
                CREATE INDEX IF NOT EXISTS keywords_status ON keywords(status, id);
            """)

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def add(self, values) -> dict:
        # Validate before beginning a transaction: never silently truncate data.
        values = [clean_keyword(value) for value in values if clean_keyword(value)]
        if len(values) > 100_000 or any(len(value) > 500 for value in values):
            raise ValueError("한 번에 100,000개까지, 키워드 하나당 500자까지 등록할 수 있습니다.")
        result = {"added": 0, "duplicate": 0, "used": 0}
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            for value in values:
                row = db.execute("SELECT id, status FROM keywords WHERE key=?", (keyword_key(value),)).fetchone()
                if row and row["status"] != "removed":
                    result["used" if row["status"] == "used" else "duplicate"] += 1
                    continue
                if row:
                    db.execute("UPDATE keywords SET status='pending', keyword=?, added_at=? WHERE id=?",
                               (value, self._now(), row["id"]))
                else:
                    db.execute("INSERT INTO keywords(key, keyword, added_at) VALUES (?, ?, ?)",
                               (keyword_key(value), value, self._now()))
                result["added"] += 1
        return result

    def counts(self) -> dict:
        with self._connection() as db:
            result = {"pending": 0, "used": 0, "history": 0}
            for row in db.execute("SELECT status, COUNT(*) AS count FROM keywords GROUP BY status"):
                if row["status"] in result:
                    result[row["status"]] = row["count"]
            result["history"] = db.execute("SELECT COUNT(*) FROM usage").fetchone()[0]
            return result

    def list(self, *, history=False, search="", offset=0, limit=40) -> tuple[list[dict], int]:
        # instr treats %, _ and backslash literally, unlike LIKE search.
        query = keyword_key(search)
        with self._connection() as db:
            if history:
                source = "usage u JOIN keywords k ON k.id=u.keyword_id"
                condition = "instr(k.key, ?) > 0"
                fields = "u.*, k.status, u.keyword_id AS item_id"
                order = "u.id DESC"
            else:
                source = "keywords k"
                condition = "k.status='pending' AND instr(k.key, ?) > 0"
                fields = "k.*, k.id AS item_id"
                order = "k.id"
            total = db.execute(f"SELECT COUNT(*) FROM {source} WHERE {condition}", (query,)).fetchone()[0]
            rows = db.execute(f"SELECT {fields} FROM {source} WHERE {condition} ORDER BY {order} LIMIT ? OFFSET ?",
                              (query, max(1, int(limit)), max(0, int(offset)))).fetchall()
            return [dict(row) for row in rows], total

    def record_use(self, keyword, *, platform, url="", title="", event_key) -> bool:
        """Consume a registered keyword only; retries of one event are idempotent."""
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT id, keyword FROM keywords WHERE key=? AND status!='removed'",
                             (keyword_key(keyword),)).fetchone()
            if row is None:
                return False
            inserted = db.execute(
                "INSERT OR IGNORE INTO usage(keyword_id, keyword, used_at, platform, url, title, event_key) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (row["id"], row["keyword"], self._now(), str(platform), str(url), str(title), str(event_key)),
            ).rowcount
            if inserted:
                db.execute("UPDATE keywords SET status='used' WHERE id=?", (row["id"],))
            return bool(inserted)

    def change_status(self, ids, status) -> int:
        if status not in {"pending", "removed"}:
            raise ValueError(status)
        changed = 0
        with self._connection() as db:
            for item_id in set(ids):
                changed += db.execute("UPDATE keywords SET status=? WHERE id=? AND status!=?",
                                      (status, int(item_id), status)).rowcount
        return changed

    def export(self, path: Path, *, history=False):
        rows, _ = self.list(history=history, limit=2_147_483_647)
        if Path(path).suffix.lower() == ".txt":
            Path(path).write_text("\n".join(row["keyword"] for row in rows) + ("\n" if rows else ""), encoding="utf-8")
            return
        fields = ["keyword", "used_at", "platform", "url", "title"] if history else ["keyword", "added_at"]
        with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
