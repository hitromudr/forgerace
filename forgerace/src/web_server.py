import asyncio
import json
from datetime import datetime
from typing import Dict, List, Optional

class WebServer:
    """WebServer с поддержкой Server-Sent Events (SSE)."""

    def __init__(self, host: str = "localhost", port: int = 8080):
        self.host = host
        self.port = port
        self.server = None
        self.clients: List[asyncio.StreamWriter] = []
        self.events: List[Dict] = []

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Обрабатывает подключение клиента."""
        request_line = await reader.readline()
        if not request_line:
            writer.close()
            await writer.wait_closed()
            return

        request_line = request_line.decode().strip()
        method, path, _ = request_line.split()

        if method == "GET" and path == "/events":
            await self.handle_sse(reader, writer)
        else:
            writer.write(b"HTTP/1.1 404 Not Found\r\n\r\n")
            await writer.drain()
            writer.close()
            await writer.wait_closed()

    async def handle_sse(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Обрабатывает SSE подключение."""
        writer.write(b"HTTP/1.1 200 OK\r\n")
        writer.write(b"Content-Type: text/event-stream\r\n")
        writer.write(b"Cache-Control: no-cache\r\n")
        writer.write(b"Connection: keep-alive\r\n")
        writer.write(b"\r\n")
        await writer.drain()

        self.clients.append(writer)

        try:
            while True:
                if self.events:
                    event = self.events.pop(0)
                    event_data = f"data: {json.dumps(event)}\n\n"
                    writer.write(event_data.encode())
                    await writer.drain()
                else:
                    await asyncio.sleep(0.1)
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            if writer in self.clients:
                self.clients.remove(writer)
            writer.close()
            await writer.wait_closed()

    async def send_event(self, event: Dict):
        """Отправляет событие всем подключенным клиентам."""
        self.events.append(event)
        for client in self.clients:
            try:
                event_data = f"data: {json.dumps(event)}\n\n"
                client.write(event_data.encode())
                await client.drain()
            except (ConnectionResetError, BrokenPipeError):
                if client in self.clients:
                    self.clients.remove(client)

    async def start(self):
        """Запускает сервер."""
        self.server = await asyncio.start_server(
            self.handle_client,
            self.host,
            self.port
        )

        print(f"Server started at http://{self.host}:{self.port}")

        async with self.server:
            await self.server.serve_forever()

    async def stop(self):
        """Останавливает сервер."""
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        for client in self.clients:
            client.close()
            await client.wait_closed()
        self.clients.clear()
        self.events.clear()

def main():
    """Запускает WebServer."""
    server = WebServer()
    try:
        asyncio.run(server.start())
    except KeyboardInterrupt:
        print("Server stopped")

if __name__ == "__main__":
    main()
