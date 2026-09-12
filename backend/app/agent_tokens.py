"""Agent bearer tokens — same construction as FiresLog's own
agent_tokens.py (see docs/MULTI_HOST_PLAN.md for why this project reuses
that pattern rather than inventing a new one).

Unlike a login password, an agent token is already 256 bits of random
bearer secret with no guessable structure — there's nothing for a slow
KDF (argon2id, as used for actual passwords) to defend against here.
Instead it's HMAC-SHA256'd with a server-side pepper and stored hashed,
indexed for direct O(1) lookup (`WHERE token_hash = ?`) on every
WebSocket handshake, rather than an argon2 per-row verify loop that would
force an O(n) scan to find which row a given token belongs to.
"""

import hashlib
import hmac
import secrets

from . import config

TOKEN_PREFIX_LEN = 12


def generate_token() -> str:
    return secrets.token_urlsafe(32)  # 256 bits, cryptographically random


def hash_token(token: str) -> str:
    pepper = config.AGENT_TOKEN_PEPPER.encode("utf-8")
    return hmac.new(pepper, token.encode("utf-8"), hashlib.sha256).hexdigest()


def token_prefix(token: str) -> str:
    return token[:TOKEN_PREFIX_LEN]  # non-secret, just lets the UI show "which token is this"


def verify_token(token: str, token_hash: str) -> bool:
    return hmac.compare_digest(hash_token(token), token_hash)
