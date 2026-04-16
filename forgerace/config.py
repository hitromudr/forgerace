"""Конфигурация ForgeRace — загрузка из TOML с дефолтами."""

import logging
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        tomllib = None  # type: ignore[assignment]


@dataclass
class FrameConfig:
    """Когнитивный фрейм для дискуссий (anti-convergence)."""
    description: str = ""
    content: str = ""  # inline-контент или загруженный из файла


@dataclass
class AgentConfig:
    """Конфиг одного агента."""
    command: str = ""
    args: list[str] = field(default_factory=list)
    review_args: list[str] = field(default_factory=list)
    inactivity_timeout: int = 300
    enabled: bool = True
    protocol: str = "cli"  # "cli", "openai"
    cognitive_frame: str = ""  # legacy: inline фрейм (используется если нет frames)
    default_frame: str = ""  # frame applied when agent called without explicit +frame
    env: dict[str, str] = field(default_factory=dict)  # extra env vars for subprocess
    prompt_stdin: bool = False  # send prompt via stdin (not CLI args)
    # OpenAI-compatible API settings (protocol = "openai")
    base_url: str = ""
    api_key: str = ""
    model: str = ""


@dataclass
class PricingConfig:
    """Цены за токен (USD) по провайдерам."""
    claude_input: float = 15.0 / 1_000_000   # $15 per 1M tokens
    claude_output: float = 75.0 / 1_000_000  # $75 per 1M tokens
    gemini_input: float = 1.25 / 1_000_000   # $1.25 per 1M tokens
    gemini_output: float = 10.0 / 1_000_000  # $10 per 1M tokens
    qwen_input: float = 2.0 / 1_000_000     # $2 per 1M tokens
    qwen_output: float = 8.0 / 1_000_000    # $8 per 1M tokens
    codex_input: float = 2.0 / 1_000_000    # $2 per 1M tokens (GPT-5.4 estimate)
    codex_output: float = 8.0 / 1_000_000   # $8 per 1M tokens


@dataclass
class Config:
    """Глобальная конфигурация оркестратора."""

    # --- Пути ---
    root_dir: Path = field(default_factory=lambda: Path.cwd())
    dev_branch: str = ""  # determined at runtime from current branch
    tasks_file_rel: str = "TASKS.md"
    discuss_dir_rel: str = "docs/discuss"
    agents_dir_rel: str = ".agents"

    # --- Режим ---
    mode: str = "competitive"  # competitive | distributed

    # --- Лимиты ---
    max_retries: int = 3
    max_parallel_tasks: int = 10
    agent_timeout: int = 900
    build_timeout: int = 120
    max_review_rounds: int = 3
    review_frame: str = "adversarial"  # cognitive frame for self-review when only one agent
    max_task_complexity: int = 3
    max_reworks: int = 3
    progress_timeout: int = 600  # kill агента если diff не меняется N секунд (10 мин)
    max_concurrent: int = 3  # макс. параллельных задач в ConcurrencyLimiter
    budget_per_task_usd: Optional[float] = None

    # --- Pricing ---
    pricing: PricingConfig = field(default_factory=PricingConfig)

    # --- Когнитивные фреймы (model × frame = agent instance) ---
    frames: dict[str, FrameConfig] = field(default_factory=dict)

    # --- Агенты ---
    agents: dict[str, AgentConfig] = field(default_factory=lambda: {
        "claude": AgentConfig(
            command="claude",
            args=["-p", "--allowedTools", "Read,Write,Edit,Bash,Grep,Glob,WebFetch,WebSearch",
                  "--max-turns", "50", "--output-format", "stream-json", "--verbose"],
            review_args=["-p", "-", "--output-format", "text", "--permission-mode", "auto"],
            inactivity_timeout=300,
        ),
        "gemini": AgentConfig(
            command="gemini",
            args=["-p", "--approval-mode", "yolo", "--output-format", "stream-json"],
            review_args=["-p", "", "--output-format", "text"],
            inactivity_timeout=180,
        ),
        "qwen": AgentConfig(
            command="qwen",
            args=["-p", "--approval-mode", "yolo", "--output-format", "stream-json"],
            review_args=["-p", "-", "--approval-mode", "yolo"],
            inactivity_timeout=180,
        ),
        "codex": AgentConfig(
            command="codex",
            args=["exec", "--json", "--full-auto", "{prompt}"],
            review_args=["exec", "--json", "--full-auto", "{prompt}"],
            inactivity_timeout=300,
            enabled=True,
        ),
    })

    # --- Команды сборки ---
    build_commands: list[list[str]] = field(default_factory=list)
    lint_commands: list[list[str]] = field(default_factory=list)  # авто-фикс линтером после агента
    check_command: str = ""

    # --- Метрики: бинарники ---
    binary_glob_dir: str = ""
    binary_globs: list[str] = field(default_factory=list)

    # --- Опции ---
    review_run_log: bool = False

    # --- Хуки ---
    hook_on_complete: str = ""

    # --- Текстовые контексты ---
    project_context: str = ""
    discuss_context: str = ""
    agent_rules: str = ""
    test_instruction: str = ""  # как запускать и интерпретировать тесты
    project_docs: str = ""  # кешированный CLAUDE.md (загружается в init_config)

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
        """All enabled agents (CLI + API) — for review, decomposition, discussions."""
        return [name for name, acfg in self.agents.items() if acfg.enabled]

    @property
    def cli_agent_names(self) -> list[str]:
        """CLI-only agents — for task execution (writing code in worktree)."""
        return [name for name, acfg in self.agents.items()
                if acfg.enabled and acfg.protocol in ("cli", "text")]


# Путь к конфигу, переданный через CLI (заполняется в init_config)
_config_path: Optional[Path] = None


def run_hint() -> str:
    """Возвращает команду запуска для подсказок пользователю."""
    # Если в проекте есть ./fr — используем его
    fr_path = cfg.root_dir / "fr"
    if fr_path.exists():
        return "./fr run"
    import sys
    script = sys.argv[0]
    if script.endswith("forgerace.py"):
        base = f"python3 {script}"
    elif script.endswith("__main__.py") or "-m" in sys.orig_argv:
        base = "python3 -m forgerace"
    else:
        base = f"{sys.executable} {script}"
    if _config_path:
        base += f" --config {_config_path}"
    return base + " run"


def run_hook(hook_command: str, task_id: str, status: str, agent: str):
    """Выполняет хук с переменными окружения FORGERACE_*.
    
    Args:
        hook_command: shell-команда из конфига
        task_id: ID задачи (например, TASK-001)
        status: статус задачи (done, blocked, review:...)
        agent: имя агента, выполнившего задачу
    """
    if not hook_command:
        return
    
    env = {
        **os.environ,
        "FORGERACE_TASK_ID": task_id,
        "FORGERACE_STATUS": status,
        "FORGERACE_AGENT": agent,
    }
    
    try:
        subprocess.run(
            hook_command,
            shell=True,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        pass  # хук таймаутился — не блокируем пайплайн
    except Exception:
        pass  # ошибка хука — логгируем, но не блокируем


_LAST_CONFIG_FILE = Path.home() / ".forgerace-last"


def find_config(start_dir: Optional[Path] = None) -> Optional[Path]:
    """Ищет forgerace.toml вверх по дереву директорий (как .git)."""
    d = (start_dir or Path.cwd()).resolve()
    for _ in range(20):  # max depth
        candidate = d / "forgerace.toml"
        if candidate.exists():
            return candidate
        parent = d.parent
        if parent == d:
            break
        d = parent
    return None


def _save_last_config(path: Path):
    """Сохраняет путь к последнему использованному конфигу."""
    try:
        _LAST_CONFIG_FILE.write_text(str(path.resolve()), encoding="utf-8")
    except OSError:
        pass


def _load_last_config() -> Optional[Path]:
    """Загружает путь к последнему использованному конфигу."""
    try:
        if _LAST_CONFIG_FILE.exists():
            p = Path(_LAST_CONFIG_FILE.read_text(encoding="utf-8").strip())
            if p.exists():
                return p
    except OSError:
        pass
    return None


def load_config(config_path: Optional[Path] = None, root_dir: Optional[Path] = None) -> Config:
    """Загружает конфиг из TOML-файла. Если файла нет — возвращает дефолты.

    Порядок поиска конфига:
    1. --config (явно указан)
    2. forgerace.toml вверх по дереву от CWD
    3. Последний использованный (~/.forgerace-last)
    4. Дефолты
    """
    cfg = Config()

    if root_dir:
        cfg.root_dir = root_dir.resolve()

    if config_path is None:
        # Ищем вверх по дереву
        config_path = find_config(cfg.root_dir)

    if config_path is None:
        # Последний использованный
        config_path = _load_last_config()

    if config_path is None or not config_path.exists() or tomllib is None:
        return cfg

    _save_last_config(config_path)

    with open(config_path, "rb") as f:
        data = tomllib.load(f)

    # Директория TOML-файла — для резолва относительных путей
    toml_dir = config_path.resolve().parent

    # [project]
    proj = data.get("project", {})
    if "root" in proj:
        root_path = Path(proj["root"])
        cfg.root_dir = (toml_dir / root_path).resolve() if not root_path.is_absolute() else root_path.resolve()
    if "name" in proj:
        pass  # informational only
    if "context" in proj:
        cfg.project_context = proj["context"]
    if "discuss_context" in proj:
        cfg.discuss_context = proj["discuss_context"]
    if "dev_branch" in proj:
        cfg.dev_branch = proj["dev_branch"]
    if "tasks_file" in proj:
        cfg.tasks_file_rel = proj["tasks_file"]
    if "discuss_dir" in proj:
        cfg.discuss_dir_rel = proj["discuss_dir"]
    if "mode" in proj:
        cfg.mode = proj["mode"]

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
                enabled=acfg.get("enabled", True),
                protocol=acfg.get("protocol", "cli"),
                cognitive_frame=acfg.get("cognitive_frame", ""),
                default_frame=acfg.get("default_frame", ""),
                env=dict(acfg.get("env", {})),
                prompt_stdin=acfg.get("prompt_stdin", False),
                base_url=acfg.get("base_url", ""),
                api_key=acfg.get("api_key", ""),
                model=acfg.get("model", ""),
            )

    # [frames.*]
    frames_data = data.get("frames", {})
    if frames_data:
        cfg.frames = {}
        for name, fcfg in frames_data.items():
            content = fcfg.get("content", "")
            # Загрузка из файла: сначала ищем в проекте, потом в директории forgerace
            if not content and "file" in fcfg:
                frame_file = fcfg["file"]
                frame_path = cfg.root_dir / frame_file
                if not frame_path.exists():
                    # Fallback: относительно пакета forgerace (встроенные фреймы)
                    frame_path = Path(__file__).resolve().parent.parent / frame_file
                if frame_path.exists():
                    content = frame_path.read_text(encoding="utf-8")
                else:
                    log.warning("Frame file not found: %s (searched project and forgerace dirs)", frame_file)
            cfg.frames[name] = FrameConfig(
                description=fcfg.get("description", ""),
                content=content,
            )

    # [build]
    build = data.get("build", {})
    if "commands" in build:
        cfg.build_commands = build["commands"]
    if "lint_fix" in build:
        cfg.lint_commands = build["lint_fix"]
    if "check_command" in build:
        cfg.check_command = build["check_command"]
    if "timeout" in build:
        cfg.build_timeout = build["timeout"]

    # [limits]
    limits = data.get("limits", {})
    for key in ("max_parallel_tasks", "agent_timeout", "max_review_rounds",
                "max_task_complexity", "max_reworks", "progress_timeout", "max_concurrent",
                "budget_per_task_usd"):
        if key in limits:
            setattr(cfg, key, limits[key])
    if "review_run_log" in limits:
        cfg.review_run_log = limits["review_run_log"]
    if "review_frame" in limits:
        cfg.review_frame = limits["review_frame"]

    # [pricing]
    pricing_data = data.get("pricing", {})
    if pricing_data:
        cfg.pricing = PricingConfig(
            claude_input=pricing_data.get("claude_input", cfg.pricing.claude_input),
            claude_output=pricing_data.get("claude_output", cfg.pricing.claude_output),
            gemini_input=pricing_data.get("gemini_input", cfg.pricing.gemini_input),
            gemini_output=pricing_data.get("gemini_output", cfg.pricing.gemini_output),
            qwen_input=pricing_data.get("qwen_input", cfg.pricing.qwen_input),
            qwen_output=pricing_data.get("qwen_output", cfg.pricing.qwen_output),
        )

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

    # [hooks]
    hooks = data.get("hooks", {})
    if "on_complete" in hooks:
        cfg.hook_on_complete = hooks["on_complete"]

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
    # Кешируем документацию проекта: PROJECT_BRIEF.md (приоритет) → CLAUDE.md (fallback)
    for doc_name in ("PROJECT_BRIEF.md", "CLAUDE.md"):
        doc_path = cfg.root_dir / doc_name
        if doc_path.exists():
            try:
                content = doc_path.read_text(encoding="utf-8", errors="ignore")
                cfg.project_docs = content[:8000] + ("\n... (обрезано)" if len(content) > 8000 else "")
                break
            except Exception:
                pass
    # Определяем dev_branch: если задан явно — проверяем что ветка существует,
    # иначе (или если не существует) — используем текущую ветку
    current = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=cfg.root_dir, capture_output=True, text=True,
    ).stdout.strip() or "main"
    if cfg.dev_branch:
        check = subprocess.run(
            ["git", "rev-parse", "--verify", f"refs/heads/{cfg.dev_branch}"],
            cwd=cfg.root_dir, capture_output=True, text=True,
        )
        if check.returncode != 0:
            log.warning("Ветка '%s' не найдена, используем текущую: %s",
                        cfg.dev_branch, current)
            cfg.dev_branch = current
    else:
        cfg.dev_branch = current
    # Создаём директории
    cfg.log_dir.mkdir(parents=True, exist_ok=True)


def resolve_agent_frame(agent_spec: str) -> tuple[str, str]:
    """Разбирает спецификатор 'model+frame' → (model_name, frame_content).

    Примеры:
        'claude'          → ('claude', cognitive_frame из AgentConfig или '')
        'qwen+octagon'    → ('qwen', content из frames['octagon'])
        'gemini+arbiter'  → ('gemini', content из frames['arbiter'])

    Приоритет: explicit +frame > agent.cognitive_frame > ''
    """
    if "+" in agent_spec:
        model_name, frame_name = agent_spec.split("+", 1)
        frame_cfg = cfg.frames.get(frame_name)
        if frame_cfg and frame_cfg.content:
            return model_name, frame_cfg.content
        log.warning("Frame '%s' not found or empty, using agent default", frame_name)
        acfg = cfg.agents.get(model_name)
        return model_name, (acfg.cognitive_frame if acfg else "")
    # Без +frame — используем default_frame → cognitive_frame (legacy) → ''
    acfg = cfg.agents.get(agent_spec)
    if acfg and acfg.default_frame:
        frame_cfg = cfg.frames.get(acfg.default_frame)
        if frame_cfg and frame_cfg.content:
            return agent_spec, frame_cfg.content
    return agent_spec, (acfg.cognitive_frame if acfg else "")
