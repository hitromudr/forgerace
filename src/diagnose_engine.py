"""DiagnoseEngine для сбора и анализа состояния системы."""

import asyncio
import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Any, AsyncIterator, Optional

log = logging.getLogger(__name__)

@dataclass
class SystemSnapshot:
    """Снимок состояния системы."""
    timestamp: float
    tasks: list[Dict[str, Any]] = field(default_factory=list)
    agents: list[Dict[str, Any]] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)

class DiagnoseEngine:
    """Движок для диагностики и сбора состояния системы."""

    def __init__(self):
        self._snapshot: Optional[SystemSnapshot] = None
        self._lock = threading.Lock()
        self._subscribers: set[asyncio.Queue] = set()
        self._running = False
        self._update_task: Optional[asyncio.Task] = None

    def start(self):
        """Запускает движок."""
        if self._running:
            return

        self._running = True
        self._update_task = asyncio.create_task(self._update_loop())

    async def stop(self):
        """Останавливает движок."""
        if not self._running:
            return

        self._running = False
        if self._update_task:
            self._update_task.cancel()
            try:
                await self._update_task
            except asyncio.CancelledError:
                pass
            self._update_task = None

    def update_snapshot(self, snapshot: SystemSnapshot):
        """Обновляет текущий снимок состояния."""
        with self._lock:
            self._snapshot = snapshot

        # Уведомляем всех подписчиков
        for queue in self._subscribers:
            queue.put_nowait(snapshot)

    def get_snapshot(self) -> Optional[SystemSnapshot]:
        """Возвращает текущий снимок состояния."""
        with self._lock:
            return self._snapshot

    async def subscribe(self) -> AsyncIterator[SystemSnapshot]:
        """Подписывается на обновления снимков состояния."""
        queue = asyncio.Queue()
        self._subscribers.add(queue)

        try:
            while True:
                snapshot = await queue.get()
                yield snapshot
        finally:
            self._subscribers.discard(queue)

    async def _update_loop(self):
        """Фоновая задача для периодического обновления снимков."""
        while self._running:
            try:
                # В реальной реализации здесь будет сбор метрик
                # Для примера создадим пустой снимок
                snapshot = SystemSnapshot(
                    timestamp=asyncio.get_event_loop().time(),
                    tasks=[],
                    agents=[],
                    metrics={}
                )
                self.update_snapshot(snapshot)

                # Обновляем каждые 5 секунд
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("Ошибка в update_loop: %s", e)
                await asyncio.sleep(1)
