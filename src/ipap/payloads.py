"""JSON payload schemas for each message type (Sections 5.3, 7.2 and 8.2)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, ClassVar

from .constants import IPAP_VERSION_STRING, LLMAvailability, MessageType, Status


class PayloadError(ValueError):
    """Raised when a payload is not valid JSON or does not match its schema."""


def _require(data: dict[str, Any], key: str, kind: type | tuple[type, ...]) -> Any:
    if key not in data:
        raise PayloadError(f"missing field: {key}")
    value = data[key]
    if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
        raise PayloadError(f"field {key!r} has wrong type: {type(value).__name__}")
    return value


def _optional(data: dict[str, Any], key: str, kind: type | tuple[type, ...], default: Any) -> Any:
    if data.get(key) is None:
        return default
    return _require(data, key, kind)


@dataclass
class TestVector:
    """An input/expected-output pair checked against the generated program's ``main``."""

    __test__: ClassVar[bool] = False  # not a pytest test class

    id: str
    input: Any = None
    expected: Any = None
    tolerance: float | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TestVector:
        if not isinstance(d, dict):
            raise PayloadError("test vector must be an object")
        tol = _optional(d, "tolerance", (int, float), None)
        return cls(id=_require(d, "id", str), input=d.get("input"), expected=d.get("expected"),
                   tolerance=None if tol is None else float(tol))

    def to_dict(self) -> dict[str, Any]:
        d = {"id": self.id, "input": self.input, "expected": self.expected}
        if self.tolerance is not None:
            d["tolerance"] = self.tolerance
        return d


@dataclass
class Constraints:
    language: str = "python"
    max_tokens: int = 2048
    timeout_ms: int = 30000
    # Each entry is a test vector ID known to the node, or an inline TestVector.
    test_vectors: list[str | TestVector] = field(default_factory=list)
    return_code: bool = False

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Constraints:
        vectors: list[str | TestVector] = []
        for v in _optional(d, "test_vectors", list, []):
            vectors.append(v if isinstance(v, str) else TestVector.from_dict(v))
        c = cls(
            language=_optional(d, "language", str, "python"),
            max_tokens=_optional(d, "max_tokens", int, 2048),
            timeout_ms=_optional(d, "timeout_ms", int, 30000),
            test_vectors=vectors,
            return_code=_optional(d, "return_code", bool, False),
        )
        if c.max_tokens <= 0 or c.timeout_ms <= 0:
            raise PayloadError("max_tokens and timeout_ms must be positive")
        return c

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "language": self.language,
            "max_tokens": self.max_tokens,
            "timeout_ms": self.timeout_ms,
            "test_vectors": [v if isinstance(v, str) else v.to_dict() for v in self.test_vectors],
        }
        if self.return_code:
            d["return_code"] = True
        return d


class Payload:
    msg_type: ClassVar[MessageType]

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Payload:
        raise NotImplementedError


@dataclass
class WakePayload(Payload):
    msg_type: ClassVar[MessageType] = MessageType.WAKE
    ipap_version: str = IPAP_VERSION_STRING

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WakePayload:
        return cls(ipap_version=_optional(d, "ipap_version", str, IPAP_VERSION_STRING))


@dataclass
class ReadyPayload(Payload):
    msg_type: ClassVar[MessageType] = MessageType.READY
    in_reply_to: int
    power: float
    power_level: str
    llm: LLMAvailability
    llm_model: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["llm"] = self.llm.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ReadyPayload:
        try:
            llm = LLMAvailability(_require(d, "llm", str))
        except ValueError as e:
            raise PayloadError(f"unknown llm availability: {d['llm']}") from e
        return cls(
            in_reply_to=_require(d, "in_reply_to", int),
            power=float(_require(d, "power", (int, float))),
            power_level=_require(d, "power_level", str),
            llm=llm,
            llm_model=_optional(d, "llm_model", str, None),
            reason=_optional(d, "reason", str, None),
        )


@dataclass
class ExecPayload(Payload):
    msg_type: ClassVar[MessageType] = MessageType.EXEC
    prompt: str
    llm_model: str | None = None
    constraints: Constraints = field(default_factory=Constraints)
    assets: list[str] = field(default_factory=list)
    fallback: str | None = None
    input: Any = None
    ipap_version: str = IPAP_VERSION_STRING

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"ipap_version": self.ipap_version}
        if self.llm_model:
            d["llm_model"] = self.llm_model
        d["prompt"] = self.prompt
        if self.assets:
            d["assets"] = list(self.assets)
        d["constraints"] = self.constraints.to_dict()
        if self.input is not None:
            d["input"] = self.input
        if self.fallback:
            d["fallback"] = self.fallback
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ExecPayload:
        prompt = _require(d, "prompt", str)
        if not prompt.strip():
            raise PayloadError("prompt must not be empty")
        assets = _optional(d, "assets", list, [])
        if not all(isinstance(a, str) for a in assets):
            raise PayloadError("assets must be a list of strings")
        return cls(
            prompt=prompt,
            llm_model=_optional(d, "llm_model", str, None),
            constraints=Constraints.from_dict(_optional(d, "constraints", dict, {})),
            assets=assets,
            fallback=_optional(d, "fallback", str, None),
            input=d.get("input"),
            ipap_version=_optional(d, "ipap_version", str, IPAP_VERSION_STRING),
        )


@dataclass
class ResultPayload(Payload):
    msg_type: ClassVar[MessageType] = MessageType.RESULT
    in_reply_to: int
    status: Status
    output: Any = None
    error: str | None = None
    program: str | None = None  # "generated" or "fallback:<program_id>"
    code_sha256: str | None = None
    code: str | None = None
    verification: list[dict[str, Any]] | None = None
    metrics: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["status"] = self.status.value
        if self.output is None and "output" in d:
            del d["output"]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ResultPayload:
        try:
            status = Status(_require(d, "status", str))
        except ValueError as e:
            raise PayloadError(f"unknown status: {d['status']}") from e
        return cls(
            in_reply_to=_require(d, "in_reply_to", int),
            status=status,
            output=d.get("output"),
            error=_optional(d, "error", str, None),
            program=_optional(d, "program", str, None),
            code_sha256=_optional(d, "code_sha256", str, None),
            code=_optional(d, "code", str, None),
            verification=_optional(d, "verification", list, None),
            metrics=_optional(d, "metrics", dict, None),
        )


@dataclass
class AbortPayload(Payload):
    msg_type: ClassVar[MessageType] = MessageType.ABORT
    target_seq: int | None = None  # None aborts whatever is running
    reason: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AbortPayload:
        return cls(target_seq=_optional(d, "target_seq", int, None),
                   reason=_optional(d, "reason", str, None))


@dataclass
class StatusPayload(Payload):
    msg_type: ClassVar[MessageType] = MessageType.STATUS
    state: str | None = None
    power: float | None = None
    power_level: str | None = None
    llm: str | None = None
    active_seq: int | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> StatusPayload:
        power = _optional(d, "power", (int, float), None)
        return cls(
            state=_optional(d, "state", str, None),
            power=None if power is None else float(power),
            power_level=_optional(d, "power_level", str, None),
            llm=_optional(d, "llm", str, None),
            active_seq=_optional(d, "active_seq", int, None),
        )


PAYLOAD_TYPES: dict[MessageType, type[Payload]] = {
    cls.msg_type: cls
    for cls in (WakePayload, ReadyPayload, ExecPayload, ResultPayload, AbortPayload, StatusPayload)
}


def describe(payload: Payload) -> str:
    """A one-line human-readable summary used in logs."""
    if isinstance(payload, ReadyPayload):
        return f"llm={payload.llm} power={payload.power}% reply_to={payload.in_reply_to}"
    if isinstance(payload, ResultPayload):
        text = f"status={payload.status} reply_to={payload.in_reply_to}"
        return text + (f" error={payload.error!r}" if payload.error else "")
    if isinstance(payload, StatusPayload):
        return f"state={payload.state} power={payload.power}%"
    if isinstance(payload, AbortPayload):
        return f"target_seq={payload.target_seq}"
    return ""


def encode_payload(payload: Payload) -> bytes:
    return json.dumps(payload.to_dict(), ensure_ascii=False, separators=(",", ":")).encode()


def decode_payload(msg_type: MessageType, raw: bytes) -> Payload:
    try:
        data = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise PayloadError(f"payload is not valid UTF-8 JSON: {e}") from e
    if not isinstance(data, dict):
        raise PayloadError("payload must be a JSON object")
    return PAYLOAD_TYPES[msg_type].from_dict(data)


__all__ = [
    "AbortPayload",
    "Constraints",
    "ExecPayload",
    "PAYLOAD_TYPES",
    "Payload",
    "PayloadError",
    "ReadyPayload",
    "ResultPayload",
    "StatusPayload",
    "TestVector",
    "WakePayload",
    "decode_payload",
    "describe",
    "encode_payload",
]
