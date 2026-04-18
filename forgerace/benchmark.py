"""Модуль для сбора и хранения метрик производительности агентов."""

import json
import os
import statistics
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import cfg
from .filelock import acquire_file_lock


@dataclass
class BenchmarkRecord:
    """Запись метрик выполнения одной задачи одним агентом."""
    task_id: str
    agent: str
    duration_sec: float
    total_cost_usd: float
    review_rounds: int
    lines_changed: int
    success: bool = True


class BenchmarkStore:
    """Хранилище метрик с потокобезопасной записью в JSON."""

    def __init__(self, path: Optional[Path] = None):
        """Инициализирует хранилище и создаёт директорию .agents/."""
        self._lock = threading.RLock()
        self.path = path or cfg.root_dir / ".agents" / "benchmark.json"
        self._buffer: List[BenchmarkRecord] = []
        # Создаём директорию .agents/ если её нет
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read_file(self) -> List[BenchmarkRecord]:
        """Читает и парсит данные из файла (без блокировки)."""
        if not self.path.exists():
            return []
        
        records = []
        try:
            content = self.path.read_text(encoding="utf-8")
            if not content.strip():
                return []
            
            data = json.loads(content)
            if not isinstance(data, list):
                return []
            
            # Получаем список полей BenchmarkRecord один раз
            valid_keys = BenchmarkRecord.__dataclass_fields__.keys()
            
            for r in data:
                try:
                    if not isinstance(r, dict):
                        continue
                    
                    # Обработка записей без поля success для обратной совместимости
                    if "success" not in r:
                        r["success"] = True
                    
                    # Фильтрация лишних ключей, которых нет в BenchmarkRecord
                    filtered_r = {k: v for k, v in r.items() if k in valid_keys}
                    records.append(BenchmarkRecord(**filtered_r))
                except (TypeError, ValueError, KeyError):
                    # Пропускаем одну битую запись, продолжаем остальные
                    continue
        except (json.JSONDecodeError, OSError):
            pass
            
        return records

    def save(self, record: Optional[BenchmarkRecord] = None) -> None:
        """
        Добавляет запись в хранилище (потокобезопасно).
        Если record не указан, сохраняет весь буфер в файл.
        Использует атомарную запись через временный файл и os.replace.
        """
        with self._lock:
            # Читаем существующие данные через asdict для сериализации
            # Нам нужны словари для записи в JSON
            raw_data = []
            if self.path.exists():
                try:
                    content = self.path.read_text(encoding="utf-8")
                    if content.strip():
                        raw_data = json.loads(content)
                        if not isinstance(raw_data, list):
                            raw_data = []
                except (json.JSONDecodeError, OSError):
                    raw_data = []

            if record:
                # Добавляем одну запись
                raw_data.append(asdict(record))
            else:
                # Сохраняем весь буфер
                for r in self._buffer:
                    raw_data.append(asdict(r))
                self._buffer = []

            # Атомарная запись: сначала во временный файл, затем os.replace
            # Временный файл создаётся в той же директории, чтобы os.replace был атомарным
            temp_path = self.path.with_suffix(".tmp")
            try:
                temp_path.write_text(json.dumps(raw_data, indent=2, ensure_ascii=False), encoding="utf-8")
                os.replace(temp_path, self.path)
            except Exception:
                # Если что-то пошло не так, удаляем временный файл чтобы не засорять диск
                try:
                    temp_path.unlink()
                except OSError:
                    pass
                raise

    def add(self, record: BenchmarkRecord) -> None:
        """
        Добавляет запись в буфер в памяти (потокобезопасно).
        Данные сохраняются на диск только при вызове save().
        """
        with self._lock:
            self._buffer.append(record)

    def get_all(self) -> List[BenchmarkRecord]:
        """Возвращает все записи из хранилища (из файла + из буфера)."""
        with acquire_file_lock(self.path, mode='shared'):
            file_records = self._read_file()
            # Объединяем с буфером (копия списка для безопасности)
            return file_records + list(self._buffer)

    def aggregate(self, agent: Optional[str] = None) -> Dict[str, Dict[str, float]]:
        """
        Вычисляет агрегированные метрики (среднее и медиана) для всех записей
        или для конкретного агента.
        """
        records = self.get_all()
        if agent:
            records = [r for r in records if r.agent == agent]

        if not records:
            return {}

        fields = ["duration_sec", "total_cost_usd", "review_rounds", "lines_changed"]
        stats = {}
        for field in fields:
            values = [getattr(r, field) for r in records]
            stats[field] = {
                "mean": statistics.mean(values) if values else 0.0,
                "median": statistics.median(values) if values else 0.0
            }

        # Добавляем success_rate
        successes = [1 if r.success else 0 for r in records]
        stats["success_rate"] = {
            "mean": statistics.mean(successes) if successes else 0.0,
            "median": statistics.median(successes) if successes else 0.0
        }

        return stats

    def as_json(self) -> str:
        """Возвращает агрегированные метрики в формате JSON для всех агентов."""
        records = self.get_all()
        agents = sorted(list(set(r.agent for r in records)))
        result = {aid: self.aggregate(agent=aid) for aid in agents}
        return json.dumps(result, indent=2, ensure_ascii=False)

    def as_table(self) -> str:
        """Возвращает агрегированные метрики в виде текстовой таблицы."""
        records = self.get_all()
        agents = sorted(list(set(r.agent for r in records)))
        if not agents:
            return "No benchmark data available."

        header = f"{'Agent':<20} | {'Count':<5} | {'Succ%':<6} | {'Time (s)':<8} | {'Cost ($)':<8} | {'Lines':<6}"
        separator = "-" * len(header)
        lines = [header, separator]

        for aid in agents:
            s = self.aggregate(agent=aid)
            count = len([r for r in records if r.agent == aid])
            lines.append(
                f"{aid:<20} | "
                f"{count:<5} | "
                f"{s['success_rate']['mean']*100:>5.1f}% | "
                f"{s['duration_sec']['mean']:>8.1f} | "
                f"{s['total_cost_usd']['mean']:>8.4f} | "
                f"{s['lines_changed']['mean']:>6.0f}"
            )
        
        return "\n".join(lines)
