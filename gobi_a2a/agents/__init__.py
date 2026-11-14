"""The agents the gateway can mount, by the name they have in a2a.yaml and in the URL (/claude, /cursor, ...)."""
from __future__ import annotations

from types import ModuleType

from gobi_a2a.agents import aider, claude, cursor, omp

AGENTS: dict[str, ModuleType] = {"claude": claude, "cursor": cursor, "aider": aider, "omp": omp}
