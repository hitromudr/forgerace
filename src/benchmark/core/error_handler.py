"""Обработчик ошибок для бенчмарка."""

import threading
from typing import Callable, Optional

class ErrorHandler:
    """Обработчик ошибок для бенчмарка."""

    def __init__(self, max_retries: int = 3):
        self.max_retries = max_retries
        self._lock = threading.Lock()
        self._retry_count = 0

    def handle_error(self, error: Exception, task_id: str, retry_callback: Optional[Callable] = None) -> bool:
        """Обрабатывает ошибку и решает, нужно ли повторять попытку.

        Args:
            error: Исключение, которое произошло
            task_id: Идентификатор задачи
            retry_callback: Функция для повторной попытки (если None - повтор не возможен)

        Returns:
            True если нужно повторить, False если максимальное количество попыток исчерпано
        """
        with self._lock:
            self._retry_count += 1
            if self._retry_count > self.max_retries:
                print(f"  ✗ {task_id}: Превышено максимальное количество попыток ({self.max_retries})")
                return False

            print(f"  ⚠ {task_id}: Ошибка - {str(error)} (попытка {self._retry_count}/{self.max_retries})")
            if retry_callback:
                retry_callback()
            return True

    def reset(self):
        """Сбрасывает счетчик попыток."""
        with self._lock:
            self._retry_count = 0

def create_error_handler(max_retries: int = 3) -> ErrorHandler:
    """Создает новый обработчик ошибок.

    Args:
        max_retries: Максимальное количество попыток

    Returns:
        Новый экземпляр ErrorHandler
    """
    return ErrorHandler(max_retries)
