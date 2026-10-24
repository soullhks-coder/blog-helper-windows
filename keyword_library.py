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
                CREATE TABLE IF NOT EXISTS keyword_groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS keyword_group_membership (
                    keyword_id INTEGER PRIMARY KEY REFERENCES keywords(id) ON DELETE CASCADE,
                    group_id INTEGER NOT NULL REFERENCES keyword_groups(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS keyword_group_members
                    ON keyword_group_membership(group_id, keyword_id);
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

    @staticmethod
    def _group_name(name):
        name = clean_keyword(name)
        if not name or len(name) > 40 or keyword_key(name) in {"전체 그룹", "미분류"}:
            raise ValueError("그룹 이름은 1~40자로 입력해 주세요. ‘전체 그룹’·‘미분류’는 사용할 수 없습니다.")
        return name

    @staticmethod
    def _check_group(db, group_id):
        if group_id and not db.execute("SELECT 1 FROM keyword_groups WHERE id=?", (int(group_id),)).fetchone():
            raise ValueError("그룹을 찾을 수 없습니다. 목록을 다시 확인해 주세요.")

    @staticmethod
    def _set_group(db, item_id, group_id):
        if group_id:
            db.execute("INSERT INTO keyword_group_membership(keyword_id, group_id) VALUES (?, ?) "
                       "ON CONFLICT(keyword_id) DO UPDATE SET group_id=excluded.group_id", (item_id, int(group_id)))
        else:
            db.execute("DELETE FROM keyword_group_membership WHERE keyword_id=?", (item_id,))

    def groups(self) -> list[dict]:
        with self._connection() as db:
            return [dict(row) for row in db.execute("SELECT id, name FROM keyword_groups ORDER BY id")]

    def create_group(self, name) -> int:
        name = self._group_name(name)
        try:
            with self._connection() as db:
                return db.execute("INSERT INTO keyword_groups(key, name, created_at) VALUES (?, ?, ?)",
                                  (keyword_key(name), name, self._now())).lastrowid
        except sqlite3.IntegrityError as exc:
            raise ValueError("같은 이름의 그룹이 이미 있습니다.") from exc

    def rename_group(self, group_id, name):
        name = self._group_name(name)
        if not group_id:
            raise ValueError("이름을 변경할 그룹을 선택해 주세요.")
        try:
            with self._connection() as db:
                db.execute("BEGIN IMMEDIATE")
                self._check_group(db, group_id)
                db.execute("UPDATE keyword_groups SET key=?, name=? WHERE id=?",
                           (keyword_key(name), name, int(group_id)))
        except sqlite3.IntegrityError as exc:
            raise ValueError("같은 이름의 그룹이 이미 있습니다.") from exc

    def delete_group(self, group_id):
        # Only the folder/assignments are removed; keywords and usage stay intact.
        with self._connection() as db:
            db.execute("DELETE FROM keyword_groups WHERE id=?", (int(group_id),))

    def assign_group(self, ids, group_id) -> int:
        changed = 0
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._check_group(db, group_id)
            for item_id in set(int(item) for item in ids):
                row = db.execute("SELECT COALESCE(m.group_id, 0) AS group_id FROM keywords k "
                                 "LEFT JOIN keyword_group_membership m ON m.keyword_id=k.id WHERE k.id=?",
                                 (item_id,)).fetchone()
                if row is not None and row["group_id"] != int(group_id or 0):
                    self._set_group(db, item_id, group_id)
                    changed += 1
        return changed

    def add(self, values, *, group_id=None) -> dict:
        # Validate before beginning a transaction: never silently truncate data.
        values = [clean_keyword(value) for value in values if clean_keyword(value)]
        if len(values) > 100_000 or any(len(value) > 500 for value in values):
            raise ValueError("한 번에 100,000개까지, 키워드 하나당 500자까지 등록할 수 있습니다.")
        result = {"added": 0, "duplicate": 0, "used": 0, "first_added_id": None}
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._check_group(db, group_id)
            for value in values:
                row = db.execute("SELECT id, status FROM keywords WHERE key=?", (keyword_key(value),)).fetchone()
                if row and row["status"] != "removed":
                    result["used" if row["status"] == "used" else "duplicate"] += 1
                    continue
                if row:
                    db.execute("UPDATE keywords SET status='pending', keyword=?, added_at=? WHERE id=?",
                               (value, self._now(), row["id"]))
                    item_id = row["id"]
                else:
                    item_id = db.execute("INSERT INTO keywords(key, keyword, added_at) VALUES (?, ?, ?)",
                                         (keyword_key(value), value, self._now())).lastrowid
                if result["first_added_id"] is None:
                    result["first_added_id"] = item_id
                self._set_group(db, item_id, group_id)
                result["added"] += 1
        return result

    def pending_page_offset(self, item_id, page_size=40, *, group_id=None) -> int:
        """Locate an added/restored row without reordering the existing inbox."""
        if item_id is None:
            return 0
        with self._connection() as db:
            position = db.execute("SELECT COUNT(*) FROM keywords k LEFT JOIN keyword_group_membership m "
                                  "ON m.keyword_id=k.id WHERE k.status='pending' AND k.id < ? "
                                  "AND (? IS NULL OR COALESCE(m.group_id, 0)=?)",
                                  (int(item_id), group_id, group_id)).fetchone()[0]
        page_size = max(1, int(page_size))
        return position // page_size * page_size

    def counts(self, *, group_id=None) -> dict:
        with self._connection() as db:
            result = {"pending": 0, "used": 0, "history": 0}
            for row in db.execute("SELECT k.status, COUNT(*) AS count FROM keywords k "
                                  "LEFT JOIN keyword_group_membership m ON m.keyword_id=k.id "
                                  "WHERE (? IS NULL OR COALESCE(m.group_id, 0)=?) GROUP BY k.status",
                                  (group_id, group_id)):
                if row["status"] in result:
                    result[row["status"]] = row["count"]
            result["history"] = db.execute("SELECT COUNT(*) FROM usage u "
                                           "LEFT JOIN keyword_group_membership m ON m.keyword_id=u.keyword_id "
                                           "WHERE (? IS NULL OR COALESCE(m.group_id, 0)=?)",
                                           (group_id, group_id)).fetchone()[0]
            return result

    def list(self, *, history=False, search="", offset=0, limit=40, group_id=None) -> tuple[list[dict], int]:
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
            source += (" LEFT JOIN keyword_group_membership m ON m.keyword_id=k.id"
                       " LEFT JOIN keyword_groups g ON g.id=m.group_id")
            fields += ", COALESCE(m.group_id, 0) AS group_id, COALESCE(g.name, '미분류') AS group_name"
            condition += " AND (? IS NULL OR COALESCE(m.group_id, 0)=?)"
            params = (query, group_id, group_id)
            total = db.execute(f"SELECT COUNT(*) FROM {source} WHERE {condition}", params).fetchone()[0]
            rows = db.execute(f"SELECT {fields} FROM {source} WHERE {condition} ORDER BY {order} LIMIT ? OFFSET ?",
                              (*params, max(1, int(limit)), max(0, int(offset)))).fetchall()
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

    def export(self, path: Path, *, history=False, group_id=None):
        rows, _ = self.list(history=history, group_id=group_id, limit=2_147_483_647)
        if Path(path).suffix.lower() == ".txt":
            Path(path).write_text("\n".join(row["keyword"] for row in rows) + ("\n" if rows else ""), encoding="utf-8")
            return
        fields = ["keyword", "group_name", "used_at", "platform", "url", "title"] if history else ["keyword", "group_name", "added_at"]
        with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
