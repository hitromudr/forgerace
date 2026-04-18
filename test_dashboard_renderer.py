"""Тесты для DashboardRenderer."""

import json
import unittest
from src.dashboard_renderer import DashboardRenderer

class TestDashboardRenderer(unittest.TestCase):
    """Тесты для DashboardRenderer."""

    def setUp(self):
        """Инициализация перед каждым тестом."""
        self.renderer = DashboardRenderer()

    def test_render_html(self):
        """Тест формирования HTML."""
        data = {
            "status": "active",
            "metrics": {
                "cpu": 45.2,
                "memory": 78.1,
                "disk": 65.3
            }
        }
        html = self.renderer.render_html(data)
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("Dashboard", html)
        self.assertIn("active", html)
        self.assertIn("cpu: 45.2", html)

    def test_render_json(self):
        """Тест формирования JSON."""
        data = {
            "status": "active",
            "metrics": {
                "cpu": 45.2,
                "memory": 78.1
            }
        }
        json_str = self.renderer.render_json(data)
        parsed = json.loads(json_str)
        self.assertEqual(parsed["status"], "success")
        self.assertEqual(parsed["data"]["status"], "active")
        self.assertEqual(parsed["data"]["metrics"]["cpu"], 45.2)

    def test_render_events_sse(self):
        """Тест формирования SSE-сообщений."""
        events = [
            {"type": "status", "value": "active"},
            {"type": "metric", "name": "cpu", "value": 45.2}
        ]
        sse = self.renderer.render_events_sse(events)
        self.assertIn("data: ", sse)
        self.assertIn("active", sse)
        self.assertIn("cpu", sse)

if __name__ == "__main__":
    unittest.main()
