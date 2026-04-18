"""Persistence timeline для сохранения событий."""

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import cfg
from .utils import log

# Путь к базе данных timeline
_TIMELINE_DB = cfg.root_dir / "timeline.db"

# Инициализация базы данных
def _init_db():
    """Создаёт таблицы timeline если их нет."""
    with sqlite3.connect(_TIMELINE_DB) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                event_data TEXT,
                source TEXT,
                severity TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON events(timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_type ON events(event_type)")

# Инициализируем базу при первом импорте
_init_db()

@dataclass
class TimelineEvent:
    """Событие timeline."""
    event_type: str
    event_data: str
    source: str = ""
    severity: str = "info"

    def to_dict(self) -> dict:
        """Конвертирует событие в словарь."""
        return {
            "event_type": self.event_type,
            "event_data": self.event_data,
            "source": self.source,
            "severity": self.severity,
            "timestamp": datetime.now().isoformat(),
        }

class Timeline:
    """Persistence timeline для сохранения событий."""

    def __init__(self):
        self._lock = threading.Lock()

    def add_event(self, event: TimelineEvent) -> int:
        """Добавляет событие в timeline."""
        with self._lock:
            with sqlite3.connect(_TIMELINE_DB) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO events (timestamp, event_type, event_data, source, severity) VALUES (?, ?, ?, ?, ?)",
                    (
                        datetime.now().isoformat(),
                        event.event_type,
                        event.event_data,
                        event.source,
                        event.severity,
                    ),
                )
                return cursor.lastrowid

    def get_events(self, limit: int = 100, event_type: Optional[str] = None) -> list[dict]:
        """Возвращает события из timeline."""
        with self._lock:
            with sqlite3.connect(_TIMELINE_DB) as conn:
                if event_type:
                    cursor = conn.execute(
                        "SELECT * FROM events WHERE event_type = ? ORDER BY timestamp DESC LIMIT ?",
                        (event_type, limit),
                    )
                else:
                    cursor = conn.execute(
                        "SELECT * FROM events ORDER BY timestamp DESC LIMIT ?",
                        (limit,),
                    )
                return [
                    {
                        "id": row[0],
                        "timestamp": row[1],
                        "event_type": row[2],
                        "event_data": row[3],
                        "source": row[4],
                        "severity": row[5],
                    }
                    for row in cursor.fetchall()
                ]

    def clear_events(self, event_type: Optional[str] = None) -> int:
        """Очищает события из timeline."""
        with self._lock:
            with sqlite3.connect(_TIMELINE_DB) as conn:
                if event_type:
                    cursor = conn.execute("DELETE FROM events WHERE event_type = ?", (event_type,))
                else:
                    cursor = conn.execute("DELETE FROM events")
                return cursor.rowcount

# Глобальный экземпляр timeline
timeline = Timeline()

def log_event(event_type: str, event_data: str, source: str = "", severity: str = "info"):
    """Удобная функция для логирования событий."""
    timeline.add_event(TimelineEvent(event_type, event_data, source, severity))
