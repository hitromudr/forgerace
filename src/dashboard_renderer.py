"""DashboardRenderer — формирование HTML/JSON для эндпоинтов."""

from typing import Any, Dict, List
from datetime import datetime
import json

class DashboardRenderer:
    """Формирует HTML/JSON для эндпоинтов без использования шаблонизаторов."""

    def __init__(self, diagnose_engine=None):
        """Инициализирует рендерер.

        Args:
            diagnose_engine: Экземпляр DiagnoseEngine для получения данных.
        """
        self.diagnose_engine = diagnose_engine

    def render_html(self, data: Dict[str, Any]) -> str:
        """Формирует HTML-страницу с данными.

        Args:
            data: Словарь с данными для отображения.

        Returns:
            HTML-строка.
        """
        # Минималистичный HTML без шаблонизаторов
        html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dashboard</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; }}
        h1 {{ color: #333; }}
        .card {{ border: 1px solid #ddd; border-radius: 4px; padding: 15px; margin: 10px 0; }}
        .metric {{ font-weight: bold; }}
    </style>
</head>
<body>
    <h1>Dashboard</h1>
    <div class="card">
        <h2>Metrics</h2>
        <div class="metric">Timestamp: {datetime.now().isoformat()}</div>
        <div class="metric">Status: {data.get('status', 'unknown')}</div>
        {self._render_metrics(data.get('metrics', {}))}
    </div>
</body>
</html>"""
        return html

    def _render_metrics(self, metrics: Dict[str, Any]) -> str:
        """Формирует HTML для метрик.

        Args:
            metrics: Словарь с метриками.

        Returns:
            HTML-строка с метриками.
        """
        if not metrics:
            return "<div>No metrics available</div>"

        lines = []
        for key, value in metrics.items():
            lines.append(f'<div class="metric">{key}: {value}</div>')
        return "\n".join(lines)

    def render_json(self, data: Dict[str, Any]) -> str:
        """Формирует JSON-ответ с данными.

        Args:
            data: Словарь с данными для сериализации.

        Returns:
            JSON-строка.
        """
        # Добавляем метаданные
        result = {
            "timestamp": datetime.now().isoformat(),
            "status": "success",
            "data": data
        }
        return json.dumps(result, ensure_ascii=False, indent=2)

    def render_events_sse(self, events: List[Dict[str, Any]]) -> str:
        """Формирует SSE-сообщения для потоковой передачи событий.

        Args:
            events: Список событий.

        Returns:
            Строка с SSE-сообщениями.
        """
        sse_messages = []
        for event in events:
            sse_messages.append(f"data: {json.dumps(event, ensure_ascii=False)}\n\n")
        return "".join(sse_messages)
