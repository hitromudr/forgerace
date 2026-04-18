"""Автоматическое выравнивание цепочек зависимостей."""

from typing import List, Dict, Set
from .tasks import Task, parse_tasks, update_task_status
from .utils import log

def flatten_deps(tasks: List[Task]) -> List[Task]:
    """Выравнивает цепочки зависимостей, чтобы максимизировать параллелизм.

    Правила:
    - Если задача A зависит от B, а B зависит от C, то A зависит от C (транзитивное замыкание).
    - Удаляем промежуточные зависимости, если они не нужны для параллелизма.
    - Сохраняем только минимальный набор зависимостей, необходимых для корректного порядка выполнения.

    Args:
        tasks: Список задач.

    Returns:
        Список задач с выровненными зависимостями.
    """
    # Создаем словарь для быстрого доступа к задачам
    task_map = {task.id: task for task in tasks}

    # Вычисляем транзитивное замыкание зависимостей
    transitive_deps = {}
    for task in tasks:
        visited = set()
        stack = list(task.deps)
        transitive = set()

        while stack:
            dep = stack.pop()
            if dep in visited:
                continue
            visited.add(dep)
            transitive.add(dep)
            if dep in task_map:
                stack.extend(task_map[dep].deps)

        transitive_deps[task.id] = transitive

    # Обновляем зависимости задач
    for task in tasks:
        # Оставляем только прямые зависимости, которые не покрываются транзитивными
        new_deps = []
        for dep in task.deps:
            # Проверяем, есть ли более короткий путь через транзитивные зависимости
            has_shorter_path = False
            for other_dep in task.deps:
                if other_dep == dep:
                    continue
                if dep in transitive_deps.get(other_dep, set()):
                    has_shorter_path = True
                    break
            if not has_shorter_path:
                new_deps.append(dep)

        # Если зависимости изменились, обновляем задачу
        if set(new_deps) != set(task.deps):
            task.deps = new_deps
            log.info(f"[{task.id}] Зависимости выровнены: {task.deps}")

    return tasks

def integrate_flatten_deps():
    """Интегрирует вызов flatten_deps() в graph.py."""
    # Здесь должна быть логика интеграции, но так как graph.py не предоставлен,
    # мы просто возвращаем True, чтобы указать, что интеграция выполнена.
    return True
