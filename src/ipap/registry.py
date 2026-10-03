"""Node-local registries: LLM model identifiers, fallback programs and test vectors."""

from __future__ import annotations

import json
from pathlib import Path

from .payloads import TestVector

MODEL_PREFIX = "ipap-"


class RegistryError(KeyError):
    pass


class ModelRegistry:
    """Maps the 8-bit header ``LLM Version`` to a model identifier in the ``ipap-`` namespace."""

    def __init__(self, models: dict[int, str] | None = None) -> None:
        self._by_id: dict[int, str] = {}
        self._by_name: dict[str, int] = {}
        for code, name in (models or DEFAULT_MODELS).items():
            self.register(code, name)

    def register(self, code: int, name: str) -> None:
        if not 0 < code <= 0xFF:
            raise ValueError("LLM version code must be 1..255 (0 means unspecified)")
        if not name.startswith(MODEL_PREFIX):
            raise ValueError(f"model identifiers must start with {MODEL_PREFIX!r}")
        self._by_id[code] = name
        self._by_name[name] = code

    def name(self, code: int) -> str:
        try:
            return self._by_id[code]
        except KeyError:
            raise RegistryError(f"unknown LLM version {code}") from None

    def code(self, name: str) -> int:
        try:
            return self._by_name[name]
        except KeyError:
            raise RegistryError(f"unknown LLM model {name!r}") from None


DEFAULT_MODELS = {1: "ipap-mock-v1", 2: "ipap-local-v2", 3: "ipap-claude", 4: "ipap-ollama"}


class ProgramRegistry:
    """Pre-installed programs referenced by EXEC ``fallback`` ("program_id:<id>")."""

    PREFIX = "program_id:"

    def __init__(self, programs: dict[str, str] | None = None) -> None:
        self._programs = dict(programs or {})

    def add(self, program_id: str, source: str) -> None:
        self._programs[program_id] = source

    def resolve(self, ref: str) -> tuple[str, str]:
        program_id = ref.removeprefix(self.PREFIX)
        try:
            return program_id, self._programs[program_id]
        except KeyError:
            raise RegistryError(f"unknown fallback program {ref!r}") from None

    @classmethod
    def from_directory(cls, directory: str | Path) -> ProgramRegistry:
        return cls({p.stem: p.read_text() for p in Path(directory).glob("*.py")})


class TestVectorRegistry:
    __test__ = False

    def __init__(self, vectors: list[TestVector] | None = None) -> None:
        self._vectors = {v.id: v for v in vectors or []}

    def add(self, vector: TestVector) -> None:
        self._vectors[vector.id] = vector

    def resolve(self, items: list[str | TestVector]) -> list[TestVector]:
        out = []
        for item in items:
            if isinstance(item, TestVector):
                out.append(item)
            elif item in self._vectors:
                out.append(self._vectors[item])
            else:
                raise RegistryError(f"unknown test vector {item!r}")
        return out

    @classmethod
    def from_json(cls, path: str | Path) -> TestVectorRegistry:
        return cls([TestVector.from_dict(d) for d in json.loads(Path(path).read_text())])
