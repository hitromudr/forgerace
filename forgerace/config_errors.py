"""Кастомные исключения для конфигурации ForgeRace."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ConfigValidationError(Exception):
    """Ошибка валидации конфигурации.

    Attributes:
        message: Текст ошибки.
        source: Источник ошибки (путь к файлу конфига или "validation").
    """

    message: str
    source: str = "validation"

    def __str__(self) -> str:
        return f"[{self.source}] {self.message}"


def raise_config_error(message: str, source: str = "") -> None:
    """Бросает ConfigValidationError с указанным сообщением и источником."""
    raise ConfigValidationError(message=message, source=source or "validation")
