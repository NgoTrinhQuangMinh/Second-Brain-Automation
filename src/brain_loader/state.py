import json
import sqlite3


class State:
    """Durable per-destination manifest, including interrupted uploads."""

    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("""CREATE TABLE IF NOT EXISTS documents (
            scope TEXT, file_id TEXT, fingerprint TEXT, active TEXT, pending TEXT,
            PRIMARY KEY(scope, file_id))""")
        self.db.commit()

    def get(self, scope, file_id):
        row = self.db.execute(
            "SELECT fingerprint, active, pending FROM documents WHERE scope=? AND file_id=?", (scope, file_id)
        ).fetchone()
        return (row[0], json.loads(row[1]), json.loads(row[2])) if row else ("", [], [])

    def prepare(self, scope, file_id, ids):
        fingerprint, active, pending = self.get(scope, file_id)
        pending = sorted(set(pending + ids))
        self.db.execute(
            "INSERT OR REPLACE INTO documents VALUES (?, ?, ?, ?, ?)",
            (scope, file_id, fingerprint, json.dumps(active), json.dumps(pending)),
        )
        self.db.commit()

    def commit(self, scope, file_id, fingerprint, ids):
        self.db.execute(
            "INSERT OR REPLACE INTO documents VALUES (?, ?, ?, ?, ?)",
            (scope, file_id, fingerprint, json.dumps(ids), "[]"),
        )
        self.db.commit()

    def close(self):
        self.db.close()

    def forget(self, scope, file_id):
        self.db.execute("DELETE FROM documents WHERE scope=? AND file_id=?", (scope, file_id))
        self.db.commit()
