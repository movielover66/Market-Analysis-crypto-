"""
Application-level authenticated encryption for `private.provider_credentials` (Section 17 & 18).
Uses HMAC-SHA256 + CTR-mode stream cipher derived via PBKDF2-HMAC-SHA256 from TOKEN_ENCRYPTION_KEY
so it works across any Python 3.10+ environment without external C-extension issues.
"""
import base64
import hashlib
import hmac
import json
import os
from typing import Any, Dict


def _derive_keys(master_key: str, salt: bytes) -> tuple:
    if not master_key or len(master_key.strip()) < 16:
        raise ValueError("TOKEN_ENCRYPTION_KEY must be at least 16 characters")
    dk = hashlib.pbkdf2_hmac("sha256", master_key.encode("utf-8"), salt, 60000, dklen=64)
    return dk[:32], dk[32:]


def _keystream_xor(data: bytes, enc_key: bytes, nonce: bytes) -> bytes:
    out = bytearray(len(data))
    block_idx = 0
    offset = 0
    while offset < len(data):
        counter_bytes = block_idx.to_bytes(8, "big")
        block = hmac.new(enc_key, nonce + counter_bytes, hashlib.sha256).digest()
        chunk = data[offset : offset + 32]
        for i, b in enumerate(chunk):
            out[offset + i] = b ^ block[i]
        offset += 32
        block_idx += 1
    return bytes(out)


def encrypt_secret_payload(payload: Dict[str, Any], master_key: str) -> str:
    plaintext = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    salt = os.urandom(16)
    nonce = os.urandom(16)
    enc_key, mac_key = _derive_keys(master_key, salt)
    ciphertext = _keystream_xor(plaintext, enc_key, nonce)
    tag = hmac.new(mac_key, salt + nonce + ciphertext, hashlib.sha256).digest()
    blob = salt + nonce + tag + ciphertext
    return base64.urlsafe_b64encode(blob).decode("ascii")


def decrypt_secret_payload(token_b64: str, master_key: str) -> Dict[str, Any]:
    blob = base64.urlsafe_b64decode(token_b64.encode("ascii"))
    if len(blob) < 64:
        raise ValueError("Encrypted payload is truncated")
    salt, nonce, tag, ciphertext = blob[:16], blob[16:32], blob[32:64], blob[64:]
    enc_key, mac_key = _derive_keys(master_key, salt)
    expected_tag = hmac.new(mac_key, salt + nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected_tag):
        raise ValueError("Encrypted payload authentication tag mismatch")
    plaintext = _keystream_xor(ciphertext, enc_key, nonce)
    return json.loads(plaintext.decode("utf-8"))
