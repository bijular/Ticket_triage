"""Paths, config, dataset ids and scoped-token helpers shared by every component."""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "config" / "eval.json").read_text())
DATA = ROOT / "data"
REPLAY = DATA / "replay"      # what the mock API serves (never contains answer keys)
PROMPTS = DATA / "prompts"    # tool-facing task text per dataset
KEYS = ROOT / "answer_keys"   # reviewer-only; never mount where a tool can read it
RUNS = ROOT / "runs"
# tests may set common.RUNS_DIR_OVERRIDE to redirect run logs


def dataset_id(name: str) -> str:
    """Opaque id so folder names / URLs do not reveal the scenario (blind review)."""
    h = hashlib.sha256(f"{CONFIG['id_salt']}:{name}".encode()).hexdigest()
    return "ds-" + h[:6]


def seed_for(name: str) -> int:
    h = hashlib.sha256(f"{CONFIG['seed_base']}:{name}".encode()).hexdigest()
    return int(h[:8], 16)


# ---- scoped tokens (HMAC, short-lived) -------------------------------------
def _secret() -> bytes:
    env = os.environ.get("EVAL_SECRET")
    if env:
        return env.encode()
    envf = ROOT / ".env"           # written by `init-env`; docker compose reads the same file
    if envf.exists():
        for line in envf.read_text().splitlines():
            if line.startswith("EVAL_SECRET="):
                return line.split("=", 1)[1].strip().encode()
    p = ROOT / ".secret"
    if not p.exists():
        p.write_text(secrets.token_hex(32))
        p.chmod(0o600)
    return p.read_text().strip().encode()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def mint_token(scope: str, run_id: str, dataset: str, tool: str,
               ttl: int = None, max_exp: float = None, now: float = None) -> str:
    now = time.time() if now is None else now
    ttl = CONFIG["token_ttl_seconds"] if ttl is None else ttl
    max_exp = max_exp if max_exp is not None else now + CONFIG["max_session_seconds"]
    claims = {"scope": scope, "run": run_id, "ds": dataset, "tool": tool,
              "iat": now, "exp": min(now + ttl, max_exp), "max_exp": max_exp}
    body = _b64(json.dumps(claims, sort_keys=True).encode())
    sig = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


class AuthError(Exception):
    pass


def verify_token(token: str, scope: str, now: float = None) -> dict:
    now = time.time() if now is None else now
    try:
        body, sig = token.split(".", 1)
        good = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good):
            raise AuthError("bad signature")
        claims = json.loads(_unb64(body))
    except AuthError:
        raise
    except Exception:
        raise AuthError("malformed token")
    if claims.get("scope") != scope:
        raise AuthError("wrong scope")
    if now >= claims["exp"]:
        raise AuthError("token expired")
    return claims
