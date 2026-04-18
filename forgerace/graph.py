"""Граф зависимостей задач."""

from typing import List, Dict, Set
from .tasks import Task

class DependencyGraph:
    """Граф зависимостей задач."""

    def __init__(self, tasks: List[Task]):
        self.tasks = tasks
        self.graph = self._build_graph()

    def _build_graph(self) -> Dict[str, Set[str]]:
        """Строит граф зависимостей."""
        graph = {}
        for task in self.tasks:
            graph[task.id] = set(task.deps)
        return graph

    def flatten_deps(self) -> List[Task]:
        """Выравнивает цепочки зависимостей."""
        from .flat_dependencies import flatten_deps
        return flatten_deps(self.tasks)

    def get_flattened_tasks(self) -> List[Task]:
        """Возвращает задачи с выровненными зависимостями."""
        return self.flatten_deps()
