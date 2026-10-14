"""SQLite 실행 로그·피드백 저장소."""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

# (컬럼명, 타입) — CSV 내보내기도 이 순서를 따른다
COLUMNS: list[tuple[str, str]] = [
    ("run_id", "TEXT PRIMARY KEY"),
    ("created_at", "TEXT"),
    ("participant_code", "TEXT"),
    ("age_group", "TEXT"),
    ("participant_region", "TEXT"),
    ("sim", "TEXT"),
    ("scenario_id", "TEXT"),
    ("region", "TEXT"),
    ("is_elderly", "INTEGER"),
    ("reference", "TEXT"),
    ("raw_audio_path", "TEXT"),
    ("pre_audio_path", "TEXT"),
    ("duration_s", "REAL"),
    ("convert_ms", "INTEGER"),
    ("pre_loudnorm", "INTEGER"),
    ("pre_vad", "INTEGER"),
    ("pre_denoise", "INTEGER"),
    ("pre_tempo", "INTEGER"),
    ("stt_provider", "TEXT"),
    ("llm_provider", "TEXT"),
    ("a_text", "TEXT"),
    ("a_ms", "INTEGER"),
    ("a_cer", "REAL"),
    ("b_raw_text", "TEXT"),
    ("b_dialect_text", "TEXT"),
    ("b_standard_text", "TEXT"),
    ("b_ms", "INTEGER"),
    ("b_preprocess_ms", "INTEGER"),
    ("b_stt_ms", "INTEGER"),
    ("b_correct_ms", "INTEGER"),
    ("b_cer", "REAL"),
    ("b_cer_standard", "REAL"),
    ("counsel_a_reply", "TEXT"),
    ("counsel_b_reply", "TEXT"),
    ("target_doc_id", "TEXT"),
    ("search_a_query", "TEXT"),
    ("search_b_query", "TEXT"),
    ("search_a_results", "TEXT"),  # JSON
    ("search_b_results", "TEXT"),  # JSON
    ("hit_a", "INTEGER"),
    ("hit_b", "INTEGER"),
    ("fb_a_text", "INTEGER"),
    ("fb_b_text", "INTEGER"),
    ("fb_a_result", "INTEGER"),
    ("fb_b_result", "INTEGER"),
    ("fb_updated_at", "TEXT"),
]
COLUMN_NAMES = [c for c, _ in COLUMNS]

FEEDBACK_COLUMNS = {
    "a_text": "fb_a_text",
    "b_text": "fb_b_text",
    "a_result": "fb_a_result",
    "b_result": "fb_b_result",
}


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        cols = ",\n  ".join(f"{name} {typ}" for name, typ in COLUMNS)
        with self._connect() as conn:
            conn.execute(f"CREATE TABLE IF NOT EXISTS runs (\n  {cols}\n)")
            # 예전 DB 파일에 없는 컬럼이 있으면 추가 (스키마를 바꿔도 기존 로그 유지)
            existing = {r["name"] for r in conn.execute("PRAGMA table_info(runs)")}
            for name, typ in COLUMNS:
                if name not in existing:
                    conn.execute(f"ALTER TABLE runs ADD COLUMN {name} {typ.replace('PRIMARY KEY', '')}")

    def insert_run(self, data: dict[str, Any]) -> None:
        unknown = set(data) - set(COLUMN_NAMES)
        if unknown:
            raise ValueError(f"알 수 없는 컬럼: {unknown}")
        row = {"created_at": now_iso(), **data}
        names = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        with self._lock, self._connect() as conn:
            conn.execute(f"INSERT INTO runs ({names}) VALUES ({marks})", list(row.values()))

    def update_run(self, run_id: str, data: dict[str, Any]) -> bool:
        unknown = set(data) - set(COLUMN_NAMES)
        if unknown:
            raise ValueError(f"알 수 없는 컬럼: {unknown}")
        sets = ", ".join(f"{k} = ?" for k in data)
        with self._lock, self._connect() as conn:
            cur = conn.execute(f"UPDATE runs SET {sets} WHERE run_id = ?", [*data.values(), run_id])
            return cur.rowcount > 0

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def all_runs(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {', '.join(COLUMN_NAMES)} FROM runs ORDER BY created_at, rowid"
            ).fetchall()
        return [dict(r) for r in rows]

    def set_feedback(self, run_id: str, target: str, ok: bool) -> bool:
        column = FEEDBACK_COLUMNS[target]
        return self.update_run(run_id, {column: int(bool(ok)), "fb_updated_at": now_iso()})
