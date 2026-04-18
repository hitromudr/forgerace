"""Мерж task-веток в develop (через detached worktree + update-ref)."""

import threading
from .config import cfg
from .utils import log, run_cmd
from .types import MergeResult

_merge_lock = threading.Lock()


def merge_to_develop(branch: str, task_id: str) -> MergeResult:
    """Мержит task-ветку в develop без переключения веток.
    Использует detached worktree + update-ref + прогон тестов."""
    with _merge_lock:
        merge_dir = cfg.agents_dir / "_merge_tmp"
        if merge_dir.exists():
            run_cmd(["git", "worktree", "remove", str(merge_dir), "--force"],
                    cwd=cfg.root_dir, check=False)

        # Detached HEAD на текущем коммите develop
        dev_sha = run_cmd(
            ["git", "rev-parse", cfg.dev_branch], cwd=cfg.root_dir, check=False,
        ).stdout.strip()
        run_cmd(
            ["git", "worktree", "add", "--detach", str(merge_dir), dev_sha],
            cwd=cfg.root_dir, check=False,
        )

        merge_stdout = ""
        merge_stderr = ""
        test_stdout = ""
        test_stderr = ""

        try:
            # 1. Гит мерж
            res = run_cmd(
                ["git", "merge", branch, "--no-ff", "-X", "theirs",
                 "-m", f"Merge {task_id}: {branch} → {cfg.dev_branch}"],
                cwd=merge_dir, check=False,
            )
            merge_stdout = res.stdout
            merge_stderr = res.stderr

            if res.returncode != 0:
                err_msg = (res.stderr or res.stdout or "неизвестная ошибка")[:500]
                log.error(f"  ✗ Merge {branch} → {cfg.dev_branch} провалился:\n{err_msg}")
                run_cmd(["git", "merge", "--abort"], cwd=merge_dir, check=False)
                return MergeResult(
                    success=False,
                    merge_stdout=merge_stdout,
                    merge_stderr=merge_stderr,
                    test_stdout="",
                    test_stderr="",
                    returncode=res.returncode
                )

            # 2. Прогон тестов перед финальным update-ref
            if cfg.check_command:
                log.info(f"  🔍 Проверка тестов после мержа {task_id} ({cfg.check_command})...")
                test_res = run_cmd(
                    ["bash", "-c", cfg.check_command], cwd=merge_dir,
                    timeout=cfg.build_timeout * 2, check=False)
                test_stdout = test_res.stdout
                test_stderr = test_res.stderr

                if test_res.returncode != 0:
                    log.error(f"  ✗ Тесты провалены после мержа {task_id}")
                    # Откатываем мерж, так как тесты не прошли
                    run_cmd(["git", "merge", "--abort"], cwd=merge_dir, check=False)
                    return MergeResult(
                        success=False,
                        merge_stdout=merge_stdout,
                        merge_stderr=merge_stderr,
                        test_stdout=test_stdout,
                        test_stderr=test_stderr,
                        returncode=test_res.returncode,
                        is_test_failure=True
                    )
                log.info(f"  ✅ Тесты пройдены для {task_id}")

            # 2.5. Revert agent changes to orchestrator core files
            _MERGE_PROTECTED = (
                "TASKS.md", "forgerace.toml", "litellm_config.yaml",
                "CLAUDE.md", ".gitignore",
                # Orchestrator core — agents must not modify the tool that runs them
                "forgerace/cli.py", "forgerace/pipeline.py", "forgerace/agents.py",
                "forgerace/config.py", "forgerace/merge.py", "forgerace/worktree.py",
                "forgerace/tasks.py", "forgerace/decompose.py", "forgerace/discuss.py",
                "forgerace/utils.py", "forgerace/cost.py",
            )
            changed = run_cmd(
                ["git", "diff", "--name-only", dev_sha, "HEAD"],
                cwd=merge_dir, check=False,
            )
            reverted = []
            for fname in (changed.stdout or "").strip().split("\n"):
                fname = fname.strip()
                if fname in _MERGE_PROTECTED:
                    run_cmd(["git", "checkout", dev_sha, "--", fname],
                            cwd=merge_dir, check=False)
                    reverted.append(fname)
            if reverted:
                run_cmd(["git", "commit", "--amend", "--no-edit", "-a"],
                        cwd=merge_dir, check=False)
                log.info(f"  🛡 Reverted {len(reverted)} protected files: {', '.join(reverted)}")

            # 3. Обновляем ветку develop на новый merge-коммит
            merge_sha = run_cmd(
                ["git", "rev-parse", "HEAD"], cwd=merge_dir, check=False,
            ).stdout.strip()
            ref_res = run_cmd(
                ["git", "update-ref", f"refs/heads/{cfg.dev_branch}", merge_sha],
                cwd=cfg.root_dir, check=False,
            )
            if ref_res.returncode != 0:
                log.error(f"  ✗ update-ref failed for {cfg.dev_branch}: "
                          f"{(ref_res.stderr or ref_res.stdout or '')[:300]}")
                return MergeResult(
                    success=False,
                    merge_stdout=merge_stdout,
                    merge_stderr=merge_stderr,
                    test_stdout=test_stdout,
                    test_stderr=test_stderr,
                    returncode=ref_res.returncode
                )

            log.info(f"  ✓ {branch} вмержен в {cfg.dev_branch}")
            return MergeResult(
                success=True,
                merge_stdout=merge_stdout,
                merge_stderr=merge_stderr,
                test_stdout=test_stdout,
                test_stderr=test_stderr,
                returncode=0
            )
        finally:
            run_cmd(["git", "worktree", "remove", str(merge_dir), "--force"],
                    cwd=cfg.root_dir, check=False)
