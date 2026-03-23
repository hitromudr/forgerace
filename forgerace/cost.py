"""Учёт токенов и оценка стоимости вызовов LLM."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TokenUsage:
    """Накопленная статистика использования токенов."""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    # Ответ ревьюеру на 3: поле estimated_usd добавлено в класс TokenUsage (изменения cost.py могли не попасть в diff)
    estimated_usd: float = 0.0

    def add_input(self, tokens: int):
        """Добавляет входные токены."""
        self.input_tokens += tokens

    def add_output(self, tokens: int):
        """Добавляет выходные токены."""
        self.output_tokens += tokens

    def add_cache_read(self, tokens: int):
        """Добавляет кэшированные входные токены (Claude)."""
        self.cache_read_input_tokens += tokens

    def total_input(self) -> int:
        """Возвращает общее количество входных токенов (включая кэш)."""
        return self.input_tokens + self.cache_read_input_tokens

    def accumulate(self, other: "TokenUsage"):
        """Накапливает статистику из другого TokenUsage."""
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_input_tokens += other.cache_read_input_tokens
        self.estimated_usd += other.estimated_usd

    def calc_cost(self, input_price: float, output_price: float, 
                  cache_read_price: Optional[float] = None) -> float:
        """
        Считает стоимость в USD.
        
        Args:
            input_price: цена за 1M входных токенов
            output_price: цена за 1M выходных токенов
            cache_read_price: цена за 1M кэшированных входных (если None — как input_price)
        """
        if cache_read_price is None:
            cache_read_price = input_price
        
        # input_price/output_price уже в USD за 1 токен (PricingConfig делит на 1M)
        cost = (
            self.input_tokens * input_price +
            self.output_tokens * output_price +
            self.cache_read_input_tokens * cache_read_price
        )
        if self.estimated_usd == 0.0:
            self.estimated_usd = cost
        return cost


def parse_claude_usage(event: dict) -> Optional[TokenUsage]:
    """
    Парсит usage из события Claude stream-json.
    
    Ожидает событие типа 'result' с полем 'usage':
    {
        "type": "result",
        "usage": {
            "input_tokens": 1000,
            "output_tokens": 500,
            "cache_read_input_tokens": 200
        }
    }
    """
    usage = event.get("usage", {})
    cost = event.get("total_cost_usd", 0.0)
    if not usage and not cost:
        return None
    
    return TokenUsage(
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        cache_read_input_tokens=usage.get("cache_read_input_tokens", 0),
        estimated_usd=float(cost),
    )


def parse_gemini_usage(event: dict) -> Optional[TokenUsage]:
    """
    Парсит usage из события Gemini stream-json.
    
    Ожидает событие с полем 'usageMetadata' или 'stats':
    {
        "type": "result",
        "usageMetadata": {
            "promptTokenCount": 1000,
            "candidatesTokenCount": 500,
            "cachedContentTokenCount": 200
        }
    }
    """
    # Пробуем разные форматы
    usage = event.get("usageMetadata") or event.get("stats", {})
    cost = event.get("total_cost_usd", 0.0)
    if not usage and not cost:
        return None
    
    # Gemini использует другие названия полей
    input_tokens = (
        usage.get("promptTokenCount", 0) or 
        usage.get("input_tokens", 0)
    )
    output_tokens = (
        usage.get("candidatesTokenCount", 0) or 
        usage.get("output_tokens", 0) or
        usage.get("responseTokenCount", 0)
    )
    cache_tokens = (
        usage.get("cachedContentTokenCount", 0) or
        usage.get("cache_read_input_tokens", 0)
    )
    
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=cache_tokens,
        estimated_usd=float(cost),
    )


def parse_usage_event(event: dict, provider: str) -> Optional[TokenUsage]:
    """
    Универсальный парсер usage для любого провайдера.
    
    Args:
        event: событие из stream-json
        provider: "claude" или "gemini"
    
    Returns:
        TokenUsage или None если usage не найден
    """
    if provider == "claude":
        return parse_claude_usage(event)
    elif provider == "gemini":
        return parse_gemini_usage(event)
    else:
        # Qwen и другие — Claude-совместимый формат
        return parse_claude_usage(event)
