import requests
import logging
from typing import Optional, Dict, Any

class Notifier:
    """
    Notifier для отправки уведомлений через webhook и telegram.

    Поддерживает:
    - Webhook (POST запрос с JSON)
    - Telegram (через bot API)
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Инициализация Notifier.

        Args:
            config: Конфигурация с параметрами webhook и telegram.
                   Пример:
                   {
                       "webhook": {
                           "url": "https://example.com/webhook",
                           "headers": {"Content-Type": "application/json"},
                           "timeout": 10
                       },
                       "telegram": {
                           "token": "BOT_TOKEN",
                           "chat_id": "CHAT_ID",
                           "timeout": 10
                       }
                   }
        """
        self.config = config or {}
        self.logger = logging.getLogger(__name__)

    def send_webhook(self, message: str, data: Optional[Dict[str, Any]] = None) -> bool:
        """
        Отправляет уведомление через webhook.

        Args:
            message: Текст сообщения
            data: Дополнительные данные для отправки

        Returns:
            bool: True если отправка успешна, False в случае ошибки
        """
        if not self.config.get("webhook"):
            self.logger.warning("Webhook не настроен")
            return False

        webhook_config = self.config["webhook"]
        url = webhook_config.get("url")
        if not url:
            self.logger.error("Webhook URL не указан")
            return False

        payload = {"message": message}
        if data:
            payload.update(data)

        headers = webhook_config.get("headers", {"Content-Type": "application/json"})
        timeout = webhook_config.get("timeout", 10)

        try:
            response = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=timeout
            )
            response.raise_for_status()
            self.logger.info(f"Webhook отправлен успешно: {response.status_code}")
            return True
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Ошибка отправки webhook: {e}")
            return False

    def send_telegram(self, message: str) -> bool:
        """
        Отправляет уведомление через Telegram bot API.

        Args:
            message: Текст сообщения

        Returns:
            bool: True если отправка успешна, False в случае ошибки
        """
        if not self.config.get("telegram"):
            self.logger.warning("Telegram не настроен")
            return False

        telegram_config = self.config["telegram"]
        token = telegram_config.get("token")
        chat_id = telegram_config.get("chat_id")

        if not token or not chat_id:
            self.logger.error("Telegram token или chat_id не указаны")
            return False

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "Markdown"
        }
        timeout = telegram_config.get("timeout", 10)

        try:
            response = requests.post(
                url,
                json=payload,
                timeout=timeout
            )
            response.raise_for_status()
            self.logger.info(f"Telegram сообщение отправлено успешно: {response.status_code}")
            return True
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Ошибка отправки telegram: {e}")
            return False

    def send(self, message: str, channel: str = "webhook", data: Optional[Dict[str, Any]] = None) -> bool:
        """
        Универсальный метод отправки уведомления.

        Args:
            message: Текст сообщения
            channel: Канал отправки ('webhook' или 'telegram')
            data: Дополнительные данные для webhook

        Returns:
            bool: True если отправка успешна, False в случае ошибки
        """
        if channel == "webhook":
            return self.send_webhook(message, data)
        elif channel == "telegram":
            return self.send_telegram(message)
        else:
            self.logger.error(f"Неизвестный канал: {channel}")
            return False
