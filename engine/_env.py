"""
Minimal, dependency-free .env loader.

Loads the project's .env (gitignored) into os.environ once, without overriding variables that
are already set. All code reads configuration from os.environ, so no keys or paths are
hardcoded.

Optional: INVESTOR_FALLBACK_ENV can point to a second .env file that is read after the
project one (useful in development to share keys with another local project).
"""
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_loaded = False


def _parse_into_environ(path):
    if not path or not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.split(" #")[0].strip().strip('"').strip("'")
                if k and k not in os.environ:        # the real environment always wins
                    os.environ[k] = v
    except Exception:
        pass


def load_env():
    """Idempotent. Loads the project .env plus the optional fallback file."""
    global _loaded
    if _loaded:
        return
    _parse_into_environ(os.path.join(_ROOT, ".env"))
    _parse_into_environ(os.environ.get("INVESTOR_FALLBACK_ENV", ""))
    _loaded = True
