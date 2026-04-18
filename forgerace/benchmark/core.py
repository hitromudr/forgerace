"""Основной модуль бенчмарка."""

import logging
import time
from typing import Optional, Callable, Any

from .error_handler import ErrorHandler

log = logging.getLogger(__name__)

class BenchmarkCore:
    """Основной класс бенчмарка."""

    def __init__(self, max_retries: int = 3):
        self.error_handler = ErrorHandler(max_retries)

    def run_task(self, task_id: str, task_fn: Callable, *args: Any, **kwargs: Any) -> bool:
        """
        Выполняет задачу с обработкой ошибок.

        Args:
            task_id: Идентификатор задачи.
            task_fn: Функция для выполнения задачи.
            *args: Позиционные аргументы.
            **kwargs: Именованные аргументы.

        Returns:
            True, если задача выполнена успешно.
            False, если задача не выполнена.
        """
        def retry_callback():
            return task_fn(*args, **kwargs)

        try:
            result = task_fn(*args, **kwargs)
            return True
        except Exception as e:
            return self.error_handler.handle_error(e, task_id, retry_callback)

    def run_benchmark(self, tasks: list[tuple[str, Callable]], max_retries: int = 3) -> dict[str, bool]:
        """
        Выполняет бенчмарк для списка задач.

        Args:
            tasks: Список задач в формате (task_id, task_fn).
            max_retries: Максимальное количество попыток восстановления.

        Returns:
            Словарь с результатами выполнения задач.
        """
        results = {}
        for task_id, task_fn in tasks:
            self.error_handler.reset()
            results[task_id] = self.run_task(task_id, task_fn)
        return results
