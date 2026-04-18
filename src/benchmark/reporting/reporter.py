"""Основной класс бенчмарка."""

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..reporting.reporter import BenchmarkReporter, BenchmarkReport

@dataclass
class BenchmarkCore:
    """Основной класс бенчмарка."""

    def __init__(self):
        self.reporter = BenchmarkReporter()

    def run_task(self, task_id: str, task_fn: Callable, *args: Any, **kwargs: Any) -> bool:
        """
        Выполняет задачу с обработкой ошибок.

        Args:
            task_id: Идентификатор задачи.
            task_fn: Функция для выполнения задачи.
            *args: Позиционные аргументы.
            **kwargs: Именованные аргументы.

        Returns:
            bool: True, если задача выполнена успешно, False в противном случае.
        """
        start_time = time.time()
        success = False
        error = None
        metrics = {}

        try:
            # Выполняем задачу
            result = task_fn(*args, **kwargs)
            success = True
            metrics = result if isinstance(result, dict) else {"result": result}
        except Exception as e:
            error = str(e)
            success = False
            metrics = {"error": error}
        finally:
            end_time = time.time()

            # Создаем отчет о выполнении задачи
            report = BenchmarkReport(
                task_id=task_id,
                agent_name="benchmark",
                start_time=start_time,
                end_time=end_time,
                success=success,
                metrics=metrics,
                error=error
            )

            # Добавляем отчет в репортер
            self.reporter.add_report(report)

        return success

    def retry_callback(self):
        """Колбэк для повторного выполнения задачи."""
        pass
"""Основной класс бенчмарка."""

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..reporting.reporter import BenchmarkReporter, BenchmarkReport

@dataclass
class BenchmarkCore:
    """Основной класс бенчмарка."""

    def __init__(self):
        self.reporter = BenchmarkReporter()

    def run_task(self, task_id: str, task_fn: Callable, *args: Any, **kwargs: Any) -> bool:
        """
        Выполняет задачу с обработкой ошибок.

        Args:
            task_id: Идентификатор задачи.
            task_fn: Функция для выполнения задачи.
            *args: Позиционные аргументы.
            **kwargs: Именованные аргументы.

        Returns:
            bool: True, если задача выполнена успешно, False в противном случае.
        """
        start_time = time.time()
        success = False
        error = None
        metrics = {}

        try:
            # Выполняем задачу
            result = task_fn(*args, **kwargs)
            success = True
            metrics = result if isinstance(result, dict) else {"result": result}
        except Exception as e:
            error = str(e)
            success = False
            metrics = {"error": error}
        finally:
            end_time = time.time()

            # Создаем отчет о выполнении задачи
            report = BenchmarkReport(
                task_id=task_id,
                agent_name="benchmark",
                start_time=start_time,
                end_time=end_time,
                success=success,
                metrics=metrics,
                error=error
            )

            # Добавляем отчет в репортер
            self.reporter.add_report(report)

        return success

    def retry_callback(self):
        """Колбэк для повторного выполнения задачи."""
        pass
"""Основной класс бенчмарка."""

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..reporting.reporter import BenchmarkReporter, BenchmarkReport

@dataclass
class BenchmarkCore:
    """Основной класс бенчмарка."""

    def __init__(self):
        self.reporter = BenchmarkReporter()

    def run_task(self, task_id: str, task_fn: Callable, *args: Any, **kwargs: Any) -> bool:
        """
        Выполняет задачу с обработкой ошибок.

        Args:
            task_id: Идентификатор задачи.
            task_fn: Функция для выполнения задачи.
            *args: Позиционные аргументы.
            **kwargs: Именованные аргументы.

        Returns:
            bool: True, если задача выполнена успешно, False в противном случае.
        """
        start_time = time.time()
        success = False
        error = None
        metrics = {}

        try:
            # Выполняем задачу
            result = task_fn(*args, **kwargs)
            success = True
            metrics = result if isinstance(result, dict) else {"result": result}
        except Exception as e:
            error = str(e)
            success = False
            metrics = {"error": error}
        finally:
            end_time = time.time()

            # Создаем отчет о выполнении задачи
            report = BenchmarkReport(
                task_id=task_id,
                agent_name="benchmark",
                start_time=start_time,
                end_time=end_time,
                success=success,
                metrics=metrics,
                error=error
            )

            # Добавляем отчет в репортер
            self.reporter.add_report(report)

        return success

    def retry_callback(self):
        """Колбэк для повторного выполнения задачи."""
        pass
