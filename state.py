import json
from pathlib import Path
from config import STATE_FILE, POSTED_FILE

def load_state():
    if not STATE_FILE.exists(): return {'events':{},'source_health':{}}
    try:
        x=json.loads(STATE_FILE.read_text(encoding='utf-8'))
        return x if isinstance(x,dict) else {'events':{},'source_health':{}}
    except Exception: return {'events':{},'source_health':{}}

def load_posted():
    if not POSTED_FILE.exists(): return set()
    return {x.strip() for x in POSTED_FILE.read_text(encoding='utf-8').splitlines() if x.strip()}

def save(state,posted):
    STATE_FILE.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
    POSTED_FILE.write_text('\n'.join(sorted(posted))+'\n',encoding='utf-8')
