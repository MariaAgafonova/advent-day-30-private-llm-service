from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4
import json
import sqlite3
from .errors import ServiceError


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS owner_sessions ON sessions(owner, updated_at);
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                    request_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
                    sources TEXT NOT NULL DEFAULT '[]', metrics TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL, UNIQUE(request_id, role));
                CREATE INDEX IF NOT EXISTS session_messages ON messages(session_id, id);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, owner, message):
        sid, timestamp = str(uuid4()), now()
        with self.connect() as db:
            db.execute('INSERT INTO sessions VALUES (?,?,?,?,?)',
                       (sid, owner, message[:70], timestamp, timestamp))
        return sid

    def require(self, sid, owner):
        with self.connect() as db:
            if not db.execute('SELECT 1 FROM sessions WHERE id=? AND owner=?', (sid, owner)).fetchone():
                raise ServiceError(404, 'session_not_found', 'Сессия не найдена.')

    def sessions(self, owner):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT id,title,created_at,updated_at FROM sessions WHERE owner=? ORDER BY updated_at DESC LIMIT 100', (owner,))]

    def messages(self, sid, owner, limit=100, before=None):
        self.require(sid, owner)
        with self.connect() as db:
            rows = db.execute('SELECT * FROM messages WHERE session_id=? AND id<? ORDER BY id DESC LIMIT ?',
                              (sid, before or 9223372036854775807, limit)).fetchall()
        return [{**dict(r), 'sources': json.loads(r['sources']), 'metrics': json.loads(r['metrics'])} for r in reversed(rows)]

    def save_turn(self, sid, owner, rid, message, answer, sources, metrics):
        self.require(sid, owner)
        timestamp = now()
        with self.connect() as db:
            for role, content, refs, stats in [('user', message, [], {}), ('assistant', answer, sources, metrics)]:
                db.execute('INSERT INTO messages(session_id,request_id,role,content,sources,metrics,created_at) VALUES (?,?,?,?,?,?,?)',
                           (sid, rid, role, content, json.dumps(refs, ensure_ascii=False), json.dumps(stats), timestamp))
            db.execute('UPDATE sessions SET updated_at=? WHERE id=? AND owner=?', (timestamp, sid, owner))
