"""Типы данных для межмодульного обмена."""

from dataclasses import dataclass


@dataclass
class MergeResult:
    """Результат операции merge с тестами."""
    success: bool
    merge_stdout: str
    merge_stderr: str
    test_stdout: str
    test_stderr: str
    returncode: int
    is_test_failure: bool = False

    @property
    def stdout(self) -> str:
        """Для совместимости с subprocess.CompletedProcess."""
        return self.test_stdout or self.merge_stdout

    @property
    def stderr(self) -> str:
        """Для совместимости с subprocess.CompletedProcess."""
        return self.test_stderr or self.merge_stderr
