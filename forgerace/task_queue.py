"""Очередь задач с приоритетами на основе heapq + ConcurrencyLimiter."""

import heapq
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Optional


class TaskQueue:
    """
    Очередь задач с приоритетами (max-heap) + ConcurrencyLimiter.

    Использует heapq с инверсией приоритета (-priority),
    чтобы задачи с большим priority извлекались первыми.
    При указании max_concurrent создаёт ConcurrencyLimiter для
    ограничения параллельного выполнения задач.
    """

    def __init__(self, max_concurrent: int = 3):
        self._heap: list[tuple[int, str]] = []
        self._counter = 0  # для стабильной сортировки при равных приоритетах
        self.limiter = ConcurrencyLimiter(max_concurrent)
    
    def push(self, task_id: str, priority: int) -> None:
        """
        Добавить задачу в очередь.
        
        Args:
            task_id: Идентификатор задачи.
            priority: Приоритет (чем больше число, тем выше приоритет).
        """
        # (-priority, counter, task_id) — counter для стабильности при равных приоритетах
        heapq.heappush(self._heap, (-priority, self._counter, task_id))
        self._counter += 1
    
    def pop(self) -> Optional[str]:
        """
        Извлечь задачу с наивысшим приоритетом.
        
        Returns:
            task_id задачи с наивысшим приоритетом или None, если очередь пуста.
        """
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
        return len(self._heap) == 0
    
    def __len__(self) -> int:
        """Возвращает количество задач в очереди."""
        return len(self._heap)
    
    def __bool__(self) -> bool:
        """Возвращает True, если очередь не пуста."""
        return bool(self._heap)


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
        self._futures: list[Future] = []

    def _remove_done(self, future: Future) -> None:
        """Callback: убирает завершённую future из списка."""
        try:
            self._futures.remove(future)
        except ValueError:
            pass

    def submit(self, fn: Callable, *args: Any, **kwargs: Any) -> Future:
        """Поставить задачу в очередь. Запуск — по мере освобождения слотов.

        Args:
            fn: Вызываемая функция.
            *args: Позиционные аргументы.
            **kwargs: Именованные аргументы.

        Returns:
            Future для отслеживания результата.
        """
        future = self._executor.submit(fn, *args, **kwargs)
        self._futures.append(future)
        future.add_done_callback(self._remove_done)
        return future

    @property
    def active_count(self) -> int:
        """Количество выполняющихся (не завершённых) задач."""
        return sum(1 for f in self._futures if f.running())

    @property
    def pending_count(self) -> int:
        """Количество задач, ожидающих запуска или выполняющихся."""
        return sum(1 for f in self._futures if not f.done())

    def shutdown(self, wait: bool = True, cancel_pending: bool = False) -> None:
        """Завершить работу пула.

        Args:
            wait: Ждать завершения текущих задач.
            cancel_pending: Отменить незапущенные задачи.
        """
        if cancel_pending:
            for f in self._futures:
                f.cancel()
        self._executor.shutdown(wait=wait)
        self._futures.clear()
