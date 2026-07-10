"""Password hashing (Day 21, §7.1).

Deliberately ``hashlib.scrypt`` — a memory-hard KDF that has shipped in the
stdlib (via OpenSSL) since Python 3.6 — rather than the argon2/bcrypt named
in the roadmap: adding a mandatory third-party dependency to a core auth
path would break this project's standing "stdlib-core, everything else is a
lazy-imported extra" architecture (``agents``/``service``/``postgres``/
``queue``/``observability`` are all optional; password hashing isn't
optional for anyone who enables auth at all). scrypt is in the same security
class as bcrypt/argon2 for this purpose. The stored encoding versions its
own cost parameters (``n``/``r``/``p``), so they can be raised later without
invalidating already-issued hashes.
"""

from __future__ import annotations

import hashlib
import hmac
import os

_N = 2 ** 14  # CPU/memory cost factor
_R = 8        # block size
_P = 1        # parallelization
_DKLEN = 32
_SALT_BYTES = 16


def hash_password(password: str) -> str:
    salt = os.urandom(_SALT_BYTES)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${dk.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time verification. Returns ``False`` (never raises) for a
    malformed/foreign encoding — e.g. a hash from a future cost-parameter
    bump this version doesn't recognize."""
    try:
        algo, n, r, p, salt_hex, hash_hex = encoded.split("$")
        if algo != "scrypt":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except ValueError:
        return False
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected))
    return hmac.compare_digest(dk, expected)
