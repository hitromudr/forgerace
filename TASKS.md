 TASKS — forgerace

### TASK-036: Реализовать команду ./fr task edit
- **Статус**: open
- **Приоритет**: P1
- **Этап**: N
- **Зависимости**: —
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/task_cmd.py
- **Интеграция**: НЕ трогать cli.py (защищённый файл). Только forgerace/task_cmd.py
- **Описание**: Добавить в forgerace/task_cmd.py функцию edit_task(task_id: str, **fields) → None. Функция: (1) читает cfg.tasks_file, (2) находит блок задачи по ID (regex: "### {task_id}:"), (3) заменяет значения указанных полей (Статус, Приоритет, Зависимости и др.) по regex "- \*\*{FieldName}\*\*: ...", (4) записывает через tasks_file_lock() + _atomic_write(). Валидация: priority in {P0,P1,P2,P3}, status in {open,done,blocked,skip}. Если задача не найдена — print ошибку. Если задача в конце файла (нет следующего "### TASK-") — обрабатывать корректно (читать до конца файла). Выводит результат с цветами из utils.C.
- **Запрещено**: readlines() для больших файлов, внешние зависимости, модификация cli.py
- **Проверка**: python3 -c "from forgerace.task_cmd import edit_task; print('OK')"
- **Критерий готовности**: edit_task("TASK-036", status="done") меняет статус в TASKS.md
- **Дискуссия**: task-add-cmd
- **Агент**: —
- **Ветка**: —
