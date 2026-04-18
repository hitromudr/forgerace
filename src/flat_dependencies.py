"""Выравнивание цепочек зависимостей задач."""

from typing import List
from .tasks import Task

def flatten_deps(tasks: List[Task]) -> List[Task]:
    """Выравнивает цепочки зависимостей, чтобы максимизировать параллелизм.

    Если задача A зависит от B, а B зависит от C, то A зависит от C.
    Это позволяет запускать A и B параллельно, если C уже выполнена.

    Args:
        tasks: Список задач с зависимостями

    Returns:
        Новый список задач с выровненными зависимостями
    """
    # Создаем маппинг task_id -> Task
    task_map = {t.id: t for t in tasks}

    # Для каждой задачи вычисляем транзитивные зависимости
    for task in tasks:
        new_deps = set(task.deps)
        # Проходим по всем зависимостям и добавляем их зависимости
        for dep_id in task.deps:
            if dep_id in task_map:
                dep_task = task_map[dep_id]
                new_deps.update(dep_task.deps)
        # Обновляем зависимости задачи
        task.deps = sorted(new_deps)

    return tasks
"""Выравнивание цепочек зависимостей задач."""

from typing import List
from .tasks import Task

def flatten_deps(tasks: List[Task]) -> List[Task]:
    """Выравнивает цепочки зависимостей, чтобы максимизировать параллелизм.

    Если задача A зависит от B, а B зависит от C, то A зависит от C.
    Это позволяет запускать A и B параллельно, если C уже выполнена.

    Args:
        tasks: Список задач с зависимостями

    Returns:
        Новый список задач с выровненными зависимостями
    """
    # Создаем маппинг task_id -> Task
    task_map = {t.id: t for t in tasks}

    # Для каждой задачи вычисляем транзитивные зависимости
    for task in tasks:
        new_deps = set(task.deps)
        # Проходим по всем зависимостям и добавляем их зависимости
        for dep_id in task.deps:
            if dep_id in task_map:
                dep_task = task_map[dep_id]
                new_deps.update(dep_task.deps)
        # Обновляем зависимости задачи
        task.deps = sorted(new_deps)

    return tasks
