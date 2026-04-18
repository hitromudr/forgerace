"""WebServer с поддержкой Server-Sent Events (SSE)."""

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any

from aiohttp import web
from aiohttp.web_request import Request
from aiohttp.web_response import Response, StreamResponse

from .diagnose_engine import DiagnoseEngine

log = logging.getLogger(__name__)

class WebServer:
    """HTTP-сервер с SSE для потоковой передачи событий от DiagnoseEngine."""

    def __init__(self, engine: DiagnoseEngine, host: str = "0.0.0.0", port: int = 8080):
        self.engine = engine
        self.host = host
        self.port = port
        self.app = web.Application()
        self._setup_routes()
        self._setup_sse()
        self._running = False
        self._server: Optional[web.TCPSite] = None

    def _setup_routes(self):
        """Настраивает маршруты сервера."""
        self.app.router.add_get("/events", self.handle_events)
        self.app.router.add_get("/health", self.handle_health)
        self.app.router.add_get("/snapshot", self.handle_snapshot)

    def _setup_sse(self):
        """Настраивает SSE-коннекты."""
        self._sse_connections: set[web.WebSocketResponse] = set()

    async def handle_events(self, request: Request) -> Response:
        """Обработчик SSE-коннекта для потоковой передачи событий."""
        response = StreamResponse()
        response.headers["Content-Type"] = "text/event-stream"
        response.headers["Cache-Control"] = "no-cache"
        response.headers["Connection"] = "keep-alive"
        response.headers["Access-Control-Allow-Origin"] = "*"

        await response.prepare(request)

        # Регистрируем коннект
        self._sse_connections.add(response)

        try:
            # Подписываемся на события от DiagnoseEngine
            async for event in self.engine.subscribe():
                try:
                    data = json.dumps(event)
                    await response.write(f"data: {data}\n\n".encode("utf-8"))
                except Exception as e:
                    log.error("Ошибка отправки SSE-события: %s", e)
                    break
        except asyncio.CancelledError:
            pass
        finally:
            # Убираем коннект при завершении
            self._sse_connections.discard(response)

        return response

    async def handle_health(self, request: Request) -> Response:
        """Обработчик проверки состояния сервера."""
        return web.json_response({"status": "ok"})

    async def handle_snapshot(self, request: Request) -> Response:
        """Обработчик получения текущего снимка состояния."""
        snapshot = self.engine.get_snapshot()
        return web.json_response(snapshot)

    async def start(self):
        """Запускает сервер."""
        if self._running:
            raise RuntimeError("Сервер уже запущен")

        runner = web.AppRunner(self.app)
        await runner.setup()
        self._server = web.TCPSite(runner, self.host, self.port)
        await self._server.start()

        self._running = True
        log.info("WebServer запущен на http://%s:%d", self.host, self.port)

    async def stop(self):
        """Останавливает сервер."""
        if not self._running:
            return

        if self._server:
            await self._server.stop()
            self._server = None

        # Закрываем все SSE-коннекты
        for conn in self._sse_connections:
            try:
                await conn.close()
            except Exception:
                pass
        self._sse_connections.clear()

        self._running = False
        log.info("WebServer остановлен")

    def is_running(self) -> bool:
        """Возвращает True, если сервер запущен."""
        return self._running

    def get_sse_connection_count(self) -> int:
        """Возвращает количество активных SSE-коннектов."""
        return len(self._sse_connections)

    async def broadcast_event(self, event: Dict[str, Any]):
        """Рассылает событие всем активным SSE-коннектам."""
        data = json.dumps(event)
        for conn in self._sse_connections:
            try:
                await conn.write(f"data: {data}\n\n".encode("utf-8"))
            except Exception as e:
                log.error("Ошибка рассылки SSE-события: %s", e)
                self._sse_connections.discard(conn)

def create_web_server(engine: DiagnoseEngine) -> WebServer:
    """Создаёт и возвращает экземпляр WebServer."""
    return WebServer(engine)
