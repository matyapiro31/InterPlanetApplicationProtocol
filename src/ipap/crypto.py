"""Section 10.2 mitigations: Ed25519 packet signatures (MUST) and payload
end-to-end encryption (SHOULD).

One 32-byte seed per endpoint yields both the Ed25519 signing key and, via the
standard birational map, the X25519 key used for NaCl ``Box`` encryption.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from nacl.exceptions import BadSignatureError, CryptoError
from nacl.public import Box
from nacl.signing import SigningKey, VerifyKey

from .packet import Packet


class SignatureError(Exception):
    """Raised when a packet is unsigned or its signature does not verify."""


class DecryptionError(Exception):
    """Raised when an encrypted payload cannot be authenticated/decrypted."""


@dataclass(frozen=True)
class Identity:
    signing_key: SigningKey

    @classmethod
    def generate(cls) -> Identity:
        return cls(SigningKey.generate())

    @classmethod
    def from_seed(cls, seed: bytes) -> Identity:
        return cls(SigningKey(seed))

    @property
    def seed(self) -> bytes:
        return bytes(self.signing_key)

    @property
    def public_key(self) -> bytes:
        return bytes(self.signing_key.verify_key)

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.write_text(self.seed.hex() + "\n")
        p.chmod(0o600)

    @classmethod
    def load(cls, path: str | Path) -> Identity:
        return cls.from_seed(bytes.fromhex(Path(path).read_text().strip()))


def load_public_key(path: str | Path) -> bytes:
    key = bytes.fromhex(Path(path).read_text().strip())
    VerifyKey(key)  # validates length
    return key


def sign_packet(packet: Packet, identity: Identity) -> Packet:
    """Return a copy of ``packet`` with the SIGNED flag set and signature attached."""
    template = packet.with_signature(b"\x00" * 64)
    signature = identity.signing_key.sign(template.signed_portion()).signature
    return template.with_signature(signature)


def verify_packet(packet: Packet, public_key: bytes) -> None:
    if packet.signature is None:
        raise SignatureError("packet is not signed")
    try:
        VerifyKey(public_key).verify(packet.signed_portion(), packet.signature)
    except BadSignatureError as e:
        raise SignatureError("signature verification failed") from e


def _box(identity: Identity, peer_public_key: bytes) -> Box:
    return Box(identity.signing_key.to_curve25519_private_key(),
               VerifyKey(peer_public_key).to_curve25519_public_key())


def encrypt_payload(plaintext: bytes, identity: Identity, peer_public_key: bytes) -> bytes:
    return bytes(_box(identity, peer_public_key).encrypt(plaintext))


def decrypt_payload(ciphertext: bytes, identity: Identity, peer_public_key: bytes) -> bytes:
    try:
        return _box(identity, peer_public_key).decrypt(ciphertext)
    except CryptoError as e:
        raise DecryptionError("payload decryption failed") from e
