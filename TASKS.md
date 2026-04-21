 TASKS — forgerace

### TASK-037: Реализовать умный перезапуск задачи
- **Статус**: open
- **Описание**: Создать функции для анализа последнего лога задачи, классификации причины провала, выбора другого агента если текущий зацикливался, сброса worktree и статуса задачи, а также запуска задачи с выбранным агентом. Использовать существующие модули `forgerace.tasks`, `forgerace.config`, `forgerace.utils`.
- **Файлы (новые)**: forgerace/retry_cmd.py
- **Запрещено**: Изменение файла cli.py
- **Проверка**: python forgerace/retry_cmd.py --task TASK-036, python forgerace/retry_cmd.py --all
- **Дискуссия**: retry-cmd
