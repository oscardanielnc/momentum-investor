"""
investor — Cargador de entorno portable (mini-dotenv, sin dependencias).
Carga investor/.env (gitignored) a os.environ UNA vez, sin pisar variables ya definidas.
Así el código lee SIEMPRE de os.environ y nunca hay rutas/claves hardcodeadas (repo público + VM).

Opcional: INVESTOR_FALLBACK_ENV puede apuntar a otro .env (p.ej. el de opportunity_alert en dev).
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
                if k and k not in os.environ:        # no pisar lo ya definido (env real manda)
                    os.environ[k] = v
    except Exception:
        pass


def load_env():
    """Idempotente. Carga .env del proyecto + el fallback opcional. Llamar al inicio de cada módulo."""
    global _loaded
    if _loaded:
        return
    _parse_into_environ(os.path.join(_ROOT, ".env"))
    _parse_into_environ(os.environ.get("INVESTOR_FALLBACK_ENV", ""))
    _loaded = True
