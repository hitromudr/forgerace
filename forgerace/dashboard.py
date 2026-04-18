"""TUI Dashboard для мониторинга прогресса задач и агентов."""

import time
from dataclasses import dataclass

from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.text import Text

from .task_queue import TaskQueue
from .utils import agent_color


@dataclass
class AgentStatus:
    """Статус активного агента."""
    task_id: str
    agent_type: str
    elapsed_sec: int
    files_changed: list[str]
    cost_usd: float


class Dashboard:
    """TUI Dashboard для отображения состояния задач и агентов."""

    def __init__(self):
        self.console = Console()
        self.live = Live(console=self.console, auto_refresh=False)
        self._start_time = time.time()

    def update(self, queue: TaskQueue, active_agents: dict[str, AgentStatus]):
        """Обновляет и отрисовывает таблицу с состоянием.

        Args:
            queue: TaskQueue с текущими задачами
            active_agents: словарь {tag: AgentStatus} для активных агентов
        """
        table = Table(title="ForgeRace Dashboard", show_header=True, header_style="bold magenta")
        table.add_column("Task", style="cyan", no_wrap=True)
        table.add_column("Agent", style="green")
        table.add_column("Time", style="yellow")
        table.add_column("Files", style="blue")
        table.add_column("Cost", style="red")

        for tag, status in active_agents.items():
            color = agent_color(status.agent_type)
            task_text = Text(status.task_id, style="cyan")
            agent_text = Text(f"@{status.agent_type}", style=color)
            time_text = Text(f"{status.elapsed_sec}s", style="yellow")
            files_text = Text(", ".join(status.files_changed[:3]), style="blue")
            cost_text = Text(f"${status.cost_usd:.2f}", style="red")
            table.add_row(task_text, agent_text, time_text, files_text, cost_text)

        # Добавляем информацию о задачах в очереди
        pending_tasks = queue.get_pending_tasks()
        if pending_tasks:
            table.add_section()
            table.add_column("Queue", style="dim")
            for priority, task_id in pending_tasks[:5]:  # Показываем первые 5 задач
                table.add_row(Text(task_id, style="dim"))

        self.live.update(table)
        self.live.refresh()

    def start(self):
        """Запускает Live-режим."""
        self.live.start()

    def stop(self):
        """Останавливает Live-режим."""
        self.live.stop()
