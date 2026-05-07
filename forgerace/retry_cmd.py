"""Умный перезапуск задач: анализ логов, классификация причин провала, выбор агента."""

import argparse
from typing import Optional, Tuple

from .config import cfg
from .tasks import parse_tasks, update_task_status
from .utils import log, run_cmd, C, R
from .worktree import remove_worktree
from .agents import is_agent_disabled

# Классификация причин провала
class FailureReason:
    TIMEOUT = "timeout"
    INACTIVITY = "inactivity"
    PROGRESS_STALL = "progress_stall"
    NO_EDIT_ABORT = "no_edit_abort"
    BUILD_ERROR = "build_error"
    TEST_FAILURE = "test_failure"
    MERGE_CONFLICT = "merge_conflict"
    QUOTA_ERROR = "quota_error"
    UNKNOWN = "unknown"

def analyze_failure(log_text: str) -> Tuple[FailureReason, str]:
    """Анализирует лог задачи и классифицирует причину провала.

    Args:
        log_text: Текст лога задачи

    Returns:
        Кортеж (причина, краткое описание)
    """
    log_text = log_text.lower()

    if "⏰ таймаут" in log_text or "timeout" in log_text:
        return FailureReason.TIMEOUT, "Таймаут выполнения"
    elif "⏰ нет tool_use" in log_text or "inactivity_timeout" in log_text:
        return FailureReason.INACTIVITY, "Агент завис (нет активности)"
    elif "⏰ diff не меняется" in log_text or "progress_timeout" in log_text:
        return FailureReason.PROGRESS_STALL, "Зацикливание (diff не меняется)"
    elif "no_edit_abort" in log_text:
        return FailureReason.NO_EDIT_ABORT, "Слишком много tool_calls без Edit/Write"
    elif "build failed" in log_text or "сборка провалена" in log_text:
        return FailureReason.BUILD_ERROR, "Ошибка сборки"
    elif "тесты провалены" in log_text or "test failure" in log_text:
        return FailureReason.TEST_FAILURE, "Провал тестов"
    elif "конфликт" in log_text or "merge conflict" in log_text:
        return FailureReason.MERGE_CONFLICT, "Конфликт мержа"
    elif any(kw in log_text for kw in ("quota exceeded", "rate limit", "api key", "429")):
        return FailureReason.QUOTA_ERROR, "Проблемы с квотой/авторизацией"
    else:
        return FailureReason.UNKNOWN, "Неизвестная причина"

def get_last_attempt_log(task_id: str) -> Optional[str]:
    """Получает лог последней попытки выполнения задачи.

    Args:
        task_id: ID задачи (например, TASK-036)

    Returns:
        Текст лога или None, если лог не найден
    """
    log_dir = cfg.log_dir
    if not log_dir.exists():
        return None

    # Ищем лог последней попытки
    log_pattern = f"{task_id.lower()}-*-attempt*.log"
    log_files = sorted(log_dir.glob(log_pattern), reverse=True)

    if not log_files:
        return None

    try:
        return log_files[0].read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None

def select_alternative_agent(current_agent: str) -> Optional[str]:
    """Выбирает альтернативного агента для перезапуска.

    Args:
        current_agent: Текущий агент, который провалил задачу

    Returns:
        Имя альтернативного агента или None, если нет доступных агентов
    """
    all_agents = cfg.cli_agent_names
    available_agents = [a for a in all_agents if not is_agent_disabled(a)]

    if not available_agents:
        return None

    # Убираем текущего агента из списка
    available_agents = [a for a in available_agents if a != current_agent]

    if not available_agents:
        return None

    # Выбираем первого доступного агента
    return available_agents[0]

def reset_task_state(task_id: str) -> bool:
    """Сбрасывает состояние задачи в 'open' и очищает worktree.

    Args:
        task_id: ID задачи

    Returns:
        True, если сброс прошел успешно, False в случае ошибки
    """
    try:
        # Обновляем статус задачи
        update_task_status(task_id, "open")

        # Ищем и удаляем worktree задачи
        agents_dir = cfg.agents_dir
        if agents_dir.exists():
            for agent_dir in agents_dir.glob("agent-*"):
                if agent_dir.is_dir():
                    # Проверяем, принадлежит ли worktree этой задаче
                    branch_pattern = f"task/{task_id.lower()}-*"
                    branch_result = run_cmd(
                        ["git", "branch", "--list", branch_pattern],
                        cwd=agent_dir, check=False
                    )
                    if branch_result.returncode == 0 and branch_result.stdout.strip():
                        remove_worktree(int(agent_dir.name.split("-")[-1]))
                        log.info(f"Worktree для задачи {task_id} удален")

        return True
    except Exception as e:
        log.error(f"Ошибка при сбросе состояния задачи {task_id}: {e}")
        return False

def retry_task(task_id: str) -> bool:
    """Перезапускает задачу с умным выбором агента.

    Args:
        task_id: ID задачи

    Returns:
        True, если перезапуск прошел успешно, False в случае ошибки
    """
    tasks = parse_tasks()
    task = next((t for t in tasks if t.id == task_id), None)

    if not task:
        log.error(f"Задача {task_id} не найдена")
        return False

    if task.status == "done":
        log.info(f"Задача {task_id} уже выполнена")
        return True

    # Получаем лог последней попытки
    log_text = get_last_attempt_log(task_id)
    if not log_text:
        log.warning(f"Лог для задачи {task_id} не найден")
        # Если нет лога, просто сбрасываем состояние
        return reset_task_state(task_id)

    # Анализируем причину провала
    reason, description = analyze_failure(log_text)
    log.info(f"Причина провала задачи {task_id}: {description} ({reason})")

    # Выбираем альтернативного агента
    current_agent = task.agent if task.agent and task.agent != "—" else None
    alternative_agent = None

    if current_agent:
        alternative_agent = select_alternative_agent(current_agent)
        if alternative_agent:
            log.info(f"Выбран альтернативный агент: {alternative_agent}")
        else:
            log.warning("Нет доступных альтернативных агентов")
    else:
        log.info("Задача не имеет назначенного агента, будет использован агент по умолчанию")

    # Сбрасываем состояние задачи
    if not reset_task_state(task_id):
        return False

    # Если выбран альтернативный агент, обновляем задачу
    if alternative_agent:
        update_task_status(task_id, "open", agent=alternative_agent)

    log.info(f"Задача {task_id} готова к перезапуску")
    return True

def retry_all_tasks() -> int:
    """Перезапускает все задачи со статусом blocked, review или in_progress.

    Returns:
        Количество задач, которые были перезапущены
    """
    tasks = parse_tasks()
    retryable_statuses = ["blocked", "review", "in_progress"]
    retryable_tasks = [t for t in tasks if any(t.status.startswith(s) for s in retryable_statuses)]

    if not retryable_tasks:
        log.info("Нет задач для перезапуска")
        return 0

    log.info(f"Найдено {len(retryable_tasks)} задач для перезапуска")
    success_count = 0

    for task in retryable_tasks:
        if retry_task(task.id):
            success_count += 1

    return success_count

def main():
    """CLI точка входа для retry_cmd."""
    parser = argparse.ArgumentParser(
        description="Умный перезапуск задач ForgeRace",
        usage="python forgerace/retry_cmd.py <command> [options]",
    )

    subparsers = parser.add_subparsers(dest="command", help="команды")

    # Команда для перезапуска конкретной задачи
    task_parser = subparsers.add_parser("task", help="Перезапустить конкретную задачу")
    task_parser.add_argument("task_id", help="ID задачи (например, TASK-036)")

    # Команда для перезапуска всех задач
    subparsers.add_parser("all", help="Перезапустить все задачи со статусом blocked/review/in_progress")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    # Инициализация конфига
    from .config import init_config
    init_config()

    if args.command == "task":
        if not retry_task(args.task_id):
            print(f"{C['red']}Не удалось перезапустить задачу {args.task_id}{R}")
        else:
            print(f"{C['green']}Задача {args.task_id} готова к перезапуску{R}")
    elif args.command == "all":
        count = retry_all_tasks()
        if count == 0:
            print(f"{C['yellow']}Нет задач для перезапуска{R}")
        else:
            print(f"{C['green']}Перезапущено {count} задач{R}")

if __name__ == "__main__":
    main()
