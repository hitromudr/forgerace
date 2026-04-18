"""Обработка ошибок и восстановление в бенчмарке."""

import logging
import traceback
from typing import Optional, Callable, Any

log = logging.getLogger(__name__)

class ErrorHandler:
    """Обработчик ошибок для бенчмарка."""

    def __init__(self, max_retries: int = 3):
        self.max_retries = max_retries
        self.retry_count = 0

    def handle_error(self, error: Exception, task_id: str, retry_callback: Optional[Callable] = None) -> bool:
        """
        Обрабатывает ошибку и пытается восстановиться.

        Args:
            error: Исключение, которое произошло.
            task_id: Идентификатор задачи.
            retry_callback: Функция для повторного выполнения задачи.

        Returns:
            True, если ошибка была обработана и задача может быть продолжена.
            False, если ошибка не может быть обработана.
        """
        self.retry_count += 1
        log.error(f"Ошибка при выполнении задачи {task_id}: {error}")
        log.debug(f"Трассировка: {traceback.format_exc()}")

        if self.retry_count > self.max_retries:
            log.error(f"Превышено максимальное количество попыток ({self.max_retries}) для задачи {task_id}")
            return False

        if retry_callback:
            try:
                log.info(f"Повторная попытка выполнения задачи {task_id} (попытка {self.retry_count}/{self.max_retries})")
                retry_callback()
                return True
            except Exception as e:
                log.error(f"Ошибка при повторной попытке выполнения задачи {task_id}: {e}")
                return False

        return False

    def reset(self):
        """Сбрасывает счетчик попыток."""
        self.retry_count = 0

def create_error_handler(max_retries: int = 3) -> ErrorHandler:
    """
    Создает обработчик ошибок.

    Args:
        max_retries: Максимальное количество попыток восстановления.

    Returns:
        Экземпляр ErrorHandler.
    """
    return ErrorHandler(max_retries)
