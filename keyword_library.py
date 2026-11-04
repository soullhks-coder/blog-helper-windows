"""Local, transactional keyword inbox and append-only usage history.

Independent from app_state.json: frequent settings saves cannot overwrite it.
Each operation owns a connection, including publications on worker threads.
"""
from __future__ import annotations

import csv
import io
import os
import re
import sqlite3
import tempfile
import unicodedata
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path


def clean_keyword(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value))).strip()


def keyword_key(value: str) -> str:
    return clean_keyword(value).casefold()


def _keyword_entries(rows, *, headers=False):
    rows = [row for row in rows if any(cell.strip() for cell in row)]
    if not rows:
        return []
    names = [cell.strip().casefold() for cell in rows[0]]
    title_names = {"keyword", "키워드", "글쓰기 키워드"}
    wiki_names = {"wiki_keyword", "wiki keyword", "위키 검색어", "위키검색어", "위키 키워드", "위키키워드",
                  "사진 검색어", "사진검색어", "위키백과·나무위키 검색어", "위키백과", "나무위키"}
    named = any(name in title_names for name in names) and (headers or any(name in wiki_names for name in names))
    column = next((i for i, name in enumerate(names) if name in title_names), 0) if named else 0
    wiki_column = next((i for i, name in enumerate(names) if name in wiki_names), None) if named else 1
    result = []
    for index, row in enumerate(rows[1:] if named else rows, 2 if named else 1):
        if not named and len(row) > 2:
            raise ValueError(f"{index}행은 ‘글쓰기 키워드, 위키 검색어’ 두 항목까지 입력해 주세요. 문구에 쉼표가 있으면 큰따옴표로 묶어 주세요.")
        if len(row) <= column or not clean_keyword(row[column]):
            raise ValueError(f"{index}행의 글쓰기 키워드가 비어 있습니다.")
        entry = {"keyword": clean_keyword(row[column])}
        if wiki_column is not None and len(row) > wiki_column:
            entry["wiki_keyword"] = clean_keyword(row[wiki_column])
        result.append(entry)
    return result


def parse_keyword_entries(text: str) -> list[dict]:
    try:
        return _keyword_entries(list(csv.reader(io.StringIO(text), skipinitialspace=True, strict=True)))
    except csv.Error as exc:
        raise ValueError("쉼표 구분 형식을 확인해 주세요. 문구에 쉼표나 큰따옴표가 있으면 CSV 형식으로 묶어 주세요.") from exc


def read_keyword_entries(path: Path) -> list[dict]:
    data = Path(path).read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp949")
    try:
        return _keyword_entries(list(csv.reader(io.StringIO(text), skipinitialspace=True, strict=True)),
                                headers=Path(path).suffix.lower() == ".csv")
    except csv.Error as exc:
        raise ValueError("키워드 파일의 쉼표·큰따옴표 형식을 확인해 주세요.") from exc


def read_keyword_file(path: Path) -> list[str]:
    return [entry["keyword"] for entry in read_keyword_entries(path)]


class KeywordLibrary:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='keywords'").fetchone()
            migrate_wiki = exists and 'wiki_keyword' not in {row['name'] for row in db.execute('PRAGMA table_info(keywords)')}
            if migrate_wiki:
                self._backup_before_wiki_migration(db)
            db.executescript("""
                CREATE TABLE IF NOT EXISTS keywords (
                    id INTEGER PRIMARY KEY, key TEXT NOT NULL UNIQUE,
                    keyword TEXT NOT NULL, added_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'used', 'removed')),
                    wiki_keyword TEXT NOT NULL DEFAULT ''
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
                CREATE TABLE IF NOT EXISTS keyword_removals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, removed_at TEXT NOT NULL,
                    scope_group_id INTEGER, search TEXT NOT NULL, restored_at TEXT
                );
                CREATE TABLE IF NOT EXISTS keyword_removal_items (
                    removal_id INTEGER NOT NULL REFERENCES keyword_removals(id),
                    keyword_id INTEGER NOT NULL REFERENCES keywords(id),
                    key TEXT NOT NULL, keyword TEXT NOT NULL, added_at TEXT NOT NULL,
                    group_id INTEGER NOT NULL, group_name TEXT NOT NULL,
                    PRIMARY KEY(removal_id, keyword_id)
                );
            """)
            if migrate_wiki:
                db.execute('BEGIN IMMEDIATE')
                if 'wiki_keyword' not in {row['name'] for row in db.execute('PRAGMA table_info(keywords)')}:
                    db.execute("ALTER TABLE keywords ADD COLUMN wiki_keyword TEXT NOT NULL DEFAULT ''")

    def _backup_before_wiki_migration(self, db):
        directory = self.path.parent / 'Keyword Backups'
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, name = tempfile.mkstemp(prefix='before-wiki-column-', suffix='.sqlite3', dir=directory)
        os.close(descriptor)
        try:
            with closing(sqlite3.connect(name)) as destination:
                db.backup(destination)
        except Exception:
            Path(name).unlink(missing_ok=True)
            raise

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
        result = self.add_entries([{"keyword": value} for value in values], group_id=group_id)
        return {key: result[key] for key in ("added", "duplicate", "used", "first_added_id")}

    def add_entries(self, entries, *, group_id=None) -> dict:
        # Validate before beginning a transaction: never silently truncate data.
        entries = list(entries)
        if any(not clean_keyword(row['keyword']) and clean_keyword(row.get('wiki_keyword') or '') for row in entries):
            raise ValueError('위키 검색어를 지정할 글쓰기 키워드를 입력해 주세요.')
        entries = [{"keyword": clean_keyword(entry["keyword"]),
                    "wiki_keyword": clean_keyword(entry.get("wiki_keyword") or "")}
                   for entry in entries if clean_keyword(entry["keyword"])]
        if len(entries) > 100_000 or any(max(len(row['keyword']), len(row['wiki_keyword'])) > 500 for row in entries):
            raise ValueError("한 번에 100,000개까지, 키워드 하나당 500자까지 등록할 수 있습니다.")
        result = {"added": 0, "duplicate": 0, "used": 0, "first_added_id": None, "wiki_updated": 0, "other_group": 0}
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._check_group(db, group_id)
            for entry in entries:
                value, wiki = entry['keyword'], entry['wiki_keyword']
                row = db.execute("SELECT k.id, k.status, k.wiki_keyword, COALESCE(m.group_id, 0) AS group_id "
                                 "FROM keywords k LEFT JOIN keyword_group_membership m ON m.keyword_id=k.id WHERE k.key=?",
                                 (keyword_key(value),)).fetchone()
                if row and row["status"] != "removed":
                    result["used" if row["status"] == "used" else "duplicate"] += 1
                    if row['status'] == 'pending' and wiki and wiki != row['wiki_keyword']:
                        if group_id is not None and row['group_id'] != int(group_id):
                            result['other_group'] += 1
                        else:
                            db.execute('UPDATE keywords SET wiki_keyword=? WHERE id=?', (wiki, row['id']))
                            result['wiki_updated'] += 1
                            if result['first_added_id'] is None:
                                result['first_added_id'] = row['id']
                    continue
                if row:
                    db.execute("UPDATE keywords SET status='pending', keyword=?, added_at=?, wiki_keyword=? WHERE id=?",
                               (value, self._now(), wiki or row['wiki_keyword'], row["id"]))
                    item_id = row["id"]
                else:
                    item_id = db.execute("INSERT INTO keywords(key, keyword, added_at, wiki_keyword) VALUES (?, ?, ?, ?)",
                                         (keyword_key(value), value, self._now(), wiki)).lastrowid
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
                fields = "u.*, k.status, k.wiki_keyword, u.keyword_id AS item_id"
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

    def update_wiki_keyword(self, expected, value) -> bool:
        value = clean_keyword(value)
        if len(value) > 500:
            raise ValueError('위키 검색어는 500자까지 입력해 주세요.')
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT k.*, COALESCE(m.group_id, 0) AS group_id FROM keywords k '
                                 'LEFT JOIN keyword_group_membership m ON m.keyword_id=k.id WHERE k.id=?',
                                 (int(expected['item_id']),)).fetchone()
            if (current is None or current['status'] != 'pending' or any(current[field] != expected[field]
                    for field in ('key', 'added_at', 'group_id', 'wiki_keyword'))):
                raise ValueError('키워드의 그룹·상태·검색어가 바뀌어 저장하지 않았습니다. 목록에서 다시 선택해 주세요.')
            if current['wiki_keyword'] == value:
                return False
            db.execute('UPDATE keywords SET wiki_keyword=? WHERE id=?', (value, int(expected['item_id'])))
        return True

    def remove_selected(self, rows, *, group_id=None, search="") -> dict:
        """Delete exactly the confirmed snapshot, or reject the entire batch."""
        rows = [dict(row) for row in rows]
        if not rows:
            return {"id": None, "count": 0}
        ids = [int(row["item_id"]) for row in rows]
        if len(ids) != len(set(ids)):
            raise ValueError("선택 목록이 중복되었습니다. 목록을 다시 확인해 주세요.")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._check_group(db, group_id)
            for expected, item_id in zip(rows, ids):
                current = db.execute("SELECT k.*, COALESCE(m.group_id, 0) AS group_id FROM keywords k "
                                     "LEFT JOIN keyword_group_membership m ON m.keyword_id=k.id WHERE k.id=?",
                                     (item_id,)).fetchone()
                if (current is None or current["status"] != "pending"
                    or any(current[field] != expected[field] for field in ("key", "keyword", "added_at", "group_id", "wiki_keyword"))
                    or (group_id is not None and current["group_id"] != int(group_id))
                    or keyword_key(search) not in current["key"]):
                    raise ValueError("선택한 키워드의 그룹·검색 조건·상태가 바뀌어 삭제하지 않았습니다. 목록에서 다시 선택해 주세요.")
            removal_id = db.execute("INSERT INTO keyword_removals(removed_at, scope_group_id, search) VALUES (?, ?, ?)",
                                    (self._now(), group_id, clean_keyword(search))).lastrowid
            db.executemany("INSERT INTO keyword_removal_items VALUES (?, ?, ?, ?, ?, ?, ?)",
                           [(removal_id, item_id, row["key"], row["keyword"], row["added_at"],
                             row["group_id"], row["group_name"]) for row, item_id in zip(rows, ids)])
            db.executemany("UPDATE keywords SET status='removed' WHERE id=?", [(item_id,) for item_id in ids])
        return {"id": removal_id, "count": len(ids)}

    @staticmethod
    def _restorable_rows(db, removal_id):
        # Re-added, published or deleted-again keywords belong to their newer
        # operation. An old undo must never overwrite those later changes.
        return [dict(row) for row in db.execute(
            "SELECT k.id AS item_id, k.keyword, COALESCE(g.name, '미분류') AS group_name "
            "FROM keyword_removal_items i JOIN keywords k ON k.id=i.keyword_id "
            "LEFT JOIN keyword_group_membership m ON m.keyword_id=k.id "
            "LEFT JOIN keyword_groups g ON g.id=m.group_id "
            "WHERE i.removal_id=? AND k.status='removed' AND k.key=i.key AND k.added_at=i.added_at "
            "AND NOT EXISTS (SELECT 1 FROM keyword_removal_items newer WHERE newer.keyword_id=k.id "
            "AND newer.removal_id>i.removal_id) ORDER BY k.id", (int(removal_id),))]

    def last_removal(self):
        with self._connection() as db:
            for batch in db.execute("SELECT * FROM keyword_removals WHERE restored_at IS NULL ORDER BY id DESC"):
                rows = self._restorable_rows(db, batch["id"])
                if rows:
                    return {**dict(batch), "rows": rows}
        return None

    def restore_removal(self, removal_id) -> int:
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            batch = db.execute("SELECT restored_at FROM keyword_removals WHERE id=?", (int(removal_id),)).fetchone()
            if batch is None or batch["restored_at"] is not None:
                return 0
            rows = self._restorable_rows(db, removal_id)
            db.executemany("UPDATE keywords SET status='pending' WHERE id=?", [(row["item_id"],) for row in rows])
            db.execute("UPDATE keyword_removals SET restored_at=? WHERE id=?", (self._now(), int(removal_id)))
        return len(rows)

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
            with Path(path).open('w', encoding='utf-8', newline='') as stream:
                writer = csv.writer(stream, lineterminator='\n')
                writer.writerows([row['keyword'], row['wiki_keyword']] if row['wiki_keyword'] else [row['keyword']] for row in rows)
            return
        fields = ["keyword", "wiki_keyword", "group_name", "used_at", "platform", "url", "title"] if history else ["keyword", "wiki_keyword", "group_name", "added_at"]
        with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
