import unittest
from unittest.mock import patch, MagicMock
from src.notifier import Notifier

class TestNotifier(unittest.TestCase):
    """Тесты для Notifier."""

    def setUp(self):
        """Настройка тестов."""
        self.config = {
            "webhook": {
                "url": "https://example.com/webhook",
                "headers": {"Content-Type": "application/json"},
                "timeout": 5
            },
            "telegram": {
                "token": "test_token",
                "chat_id": "test_chat",
                "timeout": 5
            }
        }
        self.notifier = Notifier(self.config)

    @patch('requests.post')
    def test_send_webhook_success(self, mock_post):
        """Тест успешной отправки webhook."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        result = self.notifier.send_webhook("Test message", {"key": "value"})

        self.assertTrue(result)
        mock_post.assert_called_once_with(
            "https://example.com/webhook",
            json={"message": "Test message", "key": "value"},
            headers={"Content-Type": "application/json"},
            timeout=5
        )

    @patch('requests.post')
    def test_send_webhook_failure(self, mock_post):
        """Тест неудачной отправки webhook."""
        mock_post.side_effect = Exception("Network error")

        result = self.notifier.send_webhook("Test message")

        self.assertFalse(result)

    @patch('requests.post')
    def test_send_telegram_success(self, mock_post):
        """Тест успешной отправки telegram."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        result = self.notifier.send_telegram("Test message")

        self.assertTrue(result)
        mock_post.assert_called_once_with(
            "https://api.telegram.org/bottest_token/sendMessage",
            json={"chat_id": "test_chat", "text": "Test message", "parse_mode": "Markdown"},
            timeout=5
        )

    @patch('requests.post')
    def test_send_telegram_failure(self, mock_post):
        """Тест неудачной отправки telegram."""
        mock_post.side_effect = Exception("Network error")

        result = self.notifier.send_telegram("Test message")

        self.assertFalse(result)

    def test_send_webhook_no_config(self):
        """Тест отправки webhook без конфигурации."""
        notifier = Notifier({})
        result = notifier.send_webhook("Test message")
        self.assertFalse(result)

    def test_send_telegram_no_config(self):
        """Тест отправки telegram без конфигурации."""
        notifier = Notifier({})
        result = notifier.send_telegram("Test message")
        self.assertFalse(result)

    def test_send_unknown_channel(self):
        """Тест отправки через неизвестный канал."""
        result = self.notifier.send("Test message", channel="unknown")
        self.assertFalse(result)

if __name__ == '__main__':
    unittest.main()
