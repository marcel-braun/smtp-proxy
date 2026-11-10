import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class QueueFullError(Exception):
    pass


class QueueStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as database:
            database.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY, sender TEXT NOT NULL, content BLOB NOT NULL,
                    options TEXT NOT NULL, created REAL NOT NULL, size INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS recipients (
                    message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
                    address TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '', PRIMARY KEY(message_id, address)
                );
                CREATE INDEX IF NOT EXISTS due_recipients ON recipients(status, next_attempt);
            """)

    @contextmanager
    def connection(self):
        database = sqlite3.connect(self.path, timeout=10)
        database.row_factory = sqlite3.Row
        database.execute("PRAGMA foreign_keys=ON")
        database.execute("PRAGMA synchronous=FULL")
        database.execute("PRAGMA secure_delete=ON")
        try:
            with database:
                yield database
        finally:
            database.close()

    def enqueue(self, sender, recipients, content, options=(), *, max_bytes):
        identifier = uuid.uuid4().hex
        with self.connection() as database:
            database.execute("BEGIN IMMEDIATE")
            used = database.execute("SELECT COALESCE(SUM(size),0) FROM messages").fetchone()[0]
            count = database.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            if used + len(content) > max_bytes or count >= 10000:
                raise QueueFullError("Warteschlange voll.")
            database.execute(
                "INSERT INTO messages VALUES (?,?,?,?,?,?)",
                (identifier, sender, content, json.dumps(list(options)), time.time(), len(content)),
            )
            database.executemany(
                "INSERT INTO recipients(message_id,address) VALUES (?,?)",
                [(identifier, address) for address in dict.fromkeys(recipients)],
            )
        return identifier

    def next_due(self, max_age_hours):
        now = time.time()
        with self.connection() as database:
            database.execute(
                "UPDATE recipients SET status='failed',error='Aufbewahrungsfrist erreicht' "
                "WHERE status='pending' AND message_id IN "
                "(SELECT id FROM messages WHERE created < ?)",
                (now - max_age_hours * 3600,),
            )
            row = database.execute(
                "SELECT m.* FROM messages m JOIN recipients r ON r.message_id=m.id "
                "WHERE r.status='pending' AND r.next_attempt<=? ORDER BY m.created LIMIT 1",
                (now,),
            ).fetchone()
            if row is None:
                return None
            job = dict(row)
            job["options"] = json.loads(job["options"])
            job["recipients"] = [dict(recipient) for recipient in database.execute(
                "SELECT * FROM recipients WHERE message_id=? AND status='pending' "
                "AND next_attempt<=?", (job["id"], now),
            )]
            return job

    def record(self, identifier, outcomes):
        with self.connection() as database:
            for address, (status, next_attempt, error) in outcomes.items():
                database.execute(
                    "UPDATE recipients SET status=?,next_attempt=?,error=?,attempts=attempts+1 "
                    "WHERE message_id=? AND address=?",
                    (status, next_attempt, error, identifier, address),
                )
            remaining = database.execute(
                "SELECT COUNT(*) FROM recipients WHERE message_id=? AND status!='sent'",
                (identifier,),
            ).fetchone()[0]
            if not remaining:
                database.execute("DELETE FROM messages WHERE id=?", (identifier,))

    def list_messages(self):
        with self.connection() as database:
            return [dict(row) for row in database.execute(
                "SELECT m.id,m.sender,m.created,m.size,r.address,r.status,r.attempts,r.error "
                "FROM messages m JOIN recipients r ON r.message_id=m.id ORDER BY m.created LIMIT 1000"
            )]

    def retry(self, identifier):
        with self.connection() as database:
            database.execute("UPDATE messages SET created=? WHERE id=?", (time.time(), identifier))
            database.execute(
                "UPDATE recipients SET status='pending',next_attempt=0,error='',attempts=0 "
                "WHERE message_id=? AND status!='sent'", (identifier,),
            )

    def delete(self, identifier):
        with self.connection() as database:
            database.execute("DELETE FROM messages WHERE id=?", (identifier,))