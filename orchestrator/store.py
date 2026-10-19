"""Durable outbox. A claim is never automatically expired or retried."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .git import fingerprint


def now():
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS actions (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, kind TEXT NOT NULL,
                    payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'INTENT',
                    result TEXT, locator TEXT, revision INTEGER NOT NULL DEFAULT 0,
                    sealed INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE,
                    timestamp TEXT NOT NULL, run_id TEXT NOT NULL, kind TEXT NOT NULL,
                    data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, plan TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1, initial TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS run_controls (
                    run_id TEXT PRIMARY KEY, mode TEXT NOT NULL, epoch INTEGER NOT NULL,
                    reason TEXT NOT NULL, checkpoint TEXT);
                CREATE TABLE IF NOT EXISTS bindings (
                    action_id TEXT PRIMARY KEY, external_id TEXT NOT NULL,
                    locator TEXT NOT NULL, state TEXT NOT NULL,
                    observed_epoch INTEGER NOT NULL, evidence TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS signals (
                    id TEXT PRIMARY KEY, action_id TEXT NOT NULL, run_id TEXT NOT NULL,
                    verb TEXT NOT NULL, epoch INTEGER NOT NULL, status TEXT NOT NULL,
                    evidence TEXT NOT NULL);
            """)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def event(self, run, kind, data, key=None):
        with self.db() as db:
            db.execute("INSERT OR IGNORE INTO events(key,timestamp,run_id,kind,data) "
                       "VALUES(?,?,?,?,?)", (key, now(), run, kind, json.dumps(data)))

    def intent(self, action_id, run, kind, payload):
        encoded = json.dumps(payload, sort_keys=True)
        with self.db() as db:
            db.execute("INSERT OR IGNORE INTO actions(id,run_id,kind,payload) VALUES(?,?,?,?)",
                       (action_id, run, kind, encoded))
            row = db.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
            if row["payload"] != encoded or row["kind"] != kind or row["run_id"] != run:
                raise ValueError("action ID reused with a different payload")
        self.event(run, "ACTION_INTENT", {"id": action_id, "kind": kind}, action_id + ":intent")
        return action_id

    @staticmethod
    def decode(row):
        item = dict(row)
        for key in ("payload", "result"):
            if item[key] is not None:
                item[key] = json.loads(item[key])
        return item

    def action(self, action_id):
        with self.db() as db:
            row = db.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
        if row is None:
            raise ValueError("unknown action")
        return self.decode(row)

    def actions(self):
        with self.db() as db:
            return [self.decode(r) for r in db.execute("SELECT * FROM actions ORDER BY rowid")]

    def claim(self, action_id):
        with self.db() as db:
            changed = db.execute("UPDATE actions SET status='CLAIMED' "
                                 "WHERE id=? AND status='INTENT' AND sealed=0", (action_id,))
            if changed.rowcount != 1:
                raise ValueError("action already claimed; reconcile external outcome first")
        row = self.action(action_id)
        self.event(row["run_id"], "ACTION_CLAIMED", {"id": action_id})
        return row

    def receipt(self, action_id, result, locator, expected_revision=None):
        if not isinstance(result, dict):
            raise TypeError("receipt must be a JSON object")
        if not locator.strip():
            raise ValueError("receipt requires actual external message/task locator")
        encoded = json.dumps(result, sort_keys=True)
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
            if row is None or row["status"] == "INTENT":
                raise ValueError("claim action before executing or recording a receipt")
            if row["result"] == encoded and row["locator"] == locator:
                return  # Exact duplicate delivery is idempotent.
            if row["sealed"]:
                raise ValueError("consumed receipt is immutable")
            bound = db.execute("SELECT * FROM bindings WHERE action_id=?", (action_id,)).fetchone()
            if (row["kind"] == "worker" and bound
                    and result.get("worker_id") != bound["external_id"]):
                raise ValueError("receipt worker identity differs from bound App thread")
            if row["kind"] == "worker" and result.get("worker_id"):
                for previous in db.execute(
                    "SELECT result FROM actions WHERE kind='worker' AND id<>? AND result IS NOT NULL",
                    (action_id,),
                ):
                    if json.loads(previous["result"]).get("worker_id") == result["worker_id"]:
                        raise ValueError("fresh attempt must use a new worker identity")
            if row["result"] is not None and expected_revision != row["revision"]:
                raise ValueError("receipt conflict; provide current expected_revision to correct")
            if row["result"]:
                old_worker = json.loads(row["result"]).get("worker_id")
                if old_worker and result.get("worker_id") != old_worker:
                    raise ValueError("cannot replace existing worker identity")
            revision = row["revision"] + 1
            db.execute("UPDATE actions SET status='RECEIVED',result=?,locator=?,revision=? "
                       "WHERE id=?", (encoded, locator, revision, action_id))
            db.execute("INSERT INTO events(timestamp,run_id,kind,data) VALUES(?,?,?,?)",
                       (now(), row["run_id"], "ACTION_RECEIPT", json.dumps({
                           "id": action_id, "revision": revision, "result": result,
                           "locator": locator})))

    def seal(self, action_id):
        with self.db() as db:
            db.execute("UPDATE actions SET sealed=1 WHERE id=?", (action_id,))

    def reconcile_absent(self, action_id, evidence):
        if not evidence.strip():
            raise ValueError("external absence requires reconciliation evidence")
        with self.db() as db:
            if db.execute("SELECT 1 FROM bindings WHERE action_id=?", (action_id,)).fetchone():
                raise ValueError("bound external action cannot be reconciled absent")
            changed = db.execute("UPDATE actions SET status='INTENT' WHERE id=? "
                                 "AND status='CLAIMED' AND result IS NULL AND sealed=0", (action_id,))
            if changed.rowcount != 1:
                raise ValueError("only an unresolved claimed action can be reconciled absent")
        self.event(self.action(action_id)["run_id"], "RECONCILED_ABSENT",
                   {"id": action_id, "evidence": evidence})

    def events(self, run):
        with self.db() as db:
            return [{**dict(r), "data": json.loads(r["data"])} for r in db.execute(
                "SELECT * FROM events WHERE run_id=? ORDER BY seq", (run,))]

    def register(self, run, config, plan, initial):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM runs WHERE id=?", (run,)).fetchone():
                raise ValueError("run already exists; use tick")
            if db.execute("SELECT 1 FROM runs WHERE active=1").fetchone():
                raise ValueError("another run is active in this checkout")
            db.execute("INSERT INTO runs(id,fingerprint,plan,initial) VALUES(?,?,?,?)",
                       (run, fingerprint(config), plan, json.dumps(initial)))

    def initial(self, run):
        with self.db() as db:
            row = db.execute("SELECT initial FROM runs WHERE id=?", (run,)).fetchone()
        if not row:
            raise ValueError("unknown run")
        return json.loads(row["initial"])

    def check_run(self, run, config, plan):
        with self.db() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run,)).fetchone()
        if not row:
            raise ValueError("unknown run")
        if row["fingerprint"] != fingerprint(config) or row["plan"] != plan:
            raise ValueError("plan or configuration changed; explicit amendment required")

    def complete(self, run):
        with self.db() as db:
            db.execute("UPDATE runs SET active=0 WHERE id=?", (run,))

    def control_state(self, run):
        self.initial(run)  # Reject unknown runs, including on old databases.
        with self.db() as db:
            row = db.execute("SELECT * FROM run_controls WHERE run_id=?", (run,)).fetchone()
        if row is None:
            return {"mode": "RUNNING", "epoch": 0, "reason": "", "checkpoint": None}
        result = dict(row)
        result.pop("run_id")
        result["checkpoint"] = json.loads(result["checkpoint"]) if result["checkpoint"] else None
        return result

    def set_control(self, run, mode, epoch, reason, checkpoint=None):
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO run_controls VALUES(?,?,?,?,?)",
                       (run, mode, epoch, reason,
                        json.dumps(checkpoint) if checkpoint is not None else None))
        self.event(run, "RUN_CONTROL", {"mode": mode, "epoch": epoch, "reason": reason})

    def binding(self, action_id):
        with self.db() as db:
            row = db.execute("SELECT * FROM bindings WHERE action_id=?", (action_id,)).fetchone()
        return dict(row) if row else None

    def bind(self, action_id, external_id, locator):
        if not external_id.strip() or not locator.strip():
            raise ValueError("external identity and locator are required")
        action = self.action(action_id)
        if action["status"] != "CLAIMED" or action["sealed"]:
            raise ValueError("bind only an unresolved claimed action")
        prior = self.binding(action_id)
        if prior:
            if prior["external_id"] != external_id:
                raise ValueError("external identity already bound")
            return prior
        with self.db() as db:
            if action["kind"] == "worker" and db.execute(
                "SELECT 1 FROM bindings b JOIN actions a ON b.action_id=a.id "
                "WHERE a.kind='worker' AND b.external_id=?", (external_id,),
            ).fetchone():
                raise ValueError("fresh attempt requires a different worker identity")
            db.execute("INSERT INTO bindings VALUES(?,?,?,?,?,?)",
                       (action_id, external_id, locator, "RUNNING", -1, "created/located"))
        self.event(action["run_id"], "EXTERNAL_BOUND",
                   {"action_id": action_id, "external_id": external_id, "locator": locator})
        return self.binding(action_id)

    def observe(self, action_id, state, evidence, epoch):
        if state not in {"RUNNING", "QUIESCENT"} or not evidence.strip():
            raise ValueError("observation requires RUNNING/QUIESCENT and platform evidence")
        if not self.binding(action_id):
            raise ValueError("action has no external binding")
        with self.db() as db:
            db.execute("UPDATE bindings SET state=?,evidence=?,observed_epoch=? WHERE action_id=?",
                       (state, evidence, epoch, action_id))
        self.event(self.action(action_id)["run_id"], "EXTERNAL_OBSERVED",
                   {"action_id": action_id, "state": state, "epoch": epoch, "evidence": evidence})

    def signals(self, run):
        with self.db() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM signals WHERE run_id=? ORDER BY rowid", (run,))]

    def claim_signal(self, action_id, verb, epoch):
        action = self.action(action_id)
        signal_id = f"{action_id}:{verb}:{epoch}"
        with self.db() as db:
            prior = db.execute("SELECT * FROM signals WHERE id=?", (signal_id,)).fetchone()
            if prior and prior["status"] != "ABSENT":
                raise ValueError("signal already claimed/sent; reconcile outcome")
            db.execute("INSERT OR REPLACE INTO signals VALUES(?,?,?,?,?,?,?)",
                       (signal_id, action_id, action["run_id"], verb, epoch, "CLAIMED", ""))
        self.event(action["run_id"], "SIGNAL_CLAIMED", {"id": signal_id})
        return next(s for s in self.signals(action["run_id"]) if s["id"] == signal_id)

    def resolve_signal(self, signal_id, evidence, *, absent=False):
        if not evidence.strip():
            raise ValueError("signal outcome requires platform evidence")
        with self.db() as db:
            row = db.execute("SELECT * FROM signals WHERE id=?", (signal_id,)).fetchone()
            if row is None:
                raise ValueError("unknown signal")
            target = "ABSENT" if absent else "SENT"
            if row["status"] == target:
                return dict(row)
            if row["status"] != "CLAIMED":
                raise ValueError("only a claimed signal can be reconciled")
            db.execute("UPDATE signals SET status=?,evidence=? WHERE id=?",
                       (target, evidence, signal_id))
            # Even a late acknowledgment can mean the Worker restarted.
            # Invalidate old quiescence before any pause confirmation.
            if not absent:
                db.execute("UPDATE bindings SET state='RUNNING',observed_epoch=-1 "
                           "WHERE action_id=?", (row["action_id"],))
        self.event(row["run_id"], "SIGNAL_" + target, {"id": signal_id, "evidence": evidence})
        return {**dict(row), "status": target, "evidence": evidence}
