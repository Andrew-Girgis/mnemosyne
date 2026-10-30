"""Stable, sortable, prefixed IDs (ULID-style: 48-bit ms time + 80 random bits)."""

import os
import time

_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"  # Crockford base32, lowercase


def new_id(prefix: str) -> str:
    value = (int(time.time() * 1000) << 80) | int.from_bytes(os.urandom(10))
    chars = []
    for _ in range(26):
        value, rem = divmod(value, 32)
        chars.append(_ALPHABET[rem])
    return f"{prefix}_{''.join(reversed(chars))}"
