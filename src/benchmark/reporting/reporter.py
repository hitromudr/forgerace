"""Система отчётности и визуализации для бенчмарков."""

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from ...config import cfg

@dataclass
class BenchmarkReport:
    """Отчёт о выполнении бенчмарка."""
    task_id: str
    agent_name: str
    start_time: float
    end_time: float
    success: bool
    metrics: Dict[str, float]
    error: Optional[str] = None

class BenchmarkReporter:
    """Генератор отчётов и визуализаций для бенчмарков."""

    def __init__(self, output_dir: Path = None):
        self.output_dir = output_dir or cfg.root_dir / "benchmark_reports"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.reports: List[BenchmarkReport] = []

    def add_report(self, report: BenchmarkReport):
        """Добавляет отчёт о выполнении бенчмарка."""
        self.reports.append(report)

    def generate_text_report(self) -> str:
        """Генерирует текстовый отчёт о выполнении бенчмарков."""
        if not self.reports:
            return "Нет данных для отчёта."

        report_lines = ["Бенчмарк Отчёт", "=" * 50]

        for report in self.reports:
            duration = report.end_time - report.start_time
            status = "✓ Успешно" if report.success else f"✗ Ошибка: {report.error}"

            report_lines.extend([
                f"Задача: {report.task_id}",
                f"Агент: {report.agent_name}",
                f"Время: {duration:.2f}с",
                f"Статус: {status}",
                "Метрики:"
            ])

            for metric_name, metric_value in report.metrics.items():
                report_lines.append(f"  {metric_name}: {metric_value}")

            report_lines.append("-" * 30)

        return "\n".join(report_lines)

    def generate_json_report(self) -> str:
        """Генерирует JSON отчёт о выполнении бенчмарков."""
        report_data = {
            "timestamp": time.time(),
            "total_tasks": len(self.reports),
            "successful_tasks": sum(1 for r in self.reports if r.success),
            "reports": [
                {
                    "task_id": r.task_id,
                    "agent_name": r.agent_name,
                    "start_time": r.start_time,
                    "end_time": r.end_time,
                    "duration": r.end_time - r.start_time,
                    "success": r.success,
                    "error": r.error,
                    "metrics": r.metrics
                }
                for r in self.reports
            ]
        }
        return json.dumps(report_data, indent=2, ensure_ascii=False)

    def save_reports(self):
        """Сохраняет отчёты в файлы."""
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        text_report = self.generate_text_report()
        json_report = self.generate_json_report()

        text_file = self.output_dir / f"benchmark_report_{timestamp}.txt"
        json_file = self.output_dir / f"benchmark_report_{timestamp}.json"

        text_file.write_text(text_report, encoding="utf-8")
        json_file.write_text(json_report, encoding="utf-8")

        return text_file, json_file

    def generate_visualization(self) -> str:
        """Генерирует ASCII-визуализацию результатов бенчмарков."""
        if not self.reports:
            return "Нет данных для визуализации."

        # Собираем данные для визуализации
        agents = list({r.agent_name for r in self.reports})
        tasks = list({r.task_id for r in self.reports})

        # Создаем ASCII-таблицу
        header = ["Агент/Задача"] + tasks
        table = [header]

        for agent in agents:
            row = [agent]
            for task in tasks:
                # Ищем отчёт для этой комбинации агент/задача
                report = next((r for r in self.reports
                              if r.agent_name == agent and r.task_id == task), None)
                if report:
                    status = "✓" if report.success else "✗"
                    row.append(status)
                else:
                    row.append(" ")
            table.append(row)

        # Форматируем таблицу
        col_widths = [max(len(str(row[i])) for row in table) for i in range(len(table[0]))]

        formatted_lines = []
        for row in table:
            formatted_row = " | ".join(
                str(cell).ljust(col_widths[i])
                for i, cell in enumerate(row)
            )
            formatted_lines.append(formatted_row)

        # Добавляем разделители
        separator = "-+-".join("-" * width for width in col_widths)
        formatted_lines.insert(1, separator)

        return "\n".join(formatted_lines)

    def generate_summary(self) -> str:
        """Генерирует краткое резюме результатов бенчмарков."""
        if not self.reports:
            return "Нет данных для резюме."

        total = len(self.reports)
        successful = sum(1 for r in self.reports if r.success)
        failed = total - successful

        avg_duration = sum(r.end_time - r.start_time for r in self.reports) / total

        return (
            f"Резюме бенчмарков:\n"
            f"Всего задач: {total}\n"
            f"Успешно: {successful} ({successful*100/total:.1f}%)\n"
            f"Ошибки: {failed} ({failed*100/total:.1f}%)\n"
            f"Среднее время: {avg_duration:.2f}с"
        )
````

src/benchmark/core.py
````python
<<<<<<< SEARCH
│class BenchmarkCore:
│    """Основной класс бенчмарка."""
│
⋮
│    def run_task(self, task_id: str, task_fn: Callable, *args: Any, **kwargs: Any) -> bool:
│        """
│        Выполняет задачу с обработкой ошибок.
│
│        Args:
│            task_id: Идентификатор задачи.
│            task_fn: Функция для выполнения задачи.
│            *args: Позиционные аргументы.
│            **kwargs: Именованные аргументы.
│
│        Returns:
⋮
│        def retry_callback():
⋮
