"""个人回测工作台的持久化与单任务后台队列。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping

from qsys.strategy.parameters import ParameterSpec, parameter_specs


@dataclass(frozen=True, slots=True)
class ManagedStrategy:
    id: str
    name: str
    draft: str
    updated_at: str
    parameters: tuple[ParameterSpec, ...]


@dataclass(frozen=True, slots=True)
class BacktestJob:
    id: str
    strategy_id: str
    strategy_name: str
    version_id: str
    status: str
    config: Mapping[str, object]
    progress: int
    total: int
    current_session: str
    error: str
    run_name: str
    created_at: str
    started_at: str
    finished_at: str

    @property
    def percent(self) -> int:
        return round(self.progress / self.total * 100) if self.total else 0


class WorkbenchStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self):
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init(self) -> None:
        with self._connect() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS strategies (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, draft TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS strategy_versions (
                    id TEXT PRIMARY KEY, strategy_id TEXT NOT NULL, source TEXT NOT NULL,
                    parameters_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    FOREIGN KEY(strategy_id) REFERENCES strategies(id)
                );
                CREATE TABLE IF NOT EXISTS backtest_jobs (
                    id TEXT PRIMARY KEY, strategy_id TEXT NOT NULL,
                    strategy_name TEXT NOT NULL, version_id TEXT NOT NULL,
                    status TEXT NOT NULL, config_json TEXT NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL DEFAULT 0,
                    current_session TEXT NOT NULL DEFAULT '', pid INTEGER,
                    error TEXT NOT NULL DEFAULT '', run_name TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL, started_at TEXT NOT NULL DEFAULT '',
                    finished_at TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS jobs_status_created
                    ON backtest_jobs(status, created_at);
            """)

    def recover_interrupted(self) -> None:
        """仅由队列宿主启动时调用；子进程打开数据库时不能误伤自己。"""
        with self._connect() as con:
            con.execute(
                "UPDATE backtest_jobs SET status='interrupted', finished_at=? "
                "WHERE status='running'", (_now(),)
            )

    def create_strategy(self, name: str, source: str) -> ManagedStrategy:
        name = name.strip()
        if not name:
            raise ValueError("策略名称不能为空")
        compile(source, "<strategy>", "exec")
        specs = parameter_specs(source)
        ident, now = uuid.uuid4().hex, _now()
        with self._connect() as con:
            con.execute("INSERT INTO strategies VALUES (?, ?, ?, ?)",
                        (ident, name, source, now))
        return ManagedStrategy(ident, name, source, now, specs)

    def save_strategy(self, ident: str, name: str, source: str) -> ManagedStrategy:
        compile(source, "<strategy>", "exec")
        specs = parameter_specs(source)
        name = name.strip()
        if not name:
            raise ValueError("策略名称不能为空")
        now = _now()
        with self._connect() as con:
            cur = con.execute(
                "UPDATE strategies SET name=?, draft=?, updated_at=? WHERE id=?",
                (name, source, now, ident),
            )
            if cur.rowcount != 1:
                raise LookupError("策略不存在")
        return ManagedStrategy(ident, name, source, now, specs)

    def strategies(self) -> list[ManagedStrategy]:
        with self._connect() as con:
            rows = con.execute("SELECT * FROM strategies ORDER BY updated_at DESC").fetchall()
        return [self._strategy(row) for row in rows]

    def strategy(self, ident: str) -> ManagedStrategy | None:
        with self._connect() as con:
            row = con.execute("SELECT * FROM strategies WHERE id=?", (ident,)).fetchone()
        return self._strategy(row) if row else None

    def import_file(self, path: Path) -> ManagedStrategy:
        path = Path(path).resolve()
        return self.create_strategy(path.stem, path.read_text(encoding="utf-8"))

    def versions(self, strategy_id: str) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT id, created_at FROM strategy_versions WHERE strategy_id=? "
                "ORDER BY created_at DESC", (strategy_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def enqueue(self, strategy_id: str, config: Mapping[str, object]) -> BacktestJob:
        strategy = self.strategy(strategy_id)
        if strategy is None:
            raise LookupError("策略不存在")
        digest = hashlib.sha256(f"{strategy.id}:".encode() + strategy.draft.encode()).hexdigest()
        version_id = digest[:16]
        specs_json = json.dumps([asdict(x) for x in strategy.parameters], ensure_ascii=False)
        job_id, now = uuid.uuid4().hex[:16], _now()
        with self._connect() as con:
            con.execute(
                "INSERT OR IGNORE INTO strategy_versions VALUES (?, ?, ?, ?, ?)",
                (version_id, strategy.id, strategy.draft, specs_json, now),
            )
            con.execute(
                "INSERT INTO backtest_jobs "
                "(id,strategy_id,strategy_name,version_id,status,config_json,created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (job_id, strategy.id, strategy.name, version_id, "queued",
                 json.dumps(dict(config), ensure_ascii=False), now),
            )
        return self.job(job_id)  # type: ignore[return-value]

    def claim_next(self) -> BacktestJob | None:
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            if con.execute(
                "SELECT 1 FROM backtest_jobs WHERE status='running' LIMIT 1"
            ).fetchone() is not None:
                return None
            row = con.execute(
                "SELECT id FROM backtest_jobs WHERE status='queued' "
                "ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            con.execute(
                "UPDATE backtest_jobs SET status='running', started_at=? "
                "WHERE id=? AND status='queued'", (_now(), row["id"]),
            )
        return self.job(row["id"])

    def jobs(self, limit: int = 100) -> list[BacktestJob]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM backtest_jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._job(r) for r in rows]

    def job(self, ident: str) -> BacktestJob | None:
        with self._connect() as con:
            row = con.execute("SELECT * FROM backtest_jobs WHERE id=?", (ident,)).fetchone()
        return self._job(row) if row else None

    def source_for_job(self, ident: str) -> str:
        with self._connect() as con:
            row = con.execute(
                "SELECT v.source FROM strategy_versions v JOIN backtest_jobs j "
                "ON j.version_id=v.id WHERE j.id=?", (ident,),
            ).fetchone()
        if row is None:
            raise LookupError("任务或策略版本不存在")
        return str(row["source"])

    def job_log(self, ident: str, *, limit: int = 100_000) -> str:
        path = self.path.parent / "jobs" / ident / "run.log"
        if not path.is_file():
            return ""
        return path.read_bytes()[-limit:].decode("utf-8", errors="replace")

    def set_pid(self, ident: str, pid: int) -> None:
        with self._connect() as con:
            con.execute("UPDATE backtest_jobs SET pid=? WHERE id=?", (pid, ident))

    def progress(self, ident: str, done: int, total: int, session: str) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE backtest_jobs SET progress=?,total=?,current_session=? "
                "WHERE id=? AND status='running'", (done, total, session, ident),
            )

    def finish(self, ident: str, status: str, *, error: str = "", run_name: str = "") -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE backtest_jobs SET status=?,error=?,run_name=?,finished_at=? "
                "WHERE id=? AND status='running'", (status, error, run_name, _now(), ident),
            )

    def cancel(self, ident: str) -> bool:
        with self._connect() as con:
            cur = con.execute(
                "UPDATE backtest_jobs SET status='cancelled',finished_at=? "
                "WHERE id=? AND status IN ('queued','running')", (_now(), ident),
            )
        return cur.rowcount == 1

    def _strategy(self, row) -> ManagedStrategy:
        return ManagedStrategy(row["id"], row["name"], row["draft"], row["updated_at"],
                               parameter_specs(row["draft"]))

    @staticmethod
    def _job(row) -> BacktestJob:
        return BacktestJob(
            row["id"], row["strategy_id"], row["strategy_name"], row["version_id"],
            row["status"], json.loads(row["config_json"]), row["progress"], row["total"],
            row["current_session"], row["error"], row["run_name"], row["created_at"],
            row["started_at"], row["finished_at"],
        )


class BacktestQueue:
    def __init__(self, store: WorkbenchStore, warehouse: Path, runs: Path,
                 *, timeout_seconds: int = 21_600) -> None:
        self.store, self.warehouse, self.runs = store, Path(warehouse), Path(runs)
        self.timeout = timeout_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.store.recover_interrupted()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="backtest-queue")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._process and self._process.poll() is None:
            self._process.terminate()

    def _loop(self) -> None:
        while not self._stop.is_set():
            job = self.store.claim_next()
            if job is None:
                self._stop.wait(0.5)
                continue
            self._run(job)

    def _run(self, job: BacktestJob) -> None:
        command = [sys.executable, "-m", "qsys.backtest.worker", "--db", str(self.store.path),
                   "--job", job.id, "--warehouse", str(self.warehouse),
                   "--runs", str(self.runs)]
        workspace = self.store.path.parent / "jobs" / job.id
        workspace.mkdir(parents=True, exist_ok=True)
        log_path = workspace / "run.log"
        log_stream = log_path.open("a", encoding="utf-8")
        process = subprocess.Popen(command, stdout=log_stream, stderr=subprocess.STDOUT,
                                   text=True)
        self._process = process
        self.store.set_pid(job.id, process.pid)
        started = time.monotonic()
        while process.poll() is None and not self._stop.wait(0.25):
            current = self.store.job(job.id)
            if current and current.status == "cancelled":
                process.terminate()
                try:
                    process.wait(5)
                except subprocess.TimeoutExpired:
                    process.kill()
            elif time.monotonic() - started > self.timeout:
                process.terminate()
                self.store.finish(job.id, "failed", error="回测超过最大运行时间")
        code = process.wait()
        log_stream.close()
        current = self.store.job(job.id)
        if current and current.status == "running":
            self.store.finish(job.id, "failed", error=f"回测子进程异常退出（{code}）")
        elif current and current.status == "succeeded":
            self._publish_log(current, log_path)
        self._process = None

    def _publish_log(self, job: BacktestJob, log_path: Path) -> None:
        run = self.runs / job.run_name
        if not run.is_dir():
            return
        (run / "run.log").write_bytes(log_path.read_bytes())
        archive = run / f"{run.name}.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(run.iterdir()):
                if path.is_file() and path != archive:
                    data = path.read_bytes()
                    if path.suffix.lower() in {".csv", ".json", ".txt", ".py", ".log"} \
                            and not data.startswith(b"\xef\xbb\xbf"):
                        data = b"\xef\xbb\xbf" + data
                    zf.writestr(path.name, data)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
