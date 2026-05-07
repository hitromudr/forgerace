"""Планировщик задач: анализ и генерация плана выполнения."""

import json
import re

from .agents import run_text_agent
from .tasks import Task

class TaskPlanner:
    """Генерирует план выполнения задачи через LLM."""

    def analyze(self, task: Task, agent_name: str) -> dict:
        """Анализирует задачу и возвращает план в формате JSON.

        Args:
            task: Объект задачи.
            agent_name: Имя агента для анализа.

        Returns:
            Словарь с планом выполнения.

        Raises:
            ValueError: Если не удалось получить валидный JSON-план.
        """
        prompt = f"""Проанализируй задачу и сгенерируй план выполнения в формате JSON.

Задача: {task.id} — {task.name}
Описание: {task.description}
Критерий готовности: {task.acceptance}

Формат ответа — строго JSON:
{{
  "steps": [
    {{"action": "read_file", "path": "path/to/file", "reason": "почему нужно прочитать"}},
    {{"action": "write_file", "path": "path/to/file", "reason": "почему нужно изменить"}},
    ...
  ],
  "dependencies": ["TASK-001", "TASK-002"],
  "estimated_time": "1h 30m",
  "confidence": 85
}}

Правила:
- steps: конкретные действия (read_file, write_file, run_command)
- dependencies: задачи, которые должны быть выполнены до этой
- estimated_time: оценка времени выполнения
- confidence: уверенность в плане (0-100)
- Пиши на русском.
"""
        try:
            response = run_text_agent(prompt, timeout=60, agent_name=agent_name)
            if not response:
                raise ValueError("Пустой ответ от агента")

            # Удаляем markdown-блоки и очищаем ответ
            response = re.sub(r"```(?:json)?\s*(.*?)\s*```", r"\1", response, flags=re.DOTALL)
            response = response.strip()

            # Парсим JSON
            data = json.loads(response)
            if not isinstance(data, dict):
                raise ValueError("Ответ не является JSON-объектом")

            return data
        except json.JSONDecodeError as e:
            raise ValueError(f"Не удалось распарсить JSON: {e}")
        except Exception as e:
            raise ValueError(f"Ошибка при анализе задачи: {e}")
