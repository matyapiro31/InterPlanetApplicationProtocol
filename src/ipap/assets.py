"""Section 8 IPFS integration: a local content-addressed store that resolves
``ipfs://<CID>`` references in EXEC payloads.

CIDs here are CIDv0 (base58btc of the sha2-256 multihash) computed over the raw
bytes. Real IPFS wraps file content in a UnixFS/dag-pb node before hashing, so
these CIDs are not interchangeable with ``ipfs add`` output; swapping in a real
IPFS client only requires another ``AssetStore`` implementation.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_INDEX = {c: i for i, c in enumerate(_B58_ALPHABET)}
_SHA2_256 = 0x12
_SHA2_256_LEN = 0x20
_REF = re.compile(r"^\s*(?:ipfs://|/ipfs/)?(?P<cid>[1-9A-HJ-NP-Za-km-z]+)(?:\s+(?P<label>.*))?$")


class AssetError(Exception):
    pass


class AssetNotFound(AssetError):
    pass


def b58encode(data: bytes) -> str:
    n = int.from_bytes(data, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _B58_ALPHABET[r] + out
    pad = len(data) - len(data.lstrip(b"\x00"))
    return "1" * pad + out


def b58decode(text: str) -> bytes:
    n = 0
    for c in text:
        try:
            n = n * 58 + _B58_INDEX[c]
        except KeyError as e:
            raise AssetError(f"invalid base58 character: {c!r}") from e
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    pad = len(text) - len(text.lstrip("1"))
    return b"\x00" * pad + body


def cid_v0(data: bytes) -> str:
    return b58encode(bytes([_SHA2_256, _SHA2_256_LEN]) + hashlib.sha256(data).digest())


def parse_cid(cid: str) -> bytes:
    """Validate a CIDv0 string and return its sha2-256 digest."""
    mh = b58decode(cid)
    if len(mh) != 34 or mh[0] != _SHA2_256 or mh[1] != _SHA2_256_LEN:
        raise AssetError(f"not a CIDv0 sha2-256 identifier: {cid}")
    return mh[2:]


def parse_ref(ref: str) -> tuple[str, str | None]:
    """Split ``"ipfs://Qm...  (label)"`` into (cid, label)."""
    m = _REF.match(ref)
    if not m:
        raise AssetError(f"malformed asset reference: {ref!r}")
    cid = m.group("cid")
    parse_cid(cid)
    label = m.group("label")
    return cid, label.strip().strip("()").strip() if label else None


class AssetStore:
    """In-memory content-addressed store, optionally persisted to a directory."""

    def __init__(self, directory: str | Path | None = None) -> None:
        self._blobs: dict[str, bytes] = {}
        self.directory = Path(directory) if directory else None
        if self.directory:
            self.directory.mkdir(parents=True, exist_ok=True)
            for p in self.directory.iterdir():
                if p.is_file():
                    self.put(p.read_bytes())

    def put(self, data: bytes) -> str:
        cid = cid_v0(data)
        if cid not in self._blobs:
            self._blobs[cid] = bytes(data)
            if self.directory:
                (self.directory / cid).write_bytes(data)
        return cid

    def has(self, cid: str) -> bool:
        return cid in self._blobs

    def get(self, cid: str) -> bytes:
        try:
            data = self._blobs[cid]
        except KeyError:
            raise AssetNotFound(cid) from None
        if hashlib.sha256(data).digest() != parse_cid(cid):
            raise AssetError(f"stored content does not match CID {cid}")
        return data

    def resolve(self, refs: list[str]) -> dict[str, bytes]:
        """Resolve every reference (Section 8.2 MUST); raises AssetNotFound on the first miss."""
        resolved: dict[str, bytes] = {}
        for ref in refs:
            cid, _ = parse_ref(ref)
            resolved[cid] = self.get(cid)
        return resolved
