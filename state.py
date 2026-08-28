import json
from config import STATE_FILE, POSTED_FILE

def load():
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {
        "version": 2,
        "last_run": None,
        "published_events": {},
        "source_health": {},
        "queue": {},
    }

def load_posted():
    if not POSTED_FILE.exists():
        return set()
    return {x.strip() for x in POSTED_FILE.read_text(encoding="utf-8").splitlines() if x.strip()}

def save(state, posted):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    POSTED_FILE.write_text("\n".join(sorted(posted)) + ("\n" if posted else ""), encoding="utf-8")
