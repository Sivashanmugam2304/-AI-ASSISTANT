import os
import json

STATE_PATH = os.path.join(os.getcwd(), 'assistant_offline_state.json')

# Default: online
_state = {'offline': False}


def load_state():
    global _state
    try:
        if os.path.exists(STATE_PATH):
            with open(STATE_PATH, 'r') as f:
                _state = json.load(f)
    except Exception:
        _state = {'offline': False}


def save_state():
    try:
        with open(STATE_PATH, 'w') as f:
            json.dump(_state, f)
    except Exception:
        pass


def is_offline() -> bool:
    return bool(_state.get('offline', False))


def set_offline(value: bool):
    _state['offline'] = bool(value)
    save_state()


def toggle_offline():
    _state['offline'] = not bool(_state.get('offline', False))
    save_state()
    return _state['offline']


# Initialize on import
load_state()
