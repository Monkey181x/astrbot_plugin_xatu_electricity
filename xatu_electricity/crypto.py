from __future__ import annotations

import base64
import secrets

from Cryptodome.Cipher import AES
from Cryptodome.Util.Padding import pad

AES_CHARACTERS = "ABCDEFGHJKMNPQRSTWXYZabcdefhijkmnprstwxyz2345678"


def _random_string(length: int) -> str:
    return "".join(secrets.choice(AES_CHARACTERS) for _ in range(length))


def encrypt_cas_password(password: str, salt: str) -> str:
    """Match the AES-CBC transformation used by the XATU CAS login page."""

    key = salt.strip().encode("utf-8")
    if len(key) != AES.block_size:
        raise ValueError(f"CAS AES salt must be 16 bytes, got {len(key)}")

    plaintext = (_random_string(64) + password).encode("utf-8")
    iv = _random_string(AES.block_size).encode("utf-8")
    cipher = AES.new(key, AES.MODE_CBC, iv)
    encrypted = cipher.encrypt(pad(plaintext, AES.block_size))
    return base64.b64encode(encrypted).decode("ascii")
