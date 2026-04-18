 TASKS — forgerace

### TASK-001: Реализация команды `stats`
- **Статус**: blocked
- **Приоритет**: P1
- **Этап**: N
- **Зависимости**: —
- **Файлы (новые)**: forgerace/cli.py
- **Файлы (modify)**: —
- **Интеграция**: добавить вызов `_cmd_stats()` в `main()`
- **Описание**: реализовать функцию `_cmd_stats()` для вывода статистики задач
- **Запрещено**: —
- **Проверка**: make check
- **Критерий готовности**: команда `./fr stats` выводит статистику задач
- **Дискуссия**: feature-stats
- **Агент**: aider-devstral
- **Ветка**: task/task-001-realizatsiya-komandy-stats-aider-devstral
