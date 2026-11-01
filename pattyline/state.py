"""SQLite is the sole execution/ownership authority for the primary checkout."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import uuid
import os
from datetime import datetime, timezone

from .common import Blocked, canonical, now, write_json, atomic_write, redact

DDL = """
CREATE TABLE IF NOT EXISTS runs (
 id TEXT PRIMARY KEY, created TEXT NOT NULL, snapshot TEXT NOT NULL,
 status TEXT NOT NULL, stop INTEGER NOT NULL DEFAULT 0, deadline REAL NOT NULL);
CREATE TABLE IF NOT EXISTS work (
 ticket TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, run TEXT NOT NULL,
 state TEXT NOT NULL, stage TEXT, reason TEXT, branch TEXT, worktree TEXT,
 base TEXT, pin TEXT, review TEXT, integration TEXT,
 attempts INTEGER NOT NULL DEFAULT 0, local_attempts INTEGER NOT NULL DEFAULT 0,
 owner TEXT, process TEXT, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS claims (
 capacity INTEGER PRIMARY KEY CHECK(capacity=1), ticket TEXT UNIQUE NOT NULL,
 run TEXT NOT NULL, owner TEXT NOT NULL, controller TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS run_work (
 run TEXT NOT NULL, ticket TEXT NOT NULL, evidence TEXT NOT NULL,
 PRIMARY KEY(run,ticket));
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, run TEXT NOT NULL, ticket TEXT,
 time TEXT NOT NULL, kind TEXT NOT NULL, evidence TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS operations (
 id TEXT PRIMARY KEY, run TEXT NOT NULL, ticket TEXT NOT NULL,
 kind TEXT NOT NULL, intent TEXT NOT NULL, result TEXT);
"""


class State:
    def __init__(self, root):
        self.root = Path(root)
        directory = self.root / ".pattyline"
        directory.mkdir(exist_ok=True)
        self.db = sqlite3.connect(directory / "state.sqlite3", timeout=10, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, 2):
            raise Blocked(f"Unsupported execution database version {version}; preserve state")
        if version == 1:
            with self.transaction():
                self.db.execute("ALTER TABLE claims ADD COLUMN controller TEXT NOT NULL DEFAULT '{}' ")
                self.db.execute("PRAGMA user_version=2")
        self.db.executescript(DDL)
        self.db.execute("PRAGMA user_version=2")

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def create_run(self, snapshot, deadline):
        identity = uuid.uuid4().hex
        with self.transaction():
            if self.db.execute("SELECT 1 FROM claims").fetchone():
                raise Blocked("Project capacity already owned; inspect status/resume before starting")
            active = self.db.execute("SELECT id FROM runs WHERE status='running'").fetchone()
            if active:
                raise Blocked(f"Recover unfinished run {active['id']} before starting another")
            self.db.execute("INSERT INTO runs VALUES(?,?,?,'running',0,?)", (identity, now(), canonical(snapshot), deadline))
            for ticket, fingerprint in snapshot["fingerprints"].items():
                prior = self.get_work(ticket)
                if prior:
                    self.db.execute("INSERT OR REPLACE INTO run_work VALUES(?,?,?)", (prior["run"], ticket, canonical(prior)))
                if prior and prior["state"] == "integrated":
                    if prior["fingerprint"] != fingerprint:
                        raise Blocked(f"{ticket}: integrated identity cannot be reused for new requirements")
                    continue
                if prior and prior["fingerprint"] != fingerprint:
                    self.event(identity, ticket, "successor", {"prior_run": prior["run"], "attempts": prior["attempts"], "preserved_worktree": prior["worktree"]})
                    # Preserve branch, history and budgets. New intent must reconcile prior changes.
                if prior:
                    if prior["fingerprint"] != fingerprint:
                        self.db.execute("UPDATE work SET run=?,fingerprint=?,state='queued',reason=NULL,review=NULL,updated=? WHERE ticket=?",
                                        (identity, fingerprint, now(), ticket))
                    else:
                        self.db.execute("UPDATE work SET run=?,updated=? WHERE ticket=?", (identity, now(), ticket))
                else:
                    self.db.execute("INSERT INTO work(ticket,fingerprint,run,state,updated) VALUES(?,?,?,'queued',?)", (ticket, fingerprint, identity, now()))
        return identity

    def run(self, identity=None):
        row = self.db.execute("SELECT * FROM runs WHERE id=?", (identity,)).fetchone() if identity else self.db.execute("SELECT * FROM runs ORDER BY created DESC LIMIT 1").fetchone()
        if not row:
            raise Blocked("No matching run")
        result = dict(row)
        result["snapshot"] = json.loads(result["snapshot"])
        return result

    def get_work(self, ticket):
        row = self.db.execute("SELECT * FROM work WHERE ticket=?", (ticket,)).fetchone()
        return dict(row) if row else None

    def claim(self, run, ticket):
        from .process import process_identity
        owner = uuid.uuid4().hex
        controller = {"pid": os.getpid(), "identity": process_identity(os.getpid())}
        with self.transaction():
            if self.run(run)["stop"]:
                raise Blocked("Run cancellation requested")
            row = self.get_work(ticket)
            if not row or row["run"] != run or row["owner"] or row["state"] == "integrated":
                raise Blocked(f"{ticket}: work is not claimable")
            try:
                self.db.execute("INSERT INTO claims VALUES(1,?,?,?,?)", (ticket, run, owner, canonical(controller)))
            except sqlite3.IntegrityError as error:
                raise Blocked("Project capacity is already claimed; no concurrent execution") from error
            self.db.execute("UPDATE work SET owner=?,updated=? WHERE ticket=?", (owner, now(), ticket))
        return owner

    def release(self, ticket, owner):
        with self.transaction():
            row = self.get_work(ticket)
            if row["owner"] != owner:
                raise Blocked("Ownership mismatch; preserve claim")
            if row["process"]:
                raise Blocked("Process termination not established; preserve claim")
            self.db.execute("DELETE FROM claims WHERE ticket=? AND owner=?", (ticket, owner))
            self.db.execute("UPDATE work SET owner=NULL WHERE ticket=?", (ticket,))

    def update(self, ticket, **values):
        allowed = {"state", "stage", "reason", "branch", "worktree", "base", "pin", "review", "integration", "process"}
        if not values.keys() <= allowed:
            raise ValueError("Invalid work fields")
        values = {k: canonical(v) if isinstance(v, (dict, list)) else v for k, v in values.items()}
        values["updated"] = now()
        self.db.execute("UPDATE work SET " + ",".join(f"{key}=?" for key in values) + " WHERE ticket=?", (*values.values(), ticket))

    def attempt(self, ticket, route, limits):
        with self.transaction():
            row = self.get_work(ticket)
            if row["attempts"] >= 1 + limits["repairs"]:
                raise Blocked("Implementation/repair budget exhausted; new run does not reset it")
            if route == "local" and row["local_attempts"] >= 1 + limits["local_repairs"]:
                raise Blocked("Local allocation exhausted; reserved cloud repair requires approved escalation")
            self.db.execute("UPDATE work SET attempts=attempts+1,local_attempts=local_attempts+?,review=NULL WHERE ticket=?", (int(route == "local"), ticket))
        return self.get_work(ticket)["attempts"]

    def event(self, run, ticket, kind, evidence):
        self.db.execute("INSERT INTO events(run,ticket,time,kind,evidence) VALUES(?,?,?,?,?)", (run, ticket, now(), kind, canonical(redact(evidence))))

    def intent(self, run, ticket, kind, evidence):
        identity = f"{run}:{ticket}:{kind}"
        self.db.execute("INSERT OR IGNORE INTO operations VALUES(?,?,?,?,?,NULL)", (identity, run, ticket, kind, canonical(evidence)))
        return dict(self.db.execute("SELECT * FROM operations WHERE id=?", (identity,)).fetchone())

    def complete_operation(self, identity, result):
        self.db.execute("UPDATE operations SET result=? WHERE id=?", (canonical(result), identity))

    def cancel(self, identity):
        self.db.execute("UPDATE runs SET stop=1 WHERE id=?", (identity,))

    def finish(self, identity, status):
        self.db.execute("UPDATE runs SET status=? WHERE id=?", (status, identity))
        self.event(identity, None, "run-status", {"status": status})
        for ticket in self.run(identity)["snapshot"]["order"]:
            row = self.get_work(ticket)
            if row:
                self.db.execute("INSERT OR REPLACE INTO run_work VALUES(?,?,?)", (identity, ticket, canonical(row)))
        return self.report(identity)

    def report(self, identity=None):
        run = self.run(identity)
        events = [dict(row) for row in self.db.execute("SELECT * FROM events WHERE run=? ORDER BY id", (run["id"],))]
        for event in events:
            event["evidence"] = json.loads(event["evidence"])
        work = []
        for ticket in run["snapshot"]["order"]:
            row = self.get_work(ticket)
            if row and row["run"] != run["id"]:
                stored = self.db.execute("SELECT evidence FROM run_work WHERE run=? AND ticket=?", (run["id"], ticket)).fetchone()
                row = json.loads(stored[0]) if stored else row
            work.append(row)
        usage = {"cloud": "unknown", "local": "unknown", "cost": "unknown", "reported_counts": {}}
        for event in events:
            if event["kind"] in {"implementation", "review"}:
                observed = event["evidence"].get("usage", [])
                for counts in observed if isinstance(observed, list) else []:
                    if isinstance(counts, dict):
                        route = event["evidence"].get("route", "cloud")
                        totals = usage["reported_counts"].setdefault(route, {})
                        for key, amount in counts.items():
                            if type(amount) is int:
                                totals[key] = totals.get(key, 0) + amount
        end = events[-1]["time"] if run["status"] != "running" and events else now()
        elapsed = max(0, (datetime.fromisoformat(end) - datetime.fromisoformat(run["created"])).total_seconds())
        value = {"run": run, "work": work, "events": events, "usage": usage, "elapsed_seconds": elapsed,
                 "remedy": "Resolve recorded blockers; prepare/approve changed requirements and start a successor; resume only unchanged requirements."}
        destination = self.root / ".pattyline/reports" / run["id"]
        write_json(destination.with_suffix(".json"), value)
        lines = [f"# Run {run['id']}", f"Status: {run['status']}", "", "Usage: unknown (no monetary guarantee)", ""]
        for row in work:
            if row:
                lines.append(f"- {row['ticket']}: {row['state']}; {row['reason'] or ''}")
        lines.extend(["", value["remedy"], "", f"Exact evidence: {destination.with_suffix('.json')}"])
        atomic_write(destination.with_suffix(".md"), "\n".join(lines) + "\n")
        value["report"] = str(destination.with_suffix(".md"))
        return value
