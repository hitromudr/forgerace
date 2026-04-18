"""Очередь задач с приоритетами на основе heapq + ConcurrencyLimiter."""

import heapq
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Optional, List, Dict


class TaskQueue:
    """
    Очередь задач с приоритетами (max-heap) + ConcurrencyLimiter.

    Использует heapq с инверсией приоритета (-priority),
    чтобы задачи с большим priority извлекались первыми.
    При указании max_concurrent создаёт ConcurrencyLimiter для
    ограничения параллельного выполнения задач.

    Приоритетная очередь и limiter — разделённые ответственности:
    очередь определяет порядок извлечения (pop), limiter ограничивает
    параллельность (submit). Caller делает pop() → submit(fn).

    Dashboard API (методы и поля, используемые TUI):
      - get_pending() -> list[dict]      # Ожидающие задачи с приоритетом и позицией.
      - get_pending_tasks() -> list[tuple[int, str]]  # Упрощённый формат (priority, task_id).
      - get_active() -> list[str]       # ID выполняющихся задач.
      - get_stats() -> dict[str, int]   # Статистика: pending, active, max.
      - push(task_id: str, priority: int) -> None  # Добавить задачу.
      - pop() -> Optional[str]          # Извлечь задачу с наивысшим приоритетом.
      - empty() -> bool                 # Проверить, пуста ли очередь.
      - __len__() -> int                # Количество задач в очереди.
      - __bool__() -> bool              # True, если очередь не пуста.
      - submit(fn, task_id, *args, **kwargs) -> Future  # Запустить задачу.
      - shutdown(wait=True, cancel_pending=False) -> None  # Завершить работу.
    """

    def __init__(self, max_concurrent: int = 3):
        self._heap: list[tuple[int, int, str]] = []
        self._counter = 0  # для стабильной сортировки при равных приоритетах
        self._heap_lock = threading.Lock()  # для потокобезопасного доступа
        self.limiter = ConcurrencyLimiter(max_concurrent)
    
    def push(self, task_id: str, priority: int) -> None:
        """
        Добавить задачу в очередь.

        Args:
            task_id: Идентификатор задачи.
            priority: Приоритет (чем больше число, тем выше приоритет).
        """
        # (-priority, counter, task_id) — counter для стабильности при равных приоритетах
        with self._heap_lock:
            heapq.heappush(self._heap, (-priority, self._counter, task_id))
            self._counter += 1
    
    def pop(self) -> Optional[str]:
        """
        Извлечь задачу с наивысшим приоритетом.

        Returns:
            task_id задачи с наивысшим приоритетом или None, если очередь пуста.
        """
        with self._heap_lock:
            if not self._heap:
                return None
            _, _, task_id = heapq.heappop(self._heap)
            return task_id
    
    def empty(self) -> bool:
        """
        Проверить, пуста ли очередь.

        Returns:
            True, если очередь пуста.
        """
        with self._heap_lock:
            return len(self._heap) == 0
    
    def __len__(self) -> int:
        """Возвращает количество задач в очереди."""
        with self._heap_lock:
            return len(self._heap)
    
    def __bool__(self) -> bool:
        """Возвращает True, если очередь не пуста."""
        with self._heap_lock:
            return bool(self._heap)

    def submit(self, fn: Callable, task_id: str, *args: Any, **kwargs: Any) -> Future:
        """Proxy к limiter.submit() для удобства.

        Args:
            fn: Вызываемая функция.
            task_id: Идентификатор задачи.
            *args: Позиционные аргументы.
            **kwargs: Именованные аргументы.

        Returns:
            Future для отслеживания результата.
        """
        return self.limiter.submit(fn, task_id=task_id, *args, **kwargs)

    def shutdown(self, wait: bool = True, cancel_pending: bool = False) -> None:
        """Proxy к limiter.shutdown()."""
        self.limiter.shutdown(wait=wait, cancel_pending=cancel_pending)

    def get_pending(self) -> list[dict[str, Any]]:
        """
        Возвращает список ожидающих задач с их приоритетами и позициями.
        Не меняет состояние очереди (использует sorted() для создания копии).

        Returns:
            list[dict[str, Any]]: Список словарей с полями task_id, priority, position.
                                 Сортировка производится по приоритету (от высшего к низшему),
                                 затем по порядку добавления (FIFO).
        """
        with self._heap_lock:
            # self._heap содержит кортежи (-priority, counter, task_id).
            # sorted() создает новый список, не меняя оригинальную кучу.
            # Сортировка по (-priority, counter) дает:
            # 1. Первыми идут задачи с наименьшим -priority (т.е. наибольшим priority).
            # 2. При равных приоритетах первыми идут задачи с наименьшим counter (FIFO).
            sorted_items = sorted(self._heap)

            return [
                {
                    "task_id": task_id,
                    "priority": -neg_priority,
                    "position": idx + 1
                }
                for idx, (neg_priority, _, task_id) in enumerate(sorted_items)
            ]

    def get_pending_tasks(self) -> list[tuple[int, str]]:
        """
        Возвращает список (priority, task_id) для обратной совместимости (например, с Dashboard).
        """
        with self._heap_lock:
            # Используем ту же логику сортировки, что и в get_pending.
            return [(-neg_priority, task_id) for neg_priority, _, task_id in sorted(self._heap)]

    def get_active(self) -> list[str]:
        """
        Возвращает список ID активных (выполняющихся) задач.
        Потокобезопасно.
        """
        return self.limiter.get_active()

    def get_stats(self) -> dict[str, int]:
        """
        Возвращает сводную статистику очереди.
        {
            "pending": int,
            "active": int,
            "max": int,
            "completed": int
        }
        """
        return {
            "pending": len(self),
            "active": self.limiter.active_count,
            "max": self.limiter.max_concurrent,
            "completed": self.limiter.completed_count
        }


class ConcurrencyLimiter:
    """Ограничитель параллельности на основе ThreadPoolExecutor.

    Не более max_concurrent задач выполняются одновременно.
    submit() ставит задачу в пул, запуск — по мере освобождения слотов.
    """

    def __init__(self, max_concurrent: int = 3):
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")
        self.max_concurrent = max_concurrent
        self._executor = ThreadPoolExecutor(max_workers=max_concurrent)
        self._lock = threading.Lock()
        self._futures: list[Future] = []
        self._active: dict[str, Future] = {}  # task_id -> Future
        self.completed_count = 0

    def _remove_done(self, future: Future) -> None:
        """Callback: убирает завершённую future из списка и словаря активных задач."""
        with self._lock:
            try:
                self._futures.remove(future)
            except ValueError:
                pass
            # Удаляем future из активных задач
            for task_id, f in list(self._active.items()):
                if f == future:
                    del self._active[task_id]
                    break
            self.completed_count += 1

    def submit(self, fn: Callable, task_id: str, *args: Any, **kwargs: Any) -> Future:
        """Поставить задачу в очередь. Запуск — по мере освобождения слотов.

        Args:
            fn: Вызываемая функция.
            task_id: Идентификатор задачи.
            *args: Позиционные аргументы.
            **kwargs: Именованные аргументы.

        Returns:
            Future для отслеживания результата.
        """
        future = self._executor.submit(fn, *args, **kwargs)
        with self._lock:
            self._futures.append(future)
            self._active[task_id] = future
        future.add_done_callback(self._remove_done)
        return future

    @property
    def active_count(self) -> int:
        """Количество выполняющихся (не завершённых) задач."""
        with self._lock:
            return sum(1 for f in self._futures if f.running())

    @property
    def pending_count(self) -> int:
        """Количество задач, ожидающих запуска или выполняющихся."""
        with self._lock:
            return sum(1 for f in self._futures if not f.done())

    def get_active(self) -> list[str]:
        """
        Возвращает список ID активных задач.

        Returns:
            Список идентификаторов активных задач.
        """
        with self._lock:
            return list(self._active.keys())

    def shutdown(self, wait: bool = True, cancel_pending: bool = False) -> None:
        """Завершить работу пула.

        Args:
            wait: Ждать завершения текущих задач.
            cancel_pending: Отменить незапущенные задачи.
        """
        with self._lock:
            if cancel_pending:
                for f in self._futures:
                    f.cancel()
        self._executor.shutdown(wait=wait)
        with self._lock:
            self._futures.clear()
            self._active.clear()
