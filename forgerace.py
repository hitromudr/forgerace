#!/usr/bin/env python3
"""ForgeRace CLI — пока запускает монолит, после рефакторинга переключится на модули."""

import sys
from pathlib import Path

# Пока используем монолит
sys.path.insert(0, str(Path(__file__).parent / "forgerace"))
from orchestrator_monolith import main

if __name__ == "__main__":
    main()
