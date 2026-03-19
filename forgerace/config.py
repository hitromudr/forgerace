"""Конфигурация ForgeRace — загрузка из TOML с дефолтами."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        tomllib = None  # type: ignore[assignment]


@dataclass
class AgentConfig:
    """Конфиг одного агента."""
    command: str
    args: list[str] = field(default_factory=list)
    review_args: list[str] = field(default_factory=list)
    inactivity_timeout: int = 300


@dataclass
class Config:
    """Глобальная конфигурация оркестратора."""

    # --- Пути ---
    root_dir: Path = field(default_factory=lambda: Path.cwd())
    dev_branch: str = "develop"
    tasks_file_rel: str = "TASKS.md"
    discuss_dir_rel: str = "docs/discuss"
    agents_dir_rel: str = ".agents"

    # --- Лимиты ---
    max_retries: int = 3
    max_parallel_tasks: int = 4
    agent_timeout: int = 900
    build_timeout: int = 120
    max_review_rounds: int = 3
    max_task_complexity: int = 3
    progress_timeout: int = 600  # kill агента если diff не меняется N секунд (10 мин)

    # --- Агенты ---
    agents: dict[str, AgentConfig] = field(default_factory=lambda: {
        "claude": AgentConfig(
            command="claude",
            args=["-p", "--allowedTools", "Read,Write,Edit,Bash,Grep,Glob",
                  "--max-turns", "50", "--output-format", "stream-json", "--verbose"],
            review_args=["-p", "-", "--output-format", "text", "--permission-mode", "auto"],
            inactivity_timeout=300,
        ),
        "gemini": AgentConfig(
            command="gemini",
            args=["-p", "--approval-mode", "yolo", "--output-format", "stream-json"],
            review_args=["-p", "-"],
            inactivity_timeout=180,
        ),
    })

    # --- Команды сборки ---
    build_commands: list[list[str]] = field(default_factory=lambda: [
        ["cargo", "build"],
        ["cargo", "test", "--no-run"],
    ])
    check_command: str = "make check"

    # --- Метрики: бинарники ---
    binary_glob_dir: str = "target/x86_64-unknown-none/debug"
    binary_globs: list[str] = field(default_factory=lambda: ["*.bin", "ethos*"])

    # --- Текстовые контексты ---
    project_context: str = ""
    discuss_context: str = ""
    agent_rules: str = ""
    test_instruction: str = ""  # как запускать и интерпретировать тесты

    confidence_instruction: str = """
В ПОСЛЕДНЕЙ строке ответа ОБЯЗАТЕЛЬНО напиши свою оценку готовности решения к реализации:
CONFIDENCE: XX%
где XX — число от 0 до 100:
- 0-30%: есть принципиальные разногласия или неясности
- 30-60%: направление понятно, но детали требуют обсуждения
- 60-80%: решение в целом готово, мелкие вопросы остались
- 80-100%: можно утверждать и реализовывать"""

    # --- Вычисляемые пути ---
    @property
    def tasks_file(self) -> Path:
        return self.root_dir / self.tasks_file_rel

    @property
    def discuss_dir(self) -> Path:
        return self.root_dir / self.discuss_dir_rel

    @property
    def agents_dir(self) -> Path:
        return self.root_dir / self.agents_dir_rel

    @property
    def log_dir(self) -> Path:
        return self.agents_dir / "logs"

    @property
    def agent_names(self) -> list[str]:
        return list(self.agents.keys())


# Путь к конфигу, переданный через CLI (заполняется в init_config)
_config_path: Optional[Path] = None


def run_hint() -> str:
    """Возвращает команду запуска для подсказок пользователю."""
    import sys
    script = sys.argv[0]
    # Короткая форма: python3 forgerace.py или просто forgerace
    if script.endswith("forgerace.py"):
        base = f"python3 {script}"
    elif script.endswith("__main__.py") or "-m" in sys.orig_argv:
        base = "python3 -m forgerace"
    else:
        base = f"{sys.executable} {script}"
    if _config_path:
        base += f" --config {_config_path}"
    return base + " run"


def load_config(config_path: Optional[Path] = None, root_dir: Optional[Path] = None) -> Config:
    """Загружает конфиг из TOML-файла. Если файла нет — возвращает дефолты."""
    cfg = Config()

    if root_dir:
        cfg.root_dir = root_dir.resolve()

    if config_path is None:
        # Ищем forgerace.toml в root_dir
        config_path = cfg.root_dir / "forgerace.toml"

    if not config_path.exists() or tomllib is None:
        return cfg

    with open(config_path, "rb") as f:
        data = tomllib.load(f)

    # [project]
    proj = data.get("project", {})
    if "root" in proj:
        cfg.root_dir = Path(proj["root"]).resolve()
    if "name" in proj:
        pass  # informational only
    if "context" in proj:
        cfg.project_context = proj["context"]
    if "dev_branch" in proj:
        cfg.dev_branch = proj["dev_branch"]
    if "tasks_file" in proj:
        cfg.tasks_file_rel = proj["tasks_file"]
    if "discuss_dir" in proj:
        cfg.discuss_dir_rel = proj["discuss_dir"]

    # [agents.*]
    agents_data = data.get("agents", {})
    if agents_data:
        cfg.agents = {}
        for name, acfg in agents_data.items():
            cfg.agents[name] = AgentConfig(
                command=acfg.get("command", name),
                args=acfg.get("args", []),
                review_args=acfg.get("review_args", []),
                inactivity_timeout=acfg.get("inactivity_timeout", 300),
            )

    # [build]
    build = data.get("build", {})
    if "commands" in build:
        cfg.build_commands = build["commands"]
    if "check_command" in build:
        cfg.check_command = build["check_command"]
    if "timeout" in build:
        cfg.build_timeout = build["timeout"]

    # [limits]
    limits = data.get("limits", {})
    for key in ("max_parallel_tasks", "agent_timeout", "max_review_rounds", "max_task_complexity", "progress_timeout"):
        if key in limits:
            setattr(cfg, key, limits[key])

    # [rules]
    rules = data.get("rules", {})
    if "agent_rules" in rules:
        cfg.agent_rules = rules["agent_rules"]
    if "test_instruction" in rules:
        cfg.test_instruction = rules["test_instruction"]

    # [metrics]
    metrics = data.get("metrics", {})
    if "binary_glob_dir" in metrics:
        cfg.binary_glob_dir = metrics["binary_glob_dir"]
    if "binary_globs" in metrics:
        cfg.binary_globs = metrics["binary_globs"]

    return cfg


# Глобальный конфиг — инициализируется при первом импорте или через init_config()
cfg = Config()


def init_config(config_path: Optional[Path] = None, root_dir: Optional[Path] = None):
    """Инициализирует глобальный конфиг in-place (чтобы все модули видели изменения)."""
    global _config_path
    _config_path = config_path
    new_cfg = load_config(config_path, root_dir)
    # Обновляем существующий объект, а не заменяем — иначе from .config import cfg
    # в других модулях будет ссылаться на старый объект
    for field_obj in cfg.__dataclass_fields__:
        setattr(cfg, field_obj, getattr(new_cfg, field_obj))
    # Создаём директории
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
