"""Граф зависимостей задач."""

from typing import List, Dict, Set
from .tasks import Task
from .flat_dependencies import flatten_deps

class DependencyGraph:
    """Граф зависимостей задач."""

    def __init__(self, tasks: List[Task]):
        self.tasks = flatten_deps(tasks)  # Применяем выравнивание при инициализации
        self.graph = self._build_graph()

    def _build_graph(self) -> Dict[str, Set[str]]:
        """Строит граф зависимостей."""
        graph = {}
        for task in self.tasks:
            graph[task.id] = set(task.deps)
        return graph

    def get_flattened_tasks(self) -> List[Task]:
        """Возвращает задачи с выровненными зависимостями."""
        return self.tasks
