"""Diagnose all configured agents: latency, throughput, stability.

Sends short + medium probes to every enabled agent, repeats each probe
N times to measure jitter / error rate, aggregates min/avg/max/p95 and
tokens-per-second. Designed to surface broken/slow models BEFORE a
pilot run blocks for 26 minutes on a timed-out qwen.

Read-only: no coding tasks, no file edits, no /v1/embeddings — just
small chat completions against each agent's configured backend.
"""

import json
import statistics
import subprocess
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

from .config import cfg
from .utils import log, C, R


# Two probe sizes: cheap reachability + realistic generation.
_SHORT_PROMPT = "Reply with exactly the word: pong"
_MEDIUM_PROMPT = (
    "Write Python: def add(a:int,b:int)->int that returns a+b. "
    "Output only the function, no comments, no tests, no markdown fences."
)
_SHORT_REPEATS = 3   # для дисперсии
_MEDIUM_REPEATS = 2  # дороже, поэтому меньше
_PROBE_TIMEOUT = 60  # seconds per request — clamps qwen-480b's 7-minute lateness


@dataclass
class ProbeResult:
    ok: bool
    latency_s: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    error: str | None = None


@dataclass
class AgentDiagnosis:
    name: str
    protocol: str
    enabled: bool
    model: str = ""
    base_url: str = ""

    short: list[ProbeResult] = field(default_factory=list)
    medium: list[ProbeResult] = field(default_factory=list)

    # Aggregates filled by _summarize.
    short_min: float | None = None
    short_avg: float | None = None
    short_max: float | None = None
    medium_avg: float | None = None
    short_ok: int = 0
    medium_ok: int = 0
    tokens_per_sec: float | None = None
    note: str = ""


# ---------------------------------------------------------------------------
# Backend probes
# ---------------------------------------------------------------------------

def _probe_openai(base_url: str, api_key: str, model: str,
                   prompt: str, timeout: int) -> ProbeResult:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 200,
    }
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        return ProbeResult(ok=False, error=f"HTTP {e.code}: {e.read()[:120].decode(errors='replace')}")
    except urllib.error.URLError as e:
        return ProbeResult(ok=False, error=f"network: {e.reason}")
    except (TimeoutError, ConnectionError) as e:
        return ProbeResult(ok=False, error=f"timeout/connection: {e}")
    except OSError as e:
        return ProbeResult(ok=False, error=f"os: {e}")
    latency = time.perf_counter() - t0
    try:
        usage = data.get("usage", {}) or {}
        tokens_in = usage.get("prompt_tokens")
        tokens_out = usage.get("completion_tokens")
    except (AttributeError, TypeError):
        tokens_in = tokens_out = None
    return ProbeResult(ok=True, latency_s=latency,
                        tokens_in=tokens_in, tokens_out=tokens_out)


def _probe_cli(acfg, prompt: str, timeout: int) -> ProbeResult:
    """For CLI agents (claude, gemini) — run review-mode subprocess."""
    args = list(acfg.review_args or acfg.args)
    cmd = [acfg.command] + [a.replace("{prompt}", prompt) for a in args]
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True,
                               text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        return ProbeResult(ok=False, error=f"subprocess: {e}")
    latency = time.perf_counter() - t0
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "")[:120]
        return ProbeResult(ok=False, error=f"rc={proc.returncode}: {err}",
                            latency_s=latency)
    out = proc.stdout.strip()
    if not out:
        return ProbeResult(ok=False, error="empty stdout", latency_s=latency)
    return ProbeResult(ok=True, latency_s=latency)


def _aider_underlying(acfg) -> dict | None:
    """Extract (base_url, api_key, model) from aider args.

    aider itself wraps a backend model — for diagnostics we hit that
    backend directly via /v1/chat/completions (same path aider uses,
    minus aider's own overhead).
    """
    args = acfg.args
    if "--model" not in args:
        return None
    try:
        model = args[args.index("--model") + 1]
    except IndexError:
        return None
    # Aider model often prefixed with "openai/" — strip for direct call.
    if model.startswith("openai/"):
        model = model[len("openai/"):]
    if model.startswith("openai/"):
        model = model[len("openai/"):]
    base_url = args[args.index("--openai-api-base") + 1] if "--openai-api-base" in args else ""
    api_key = args[args.index("--openai-api-key") + 1] if "--openai-api-key" in args else ""
    if not base_url or not api_key:
        return None
    return {"base_url": base_url, "api_key": api_key, "model": model}


def _do_probe(acfg, prompt: str, timeout: int) -> ProbeResult:
    if acfg.protocol == "openai":
        return _probe_openai(acfg.base_url, acfg.api_key, acfg.model, prompt, timeout)
    if acfg.protocol == "text" and acfg.command == "aider":
        u = _aider_underlying(acfg)
        if u is None:
            return ProbeResult(ok=False, error="aider args missing --openai-api-base/--model")
        return _probe_openai(u["base_url"], u["api_key"], u["model"], prompt, timeout)
    if acfg.protocol == "cli":
        return _probe_cli(acfg, prompt, timeout)
    return ProbeResult(ok=False, error=f"unknown protocol {acfg.protocol!r}")


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _summarize(d: AgentDiagnosis) -> None:
    short_lat = [p.latency_s for p in d.short if p.ok and p.latency_s is not None]
    med_lat = [p.latency_s for p in d.medium if p.ok and p.latency_s is not None]
    d.short_ok = sum(1 for p in d.short if p.ok)
    d.medium_ok = sum(1 for p in d.medium if p.ok)
    if short_lat:
        d.short_min = round(min(short_lat), 2)
        d.short_max = round(max(short_lat), 2)
        d.short_avg = round(statistics.mean(short_lat), 2)
    if med_lat:
        d.medium_avg = round(statistics.mean(med_lat), 2)
    # Throughput from medium probes that returned token counts.
    samples = [(p.latency_s, p.tokens_out) for p in d.medium
                if p.ok and p.latency_s and p.tokens_out]
    if samples:
        d.tokens_per_sec = round(
            sum(out for _, out in samples) / sum(t for t, _ in samples), 1
        )

    # Note: short, machine-readable status string.
    if not d.enabled:
        d.note = "disabled"
        return
    total = len(d.short) + len(d.medium)
    failed = total - d.short_ok - d.medium_ok
    if failed == total:
        first_err = next((p.error for p in d.short + d.medium if p.error), "?")
        d.note = f"unreachable: {first_err[:80]}"
        return
    if failed > 0:
        d.note = f"flaky: {failed}/{total} failed"
        return
    if d.medium_avg and d.medium_avg > 30:
        d.note = "slow"
        return
    d.note = "ok"


def diagnose_agent(name: str, acfg) -> AgentDiagnosis:
    """Run all probes for one agent. Sequential within an agent; safe to
    call in parallel across agents (each hits its own backend)."""
    d = AgentDiagnosis(
        name=name,
        protocol=acfg.protocol,
        enabled=acfg.enabled,
        model=acfg.model or _aider_underlying(acfg) and _aider_underlying(acfg)["model"] or "",
        base_url=acfg.base_url or _aider_underlying(acfg) and _aider_underlying(acfg)["base_url"] or "",
    )
    if not acfg.enabled:
        _summarize(d)
        return d
    for _ in range(_SHORT_REPEATS):
        d.short.append(_do_probe(acfg, _SHORT_PROMPT, _PROBE_TIMEOUT))
    for _ in range(_MEDIUM_REPEATS):
        d.medium.append(_do_probe(acfg, _MEDIUM_PROMPT, _PROBE_TIMEOUT))
    _summarize(d)
    return d


def diagnose_all(parallel: int = 4, only_enabled: bool = True) -> list[AgentDiagnosis]:
    """Probe every configured agent. Returns sorted list."""
    items = list(cfg.agents.items())
    if only_enabled:
        items = [(n, c) for n, c in items if c.enabled]
    log.info(f"Probing {len(items)} agents (parallelism={parallel})...")
    out: list[AgentDiagnosis] = []
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futs = {pool.submit(diagnose_agent, n, c): n for n, c in items}
        for f in as_completed(futs):
            try:
                out.append(f.result())
            except Exception as e:  # pragma: no cover
                # Diagnostics shouldn't crash the whole run.
                name = futs[f]
                log.error(f"diagnose_agent({name}) crashed: {e}")
                out.append(AgentDiagnosis(name=name, protocol="?", enabled=True, note=f"crash: {e}"))
    return sorted(out, key=lambda d: d.name)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def print_report(results: list[AgentDiagnosis]) -> None:
    print(f"\n{C['bold']}Agent Diagnostics{R}  {C['dim']}(short×{_SHORT_REPEATS} + medium×{_MEDIUM_REPEATS}, timeout {_PROBE_TIMEOUT}s){R}\n")
    header = f"  {'agent':<18} {'proto':<6} {'short min/avg/max':<20} {'med avg':>8} {'tok/s':>6}  {'errs':>5}  status"
    print(header)
    print(f"  {'-' * (len(header) - 2)}")
    for d in results:
        proto = d.protocol[:5]
        if d.short_avg is not None:
            short_str = f"{d.short_min}/{d.short_avg}/{d.short_max}"
        else:
            short_str = "—"
        med_str = f"{d.medium_avg}" if d.medium_avg is not None else "—"
        tps_str = f"{d.tokens_per_sec}" if d.tokens_per_sec else "—"
        total = len(d.short) + len(d.medium)
        ok = d.short_ok + d.medium_ok
        errs = f"{total - ok}/{total}"
        if not d.enabled:
            color = C['dim']
        elif d.note == "ok":
            color = C['green']
        elif d.note == "slow":
            color = C['yellow']
        else:
            color = C['red']
        print(f"  {d.name:<18} {proto:<6} {short_str:<20} {med_str:>8} {tps_str:>6}  {errs:>5}  {color}{d.note}{R}")
    print()
    # Hint: which agents would block a real run.
    bad = [d for d in results if d.enabled and d.note not in ("ok", "slow")]
    if bad:
        print(f"  {C['red']}{C['bold']}{len(bad)} agent(s) would likely fail in a pilot run:{R}")
        for d in bad:
            first_err = next((p.error for p in d.short + d.medium if p.error), "?")
            print(f"    {C['red']}{d.name}{R}: {first_err[:120]}")
        print()


def save_json(results: list[AgentDiagnosis], path: Path | None = None) -> Path:
    if path is None:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        out_dir = cfg.agents_dir / "diagnostics"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"agents-{ts}.json"
    data = {
        "timestamp": datetime.now().isoformat(),
        "results": [asdict(d) for d in results],
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def run_probe(parallel: int = 4, json_out: bool = False, all_agents: bool = False) -> int:
    """CLI entry point. Returns exit code (0 = ok, 1 = at least one bad)."""
    results = diagnose_all(parallel=parallel, only_enabled=not all_agents)
    if json_out:
        path = save_json(results)
        print(json.dumps([asdict(d) for d in results], indent=2, ensure_ascii=False))
        log.info(f"Saved JSON to {path}")
    else:
        print_report(results)
        save_json(results)
    bad = [d for d in results if d.enabled and d.note not in ("ok", "slow", "disabled")]
    return 1 if bad else 0
