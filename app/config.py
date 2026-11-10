"""Runtime settings. Reads .env locally; on Render the same names come from the dashboard."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_DIR = Path(__file__).resolve().parent

_QUOTES = "\"'“”‘’ "


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip(_QUOTES))


_load_dotenv(ROOT / ".env")


def env(name: str, default: str = "") -> str:
    """Env value with stray quotes (straight or curly) stripped."""
    return os.getenv(name, default).strip().strip(_QUOTES)


DB_PATH = env("TOWER_DB", str(ROOT / "data" / "tower.db"))
KEEP_DB = env("TOWER_KEEP_DB", "0") == "1"
LLM_MODE = env("LLM_MODE", "hybrid")          # hybrid | live | scripted
LLM_RPM = int(env("LLM_RPM", "14"))
TASK_GAP_SECONDS = float(env("TASK_GAP_SECONDS", "14"))   # pause between tasks per agent
STEP_DELAY_SECONDS = float(env("STEP_DELAY_SECONDS", "1.2"))  # makes steps watchable
APPROVAL_TTL_SECONDS = int(env("APPROVAL_TTL_SECONDS", "600"))
IDLE_AFTER_SECONDS = int(env("IDLE_AFTER_SECONDS", "600"))  # no viewers -> fleet idles to save quota
