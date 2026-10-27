"""Small SQLite archive; source and privacy policy boundaries are explicit."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from .facts import digest


class DiaryStore:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.directory = directory.resolve()
        self.db = sqlite3.connect(directory / "diaries.sqlite3", timeout=5)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS sources (
                source TEXT, zone TEXT, enabled_date TEXT, PRIMARY KEY(source, zone));
            CREATE TABLE IF NOT EXISTS entries (
                source TEXT, zone TEXT, day TEXT, policy TEXT, body TEXT,
                PRIMARY KEY(source, zone, day));
            CREATE TABLE IF NOT EXISTS revisions (
                source TEXT, zone TEXT, day TEXT, revision INTEGER, body TEXT,
                PRIMARY KEY(source, zone, day, revision));
            CREATE TABLE IF NOT EXISTS jobs (
                source TEXT, zone TEXT, day TEXT, attempts INTEGER, retry_at REAL, error TEXT,
                PRIMARY KEY(source, zone, day));
            CREATE TABLE IF NOT EXISTS suppressed (
                source TEXT, zone TEXT, day TEXT, PRIMARY KEY(source, zone, day));
        """)

    def prepare(self, source: str, zone: str, policy: str, today, retention: int) -> str:
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO sources VALUES (?, ?, ?)", (source, zone, today.isoformat())
            )
            stale = list(
                self.db.execute(
                    "SELECT zone, day FROM entries WHERE source=? AND policy<>?",
                    (source, policy),
                )
            )
            for old_zone, day in stale:
                self.delete(source, old_zone, day)
            cutoff = (today - timedelta(days=retention)).isoformat()
            expired = list(
                self.db.execute("SELECT source, zone, day FROM entries WHERE day<?", (cutoff,))
            )
            for old_source, old_zone, day in expired:
                self.delete(old_source, old_zone, day)
            for table in ("revisions", "jobs", "suppressed"):
                self.db.execute(
                    f"DELETE FROM {table} WHERE day<?",
                    (cutoff,),
                )
        return self.db.execute(
            "SELECT enabled_date FROM sources WHERE source=? AND zone=?", (source, zone)
        ).fetchone()[0]

    def get(self, source: str, zone: str, day: str, policy: str) -> dict | None:
        row = self.db.execute(
            "SELECT body FROM entries WHERE source=? AND zone=? AND day=? AND policy=?",
            (source, zone, day, policy),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, source: str, zone: str, day: str, policy: str, entry: dict) -> None:
        previous = self.get(source, zone, day, policy)
        entry["revision"] = previous.get("revision", 0) + 1 if previous else 1
        body = json.dumps(entry, ensure_ascii=False)
        with self.db:
            if previous:
                self.db.execute(
                    "INSERT OR REPLACE INTO revisions VALUES (?, ?, ?, ?, ?)",
                    (
                        source,
                        zone,
                        day,
                        previous["revision"],
                        json.dumps(previous, ensure_ascii=False),
                    ),
                )
                self.db.execute(
                    "DELETE FROM revisions WHERE source=? AND zone=? AND day=? AND revision<?",
                    (source, zone, day, max(1, entry["revision"] - 3)),
                )
            self.db.execute(
                "INSERT OR REPLACE INTO entries VALUES (?, ?, ?, ?, ?)",
                (source, zone, day, policy, body),
            )
            self.db.execute(
                "DELETE FROM jobs WHERE source=? AND zone=? AND day=?", (source, zone, day)
            )
            self.db.execute(
                "DELETE FROM suppressed WHERE source=? AND zone=? AND day=?", (source, zone, day)
            )
        self.export_path(source, zone, day).unlink(missing_ok=True)

    def failure(self, source: str, zone: str, day: str, now: float, error: str) -> None:
        attempts, _ = self.job(source, zone, day)
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO jobs VALUES (?, ?, ?, ?, ?, ?)",
                (source, zone, day, attempts + 1, now + 900 * (attempts + 1), error),
            )

    def job(self, source: str, zone: str, day: str) -> tuple[int, float]:
        row = self.db.execute(
            "SELECT attempts, retry_at FROM jobs WHERE source=? AND zone=? AND day=?",
            (source, zone, day),
        ).fetchone()
        return tuple(row) if row else (0, 0)

    def is_suppressed(self, source: str, zone: str, day: str) -> bool:
        return (
            self.db.execute(
                "SELECT 1 FROM suppressed WHERE source=? AND zone=? AND day=?", (source, zone, day)
            ).fetchone()
            is not None
        )

    def export_path(self, source: str, zone: str, day: str) -> Path:
        date.fromisoformat(day)
        target = self.directory / "exports" / source / digest(zone)[:16] / f"{day}.md"
        if not target.resolve().is_relative_to(self.directory):
            raise ValueError("日记导出路径超出数据目录")
        return target

    def export(self, source: str, zone: str, day: str, text: str) -> Path:
        target = self.export_path(source, zone, day)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        if not temporary.resolve().is_relative_to(self.directory):
            raise ValueError("无效导出临时路径")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(target)
        return target

    def delete(
        self, source: str, zone: str, day: str | None = None, *, suppress=False, today=""
    ) -> int:
        params = (source, zone) if day is None else (source, zone, day)
        where = "source=? AND zone=?" + (" AND day=?" if day is not None else "")
        days = [row[0] for row in self.db.execute(f"SELECT day FROM entries WHERE {where}", params)]
        with self.db:
            count = self.db.execute(f"DELETE FROM entries WHERE {where}", params).rowcount
            for table in ("revisions", "jobs"):
                self.db.execute(f"DELETE FROM {table} WHERE {where}", params)
            if suppress and day is not None:
                self.db.execute(
                    "INSERT OR IGNORE INTO suppressed VALUES (?, ?, ?)", (source, zone, day)
                )
            elif suppress:
                self.db.execute(
                    "UPDATE sources SET enabled_date=? WHERE source=? AND zone=?",
                    (today, source, zone),
                )
        for old_day in days:
            self.export_path(source, zone, old_day).unlink(missing_ok=True)
        return count

    def close(self) -> None:
        self.db.close()
