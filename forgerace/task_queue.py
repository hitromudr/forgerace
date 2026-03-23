"""Очередь задач с приоритетами на основе heapq."""

import heapq
from typing import Optional


class TaskQueue:
    """
    Очередь задач с приоритетами (max-heap).
    
    Использует heapq с инверсией приоритета (-priority),
    чтобы задачи с большим priority извлекались первыми.
    """
    
    def __init__(self):
        self._heap: list[tuple[int, str]] = []
        self._counter = 0  # для стабильной сортировки при равных приоритетах
    
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
