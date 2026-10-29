import json
import os

CONFIG_FILE = os.path.join(os.path.dirname(__file__), '.config.json')

DEFAULTS = {
    'mode':    'local',   # 'local' | 'online'
    'api_url': 'http://zeus.hidencloud.com:25535',
    'api_key': '',
}


def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                saved = json.load(f)
            return {**DEFAULTS, **saved}
        except Exception:
            pass
    return DEFAULTS.copy()


def save_config(cfg: dict) -> None:
    merged = {**DEFAULTS, **cfg}
    with open(CONFIG_FILE, 'w') as f:
        json.dump(merged, f, indent=2)
