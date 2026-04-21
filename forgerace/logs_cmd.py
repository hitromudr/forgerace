"""Команда ./fr logs — просмотр логов задач."""
import os
import re
import time
from collections import deque
from pathlib import Path
from .config import cfg
from .utils import log, C, R

def list_logs():
    """Выводит таблицу файлов логов, отсортированную по mtime (новые сверху)."""
    log_dir = cfg.log_dir
    if not log_dir.exists():
        print(f" {C['dim']}Нет директории логов: {log_dir}{R}")
        return
    
    files = []
    for f in log_dir.glob("*.log"):
        if f.is_file():
            files.append(f)
    
    if not files:
        print(f" {C['dim']}Нет файлов логов{R}")
        return
    
    # Сортировка по mtime (новые сверху)
    files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    
    # Заголовок таблицы
    print(f"\n{C['bold']}{'Файл':<50} {'Размер':>10} {'Модифицирован':>20}{R}")
    print(f"{C['dim']}{'─' * 85}{R}")
    
    for f in files:
        stat = f.stat()
        size = stat.st_size
        mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))
        
        # Форматирование размера
        if size < 1024:
            size_str = f"{size} B"
        elif size < 1024 * 1024:
            size_str = f"{size / 1024:.1f} KB"
        else:
            size_str = f"{size / (1024 * 1024):.1f} MB"
        
        # Цвет имени файла в зависимости от типа
        fname = f.name
        if "attempt" in fname:
            # Попытка выполнения — белый
            fname_colored = f"{C['white']}{fname}{R}"
        elif "orchestrator" in fname:
            # Оркестратор — синий
            fname_colored = f"{C['blue']}{fname}{R}"
        elif "decomposed" in fname:
            # Декомпозиция — фиолетовый
            fname_colored = f"{C['purple']}{fname}{R}"
        else:
            fname_colored = fname
        
        print(f" {fname_colored:<50} {size_str:>10} {mtime:>20}")
    
    print()

def show_log(task_id: str, agent: str = None, tail: int = 50):
    """Показывает последние tail строк лога задачи.
    
    Args:
        task_id: ID задачи (например, "032" или "TASK-032")
        agent: имя агента для фильтрации (опционально)
        tail: количество строк с конца (по умолчанию 50)
    """
    # Нормализация task_id
    if not task_id.startswith("TASK-"):
        task_id = f"TASK-{task_id}"
    task_prefix = task_id.lower().replace("task-", "task-")
    
    log_dir = cfg.log_dir
    if not log_dir.exists():
        print(f" {C['red']}Нет директории логов: {log_dir}{R}")
        return
    
    # Поиск подходящих файлов
    candidates = []
    for f in log_dir.glob("*.log"):
        fname = f.name.lower()
        # Матчинг task_id
        if task_prefix not in fname:
            continue
        # Матчинг агента если указан
        if agent and agent.lower() not in fname:
            continue
        candidates.append(f)
    
    if not candidates:
        print(f" {C['yellow']}Нет логов для {task_id}{R}" + (f" (агент: {agent})" if agent else ""))
        return
    
    # Берём самый свежий файл
    candidates.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    target = candidates[0]
    
    # Читаем последние tail строк через deque
    lines = deque(maxlen=tail)
    try:
        with open(target, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                lines.append(line.rstrip('\n'))
    except OSError as e:
        print(f" {C['red']}Ошибка чтения {target}: {e}{R}")
        return
    
    # Вывод с цветовой подсветкой
    for line in lines:
        print(_colorize_log_line(line))

def follow_log(task_id: str = None, agent: str = None):
    """tail -f для логов задач.
    
    Args:
        task_id: ID задачи (опционально)
        agent: имя агента для фильтрации (опционально)
    """
    log_dir = cfg.log_dir
    if not log_dir.exists():
        print(f" {C['red']}Нет директории логов: {log_dir}{R}")
        return
    
    # Поиск файлов для слежения
    def find_files():
        files = []
        for f in log_dir.glob("*.log"):
            fname = f.name.lower()
            if task_id:
                task_prefix = task_id.lower() if task_id.startswith("TASK-") else f"task-{task_id}"
                if task_prefix not in fname:
                    continue
            if agent and agent.lower() not in fname:
                continue
            files.append(f)
        return sorted(files, key=lambda x: x.stat().st_mtime, reverse=True)
    
    files = find_files()
    if not files:
        print(f" {C['yellow']}Нет файлов для слежения{R}" + (f" (task={task_id}, agent={agent})" if task_id or agent else ""))
        return
    
    # Открываем файлы и читаем до конца
    handles = []
    for f in files:
        try:
            h = open(f, 'r', encoding='utf-8', errors='replace', buffering=1)
            # Перемещаем в конец
            h.seek(0, 2)
            handles.append((f, h))
        except OSError as e:
            log.warning(f"Не удалось открыть {f}: {e}")
    
    if not handles:
        print(f" {C['red']}Не удалось открыть файлы{R}")
        return
    
    print(f" {C['cyan']}Слежение за логами:{R} {', '.join(h[0].name for h in handles)}")
    print(f" {C['dim']}Ctrl+C для выхода{R}\n")
    
    try:
        while True:
            for f, h in handles:
                line = h.readline()
                if line:
                    print(_colorize_log_line(line.rstrip('\n')), flush=True)
            
            time.sleep(0.2)
    except KeyboardInterrupt:
        print(f"\n {C['dim']}Прервано{R}")
    finally:
        for f, h in handles:
            h.close()

def _colorize_log_line(line: str) -> str:
    """Применяет цветовую подсветку к строке лога."""
    if not line:
        return line
    
    line_lower = line.lower()
    
    # Ключевые слова для подсветки
    if "error" in line_lower or "✗" in line or "failed" in line_lower:
        return f"{C['red']}{line}{R}"
    elif "warn" in line_lower or "⚠" in line or "warning" in line_lower:
        return f"{C['yellow']}{line}{R}"
    elif "success" in line_lower or "✓" in line or "passed" in line_lower or "approved" in line_lower:
        return f"{C['green']}{line}{R}"
    elif "info" in line_lower or "▶" in line:
        return f"{C['cyan']}{line}{R}"
    else:
        return line

def handle_args(args):
    """Точка входа из cli.py — диспатч по аргументам.
    
    Ожидает namespace с полями:
    - subcmd: "list", "show", "follow"
    - task_id: ID задачи (для show/follow)
    - agent: имя агента (для show/follow)
    - tail: количество строк (для show)
    - follow: флаг для follow mode
    """
    subcmd = getattr(args, 'subcmd', None)
    
    if subcmd == 'list' or subcmd is None:
        list_logs()
    elif subcmd == 'show':
        task_id = getattr(args, 'task_id', None)
        if not task_id:
            print(f" {C['red']}Укажите task_id: ./fr logs show TASK-032{R}")
            return
        agent = getattr(args, 'agent', None)
        tail = getattr(args, 'tail', 50)
        show_log(task_id, agent=agent, tail=tail)
    elif subcmd == 'follow':
        task_id = getattr(args, 'task_id', None)
        agent = getattr(args, 'agent', None)
        follow_log(task_id=task_id, agent=agent)
    else:
        print(f" {C['red']}Неизвестная подкоманда: {subcmd}{R}")
        print(f" {C['dim']}Доступно: list, show, follow{R}")
