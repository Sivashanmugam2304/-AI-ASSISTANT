import pyttsx3
import speech_recognition as sr
import datetime
import wikipedia
import webbrowser
import os
import pvporcupine
import pyaudio
import struct
import subprocess
import vlc
import time
import threading
import re
import pygame  # For alarm sound
from pygame import mixer
import requests
import json
import shutil
import conversation_mode
import offline_mode
import logging

# Initialize pygame mixer for alarm sound
pygame.init()
mixer.init()

engine = pyttsx3.init()
voices = engine.getProperty('voices')
engine.setProperty('voice', voices[27].id)
engine.setProperty('rate', 150)
engine.setProperty('volume', 1.0)

# Global flags and threads
alarm_thread = None
timer_thread = None
alarm_cancel = False
timer_cancel = False
alarm_sound = None
stop_reading = False
monitoring_thread = None
monitoring_active = False
monitoring_interval = 30  # seconds between checks
# Default thresholds
thresholds = {
    'load_avg': max(1.0, (os.cpu_count() or 1) * 1.5),
    'mem_used_pct': 85.0,  # percent used
    'disk_free_pct': 10.0  # percent free
}
# Battery threshold (percent)
thresholds.setdefault('battery_low_pct', 20.0)
config_path = os.path.join(os.getcwd(), 'assistant_config.json')
user_data_path = os.path.join(os.getcwd(), 'assistant_user_data.json')
events_path = os.path.join(os.getcwd(), 'assistant_events.json')
# in-memory events list
events = []
# Text-mode input provider (when set, get_input will use it instead of voice takeCommand)
_text_mode_provider = None


def set_text_mode_provider(fn):
    global _text_mode_provider
    _text_mode_provider = fn


def clear_text_mode_provider():
    global _text_mode_provider
    _text_mode_provider = None


def get_input(prompt=None):
    """Unified input for follow-up prompts: uses text-mode provider when active, else falls back to takeCommand().

    If prompt is provided and text-mode provider is active, the prompt will be printed to the terminal.
    """
    if _text_mode_provider:
        try:
            if prompt:
                # In text mode, print the prompt for clarity
                print(prompt)
            return _text_mode_provider(prompt)
        except Exception:
            pass
    # fallback to normal voice input
    return takeCommand()


def load_events():
    global events
    try:
        if os.path.exists(events_path):
            with open(events_path, 'r') as f:
                events = json.load(f)
                return events
    except Exception as e:
        print(f"Error loading events: {e}")
    events = []
    return events


def save_events():
    try:
        with open(events_path, 'w') as f:
            json.dump(events, f, indent=2, default=str)
        speak('Events saved.')
    except Exception as e:
        print(f"Error saving events: {e}")
        speak('Failed to save events.')


def next_event_id():
    load_events()
    if not events:
        return 1
    return max(int(e.get('id', 0)) for e in events) + 1


def parse_datetime_input(text):
    """Try to parse a natural language datetime into a timezone-aware ISO string.
    Falls back to dateutil.parser if available, else naive parsing for common patterns.
    """
    try:
        import dateutil.parser as dp
        dt = dp.parse(text, default=datetime.datetime.now())
        # make timezone-aware if naive
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.isoformat()
    except Exception:
        # simple patterns: YYYY-MM-DD HH:MM or DD/MM/YYYY HH:MM or words like 'tomorrow 7pm'
        m = re.search(r"(\d{4}-\d{2}-\d{2})[ T](\d{1,2}:\d{2})", text)
        if m:
            try:
                dt = datetime.datetime.strptime(m.group(1) + ' ' + m.group(2), '%Y-%m-%d %H:%M')
                dt = dt.replace(tzinfo=datetime.timezone.utc)
                return dt.isoformat()
            except Exception:
                pass
    return None


def create_event_via_voice():
    speak('Creating a new event. What is the title?')
    title = get_input('Event title:')
    if not title or title in ('none', 'None'):
        speak('Cancelled event creation.')
        return

    speak('When is the event? Please provide a date and time, for example "2025-10-25 18:30" or "tomorrow 7 pm".')
    when = get_input('Event date/time:')
    when_iso = parse_datetime_input(when) if when and when not in ('none', 'None') else None

    speak('Any description for the event?')
    desc = get_input('Event description:')

    load_events()
    ev = {
        'id': str(next_event_id()),
        'title': title.strip(),
        'when': when_iso,
        'description': desc.strip() if desc and desc not in ('none', 'None') else ''
    }
    events.append(ev)
    save_events()
    log_action({'type': 'calendar', 'action': 'create', 'event': ev})
    speak(f'Event "{ev["title"]}" created.')


def view_events(range_type='upcoming', days=7):
    """View events. range_type: 'daily','weekly','monthly','upcoming'"""
    load_events()
    now = datetime.datetime.now(datetime.timezone.utc)
    results = []
    for e in events:
        try:
            when = None
            if e.get('when'):
                when = datetime.datetime.fromisoformat(e['when'])
            if not when:
                continue
            if range_type == 'daily':
                if when.date() == now.date():
                    results.append(e)
            elif range_type == 'weekly':
                if 0 <= (when - now).days < 7:
                    results.append(e)
            elif range_type == 'monthly':
                if when.year == now.year and when.month == now.month:
                    results.append(e)
            else:  # upcoming
                if when >= now:
                    results.append(e)
        except Exception:
            continue

    if not results:
        speak('No events found for that range.')
        return
    speak(f'Found {len(results)} events:')
    for ev in sorted(results, key=lambda x: x.get('when') or '')[:10]:
        when_text = ev.get('when') or 'unspecified time'
        speak(f"{ev.get('title')} at {when_text}. {ev.get('description','')}")


def list_events():
    """List all events concisely with id, title and time."""
    load_events()
    if not events:
        speak('You have no saved events.')
        return
    speak(f'You have {len(events)} events. Here are the first twenty:')
    for e in events[:20]:
        speak(f"{e.get('id')}: {e.get('title')} at {e.get('when') or 'unspecified'}")


def find_event_by_id(eid):
    for e in events:
        if str(e.get('id')) == str(eid):
            return e
    return None


def edit_event_via_voice():
    load_events()
    speak('Please tell me the event id to edit.')
    eid = get_input('Event id to edit:')
    if not eid or eid in ('none', 'None'):
        speak('No id provided.')
        return
    ev = find_event_by_id(eid)
    if not ev:
        speak('Event not found.')
        return
    speak(f'Editing event {ev.get("title")}. Say title, time, description or cancel.')
    field = get_input('Which field to edit (title/time/description):')
    if not field:
        speak('Cancelled.')
        return
    if 'title' in field:
        speak('What is the new title?')
        val = get_input('New title:')
        ev['title'] = val
    elif 'time' in field or 'date' in field:
        speak('What is the new date and time?')
        val = get_input('New date/time:')
        parsed = parse_datetime_input(val)
        if parsed:
            ev['when'] = parsed
        else:
            speak('Could not parse that time.')
    elif 'description' in field:
        speak('What is the new description?')
        val = get_input('New description:')
        ev['description'] = val
    else:
        speak('Unknown field. Cancelled.')
        return
    save_events()
    speak('Event updated.')


def delete_event_via_voice():
    load_events()
    if not events:
        speak('You have no events to delete.')
        return

    # If there are multiple events, list them and allow numeric selection
    speak(f'You have {len(events)} events. Here are the first twenty:')
    for idx, e in enumerate(events[:20], start=1):
        speak(f"{idx}. {e.get('title')} (id: {e.get('id')}) at {e.get('when') or 'unspecified'})")

    speak('Which event would you like to delete? You can say the number or the event id.')
    choice = get_input('Event number or id to delete:')
    if not choice or choice in ('none', 'None'):
        speak('No selection provided.')
        return

    choice = choice.strip()
    ev = None
    # Try numeric index first (1-based)
    m = re.match(r"^(\d+)$", choice)
    if m:
        idx = int(m.group(1)) - 1
        if 0 <= idx < len(events):
            ev = events[idx]
    # If not numeric, try matching by id
    if not ev:
        ev = find_event_by_id(choice)

    if not ev:
        speak('Event not found for that selection.')
        return

    # Confirmation
    speak(f'Are you sure you want to delete "{ev.get("title")}"? Say yes to confirm.')
    confirm = get_input('Confirm deletion (yes/no):')
    if not confirm or confirm.lower() not in ('yes', 'y', 'confirm'):
        speak('Deletion cancelled.')
        return

    try:
        events.remove(ev)
        save_events()
        log_action({'type': 'calendar', 'action': 'delete', 'event': ev})
        speak('Event deleted.')
    except Exception as e:
        logging.exception('Failed to delete event: %s', e)
        speak('Failed to delete the event.')


def search_events(query_term):
    load_events()
    q = (query_term or '').lower()
    results = [e for e in events if q in (e.get('title','').lower() + ' ' + e.get('description','').lower())]
    return results

user_data = {}
LOG_PATH = os.path.join(os.getcwd(), 'assistant_actions.log')

# Configure basic logging to file as fallback
logging.basicConfig(filename=os.path.join(os.getcwd(), 'assistant_debug.log'), level=logging.DEBUG,
                    format='%(asctime)s [%(levelname)s] %(message)s')

# Weather API Configuration
WEATHER_API_URL = "http://api.openweathermap.org/data/2.5/weather"
FORECAST_API_URL = "http://api.openweathermap.org/data/2.5/forecast"
# NOTE: It's recommended you store API keys securely. This value is a placeholder.
WEATHER_API_KEY = "a33b03c56e6b4e127f3aa400d12bcc42"

def speak(audio):
    print(f"Assistant: {audio}")
    # Log assistant response
    try:
        log_action({'type': 'response', 'text': str(audio)})
    except Exception:
        logging.exception('Failed to log speak() response')
    engine.say(audio)
    engine.runAndWait()

def wishMe():
    hour = int(datetime.datetime.now().hour)
    if 0 <= hour < 12:
        speak("Good Morning!")
    elif 12 <= hour < 18:
        speak("Good Afternoon!")
    else:
        speak("Good Evening!")
    speak("I am your assistant. Please tell me how may I help you.")

def takeCommand():
    r = sr.Recognizer()
    with sr.Microphone() as source:
        print("Listening...")
        r.pause_threshold = 1
        try:
            audio = r.listen(source, timeout=5, phrase_time_limit=5)
            query = r.recognize_google(audio, language='en-in')
            print(f"User said: {query}")
        except Exception as e:
            print("Say that again please...", e)
            return "None"
    # Log recognized command
    try:
        log_action({'type': 'command', 'text': str(query)})
    except Exception:
        logging.exception('Failed to log takeCommand()')
    return query.lower()

def stop_speaking():
    """Stop any current speech."""
    try:
        engine.stop()
    except Exception:
        pass

def listen_for_stop_command(timeout=1):
    """Listen briefly for stop-like commands while reading."""
    r = sr.Recognizer()
    with sr.Microphone() as source:
        r.pause_threshold = 0.5
        r.energy_threshold = 400
        try:
            audio = r.listen(source, timeout=timeout, phrase_time_limit=2)
            command = r.recognize_google(audio, language='en-in').lower()
            if any(word in command for word in ['stop', 'stop reading', 'enough', "that's enough"]):
                return True
        except Exception:
            return False
    return False


def log_action(entry: dict):
    """Append a timestamped JSON line to LOG_PATH. Entry is a dict with arbitrary keys."""
    try:
        e = dict(entry)
        # Use timezone-aware UTC timestamp to avoid DeprecationWarning
        e['timestamp'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with open(LOG_PATH, 'a') as lf:
            lf.write(json.dumps(e, ensure_ascii=False) + '\n')
    except Exception:
        # Ensure logging to debug log if file write fails
        logging.exception('Failed writing to assistant_actions.log')


def normalize_key(key: str) -> str:
    """Normalize keys for consistent storage and lookup."""
    if not key:
        return ''
    k = key.strip().lower()
    # remove surrounding quotes and filler words
    k = re.sub(r"[\"'`]", '', k)
    k = re.sub(r"\b(my|the|a|an|that|called|named|is)\b", '', k)
    k = re.sub(r'[^\w\s-]', '', k)
    k = re.sub(r'\s+', ' ', k).strip()
    return k


def _detect_brightness_backend():
    """Detect method to control brightness. Prefer 'ddcutil' for external monitors, else 'xbacklight' or 'brightnessctl'."""
    if shutil.which('ddcutil'):
        return 'ddcutil'
    if shutil.which('xbacklight'):
        return 'xbacklight'
    if shutil.which('brightnessctl'):
        return 'brightnessctl'
    # On some systems, writing to /sys/class/backlight is possible
    backlight_base = '/sys/class/backlight'
    if os.path.isdir(backlight_base):
        # pick first device
        try:
            dev = os.listdir(backlight_base)[0]
            return f'sys:{dev}'
        except Exception:
            pass
    return None


def get_brightness():
    """Return current brightness as integer percent or None if unsupported."""
    backend = _detect_brightness_backend()
    try:
        if backend == 'ddcutil':
            out = subprocess.check_output(['ddcutil', 'getvcp', '10'], text=True, stderr=subprocess.DEVNULL)
            m = re.search(r'current value = (\d+),', out)
            if m:
                return int(m.group(1))
        elif backend == 'xbacklight':
            out = subprocess.check_output(['xbacklight', '-get'], text=True)
            return int(round(float(out.strip())))
        elif backend == 'brightnessctl':
            out = subprocess.check_output(['brightnessctl', 'get'], text=True)
            maxv = subprocess.check_output(['brightnessctl', 'max'], text=True)
            cur = int(out.strip())
            mx = int(maxv.strip())
            return int(round((cur / mx) * 100))
        elif backend and backend.startswith('sys:'):
            dev = backend.split(':', 1)[1]
            base = os.path.join('/sys/class/backlight', dev)
            with open(os.path.join(base, 'brightness')) as f:
                cur = int(f.read().strip())
            with open(os.path.join(base, 'max_brightness')) as f:
                mx = int(f.read().strip())
            return int(round((cur / mx) * 100))
    except Exception:
        logging.exception('Failed to get brightness')
    return None


def set_brightness(percent: int):
    """Set brightness to percent (0-100). Return True on success."""
    percent = max(0, min(100, int(percent)))
    backend = _detect_brightness_backend()
    try:
        if backend == 'ddcutil':
            subprocess.run(['ddcutil', 'setvcp', '10', str(percent)], check=False)
            return True
        elif backend == 'xbacklight':
            subprocess.run(['xbacklight', '-set', str(percent)], check=False)
            return True
        elif backend == 'brightnessctl':
            # brightnessctl set accepts percentage
            subprocess.run(['brightnessctl', 'set', f'{percent}%'], check=False)
            return True
        elif backend and backend.startswith('sys:'):
            dev = backend.split(':', 1)[1]
            base = os.path.join('/sys/class/backlight', dev)
            with open(os.path.join(base, 'max_brightness')) as f:
                mx = int(f.read().strip())
            value = int(round((percent / 100.0) * mx))
            # Need root permissions for this; attempt write
            try:
                with open(os.path.join(base, 'brightness'), 'w') as f:
                    f.write(str(value))
                return True
            except Exception:
                # Try using sudo tee as fallback
                try:
                    subprocess.run(['sudo', 'tee', os.path.join(base, 'brightness')], input=str(value), text=True)
                    return True
                except Exception:
                    logging.exception('Failed to set sys brightness via sudo')
                    return False
    except Exception:
        logging.exception('Failed to set brightness')
    return False


def increase_brightness(step=10):
    cur = get_brightness()
    if cur is None:
        return False
    return set_brightness(min(100, cur + int(step)))


def decrease_brightness(step=10):
    cur = get_brightness()
    if cur is None:
        return False
    return set_brightness(max(0, cur - int(step)))

def load_config():
    try:
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                cfg = json.load(f)
                thresholds.update(cfg.get('thresholds', {}))
                global monitoring_interval
                monitoring_interval = cfg.get('monitoring_interval', monitoring_interval)
                return cfg
    except Exception as e:
        print(f"Error loading config: {e}")
    return {'thresholds': thresholds, 'monitoring_interval': monitoring_interval}

def save_config():
    try:
        cfg = {'thresholds': thresholds, 'monitoring_interval': monitoring_interval}
        with open(config_path, 'w') as f:
            json.dump(cfg, f, indent=2)
        speak('Configuration saved.')
    except Exception as e:
        print(f"Error saving config: {e}")
        speak('Failed to save configuration.')

def load_user_data():
    global user_data
    try:
        if os.path.exists(user_data_path):
            with open(user_data_path, 'r') as f:
                user_data = json.load(f)
                return user_data
    except Exception as e:
        print(f"Error loading user data: {e}")
    user_data = {}
    return user_data

def save_user_data():
    try:
        with open(user_data_path, 'w') as f:
            json.dump(user_data, f, indent=2)
        speak('User data saved.')
    except Exception as e:
        print(f"Error saving user data: {e}")
        speak('Failed to save user data.')

def set_user_data_via_voice():
    """Interactively set a key/value pair for user data."""
    speak('What would you like me to remember? Please state the key, for example: my name.')
    key = get_input('User data key:')
    if not key or key in ('none', 'None'):
        speak('I did not catch the key. Cancelled.')
        return
    key = normalize_key(key)
    speak(f'What is the value for {key}?')
    value = get_input('User data value:')
    if not value or value in ('none', 'None'):
        speak('I did not catch the value. Cancelled.')
        return
    val = value.strip()
    user_data[key] = val
    save_user_data()
    log_action({'type': 'action', 'action': 'remember', 'key': key, 'value': val})
    speak(f'I will remember {key} as {user_data[key]}.')

def get_user_data(key=None):
    """Return a stored value for a key; prompt if no key provided."""
    if not key:
        speak('Which piece of user data would you like me to recall?')
        key = takeCommand()
        if not key or key in ('none', 'None'):
            speak('I did not catch the key.')
            return None
    lookup = normalize_key(key)
    logging.debug(f'get_user_data lookup: "{lookup}" available keys: {list(user_data.keys())}')

    # Exact match
    if lookup in user_data:
        val = user_data[lookup]
        log_action({'type': 'action', 'action': 'recall', 'key': lookup, 'result': 'found'})
        return val

    # Substring or reversed match: try key in stored-key or stored-key in key
    for k, v in user_data.items():
        nk = normalize_key(k)
        if lookup and (lookup in nk or nk in lookup):
            log_action({'type': 'action', 'action': 'recall', 'key': k, 'matched_with': lookup, 'result': 'found'})
            return v

    # Try matching within values
    for k, v in user_data.items():
        try:
            if isinstance(v, str) and lookup in v.lower():
                log_action({'type': 'action', 'action': 'recall', 'key': k, 'matched_value': True, 'result': 'found'})
                return v
        except Exception:
            pass

    log_action({'type': 'action', 'action': 'recall', 'key': lookup, 'result': 'not_found'})
    return None

def forget_user_data_via_voice():
    speak('Which key should I forget?')
    key = get_input('Key to forget:')
    if not key or key in ('none', 'None'):
        speak('I did not catch the key.')
        return
    lookup = normalize_key(key)
    # build candidate lists
    candidates = []  # list of tuples (stored_key, normalized_stored_key)
    for stored_key in list(user_data.keys()):
        nk = normalize_key(stored_key)
        candidates.append((stored_key, nk))

    # try exact normalized match
    for stored_key, nk in candidates:
        if lookup and nk == lookup:
            val = user_data.pop(stored_key)
            save_user_data()
            log_action({'type': 'action', 'action': 'forget', 'key': stored_key, 'matched_with': lookup, 'result': 'removed'})
            speak(f'I forgot {stored_key}.')
            return

    # try exact raw key match
    for stored_key, nk in candidates:
        if key.strip().lower() == stored_key.strip().lower():
            val = user_data.pop(stored_key)
            save_user_data()
            log_action({'type': 'action', 'action': 'forget', 'key': stored_key, 'matched_with': key, 'result': 'removed'})
            speak(f'I forgot {stored_key}.')
            return

    # try substring matching
    for stored_key, nk in candidates:
        if lookup and (lookup in nk or nk in lookup):
            val = user_data.pop(stored_key)
            save_user_data()
            log_action({'type': 'action', 'action': 'forget', 'key': stored_key, 'matched_with': lookup, 'result': 'removed'})
            speak(f'I forgot {stored_key}.')
            return

    # try fuzzy by words
    lookup_words = set(lookup.split()) if lookup else set()
    for stored_key, nk in candidates:
        if lookup_words and lookup_words.intersection(set(nk.split())):
            val = user_data.pop(stored_key)
            save_user_data()
            log_action({'type': 'action', 'action': 'forget', 'key': stored_key, 'matched_with': lookup, 'result': 'removed'})
            speak(f'I forgot {stored_key}.')
            return

    # nothing matched: speak and log candidates for debugging
    speak('No matching user data found to forget.')
    log_action({'type': 'action', 'action': 'forget', 'key': lookup, 'result': 'not_found', 'candidates': [c[0] for c in candidates]})

def show_all_user_data():
    if not user_data:
        speak('I have no stored user data.')
        return
    speak(f'I have {len(user_data)} items saved. Here are the first five:')
    i = 0
    for k, v in user_data.items():
        i += 1
        speak(f'{k}: {v}')
        if i >= 5:
            break

def show_config():
    speak(f"Monitoring interval is {monitoring_interval} seconds.")
    speak(f"Thresholds: load average {thresholds['load_avg']}, memory used {thresholds['mem_used_pct']} percent, disk free {thresholds['disk_free_pct']} percent.")

def get_system_status():
    """Return dict with load, memory, and disk usage."""
    try:
        load1, load5, load15 = os.getloadavg()
    except Exception:
        load1 = load5 = load15 = 0.0

    # Read memory from /proc/meminfo (Linux)
    mem_total = mem_available = None
    try:
        with open('/proc/meminfo') as f:
            for line in f:
                if line.startswith('MemTotal:'):
                    mem_total = int(re.findall(r"\d+", line)[0])
                elif line.startswith('MemAvailable:'):
                    mem_available = int(re.findall(r"\d+", line)[0])
                if mem_total and mem_available:
                    break
        mem_used_pct = 0.0
        if mem_total and mem_available:
            mem_used_pct = (1 - (mem_available / mem_total)) * 100.0
    except Exception:
        mem_used_pct = 0.0

    # Disk usage
    try:
        du = shutil.disk_usage('/')
        disk_total = du.total
        disk_free = du.free
        disk_free_pct = (disk_free / disk_total) * 100.0 if disk_total else 0.0
    except Exception:
        disk_free_pct = 0.0

    status = {
        'load1': load1,
        'load5': load5,
        'load15': load15,
        'mem_used_pct': round(mem_used_pct, 1),
        'disk_free_pct': round(disk_free_pct, 1)
    }
    # add battery info if available
    try:
        batt = get_battery_info()
        status.update(batt)
    except Exception:
        pass
    return status

def get_battery_info():
    """Return battery percentage and charging status. Try psutil if available, else /sys/class/power_supply."""
    info = {'battery_pct': None, 'battery_charging': None}
    try:
        # Import psutil dynamically to avoid a hard dependency during static analysis
        psutil = None
        try:
            import importlib
            psutil = importlib.import_module('psutil')
        except Exception:
            psutil = None

        if psutil:
            batt = psutil.sensors_battery()
            if batt:
                info['battery_pct'] = round(batt.percent, 1)
                info['battery_charging'] = bool(batt.power_plugged)
                return info
    except Exception:
        pass

    # Fallback: try reading from /sys/class/power_supply
    try:
        base = '/sys/class/power_supply'
        if os.path.exists(base):
            for name in os.listdir(base):
                p = os.path.join(base, name)
                if os.path.isdir(p) and name.startswith('BAT'):
                    # try to read capacity and status
                    cap_path = os.path.join(p, 'capacity')
                    stat_path = os.path.join(p, 'status')
                    pct = None
                    charging = None
                    try:
                        with open(cap_path) as f:
                            pct = float(f.read().strip())
                    except Exception:
                        pct = None
                    try:
                        with open(stat_path) as f:
                            s = f.read().strip().lower()
                            charging = 'charging' in s or 'full' in s
                    except Exception:
                        charging = None
                    info['battery_pct'] = pct
                    info['battery_charging'] = charging
                    return info
    except Exception:
        pass

    return info

def set_battery_threshold_via_voice():
    speak('Please say the minimum battery percent to warn at, for example 20.')
    resp = get_input('Battery threshold percent:')
    if not resp or resp in ('none', 'None'):
        speak('No value provided.')
        return
    match = re.search(r"(\d{1,3})", resp)
    if match:
        val = int(match.group(1))
        thresholds['battery_low_pct'] = max(1, min(100, val))
        save_config()
        speak(f'Battery low threshold set to {thresholds["battery_low_pct"]} percent')
    else:
        speak('Could not understand the number. Please try again.')

def check_for_system_updates():
    """Try to list upgradable packages (non-destructive)."""
    try:
        # Prefer apt on Debian/Ubuntu
        proc = subprocess.run(['apt', 'list', '--upgradable'], capture_output=True, text=True)
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout.strip()
    except Exception:
        pass
    try:
        proc = subprocess.run(['dnf', 'check-update'], capture_output=True, text=True)
        if proc.returncode == 100 or (proc.returncode == 0 and proc.stdout):
            return proc.stdout.strip()
    except Exception:
        pass
    return 'Update check not supported on this system.'

def clear_temp_files(older_than_hours=24):
    """Remove files in /tmp older than specified hours. Non-recursive for safety."""
    try:
        now = time.time()
        cutoff = now - (older_than_hours * 3600)
        removed = 0
        for entry in os.scandir('/tmp'):
            try:
                stat = entry.stat()
                if stat.st_mtime < cutoff:
                    if entry.is_file() or entry.is_symlink():
                        os.remove(entry.path)
                        removed += 1
                    elif entry.is_dir():
                        # only remove empty directories
                        try:
                            os.rmdir(entry.path)
                            removed += 1
                        except Exception:
                            pass
            except Exception:
                pass
        speak(f"Removed {removed} temporary items from /tmp.")
    except Exception as e:
        print(f"Error clearing /tmp: {e}")
        speak('Failed to clear temporary files.')

def set_thresholds_via_voice():
    speak('Please say the maximum 1-minute load average to warn at, or say skip.')
    resp = get_input('Load average threshold or skip:')
    if resp and resp not in ('none', 'None', 'skip'):
        match = re.search(r"(\d+(?:\.\d+)?)", resp)
        if match:
            thresholds['load_avg'] = float(match.group(1))

    speak('Please say the maximum memory used percent to warn at, or say skip.')
    resp = get_input('Memory used percent threshold or skip:')
    if resp and resp not in ('none', 'None', 'skip'):
        match = re.search(r"(\d+(?:\.\d+)?)", resp)
        if match:
            thresholds['mem_used_pct'] = float(match.group(1))

    speak('Please say the minimum disk free percent to warn at, or say skip.')
    resp = get_input('Disk free percent threshold or skip:')
    if resp and resp not in ('none', 'None', 'skip'):
        match = re.search(r"(\d+(?:\.\d+)?)", resp)
        if match:
            thresholds['disk_free_pct'] = float(match.group(1))

    save_config()

def monitor_resources():
    """Background thread that monitors resources and speaks warnings."""
    global monitoring_active
    while monitoring_active:
        status = get_system_status()
        print('Resource status:', status)
        if status['load1'] > thresholds['load_avg']:
            speak(f"Warning: high load average {status['load1']:.1f}")
        if status['mem_used_pct'] >= thresholds['mem_used_pct']:
            speak(f"Warning: memory usage {status['mem_used_pct']} percent")
        if status['disk_free_pct'] <= thresholds['disk_free_pct']:
            speak(f"Warning: disk free only {status['disk_free_pct']} percent")
        time.sleep(monitoring_interval)

def enable_monitoring():
    global monitoring_active, monitoring_thread
    if monitoring_active:
        speak('Monitoring is already active.')
        return
    monitoring_active = True
    monitoring_thread = threading.Thread(target=monitor_resources, daemon=True)
    monitoring_thread.start()
    speak('Resource monitoring enabled.')

def disable_monitoring():
    global monitoring_active
    if not monitoring_active:
        speak('Monitoring is not active.')
        return
    monitoring_active = False
    speak('Resource monitoring disabled.')

def close_browser():
    browsers = ["firefox", "chrome", "brave", "google-chrome", "chromium", "firefox-bin"]
    closed = False
    for browser in browsers:
        result = os.system(f"pkill -f {browser}")
        if result == 0:
            closed = True
    speak("Browser closed successfully." if closed else "No supported browser seems to be running.")

def create_folder():
    speak("What would you like to name the new folder?")
    folder_name = get_input('Folder name:')
    if not folder_name or folder_name in ("none", "None"):
        speak("I didn't catch the folder name. Please try again.")
        return

    # sanitize name
    folder_name = re.sub(r"\b(create|make|new|folder|directory)\b", '', folder_name).strip()
    if not folder_name:
        speak("Please provide a valid folder name.")
        return

    folder_name = re.sub(r'[^\w\s-]', '', folder_name).strip().replace(' ', '_')
    target_path = os.path.join(os.getcwd(), folder_name)
    try:
        os.makedirs(target_path, exist_ok=False)
        speak(f"Folder '{folder_name}' created at {target_path}")
    except FileExistsError:
        speak(f"A folder named '{folder_name}' already exists.")
    except Exception as e:
        print(f"Error creating folder: {e}")
        speak("Sorry, I couldn't create the folder.")

def delete_folder():
    speak("Which folder would you like to delete?")
    folder_name = get_input('Folder name to delete:')
    if not folder_name or folder_name in ("none", "None"):
        speak("I didn't catch the folder name. Please try again.")
        return

    folder_name = re.sub(r"\b(delete|remove|folder|directory)\b", '', folder_name).strip()
    if not folder_name:
        speak("Please provide a valid folder name.")
        return

    folder_name = folder_name.replace(' ', '_')
    # search common locations
    search_locations = [os.getcwd(), os.path.expanduser('~'), os.path.expanduser('~/Documents'), os.path.expanduser('~/Desktop')]
    found = []
    for loc in search_locations:
        candidate = os.path.join(loc, folder_name)
        if os.path.isdir(candidate):
            found.append(candidate)

    if not found:
        speak(f"Could not find a folder named {folder_name} in common locations.")
        return

    if len(found) > 1:
        speak(f"I found multiple folders named {folder_name}. Please delete them manually.")
        return

    folder_path = found[0]
    try:
        if not os.listdir(folder_path):
            os.rmdir(folder_path)
            speak(f"Folder '{folder_name}' deleted.")
        else:
            shutil.rmtree(folder_path)
            speak(f"Folder '{folder_name}' and its contents were deleted.")
    except Exception as e:
        print(f"Error deleting folder: {e}")
        speak("Sorry, I couldn't delete the folder. There might be a permission issue.")

def create_file():
    speak("What would you like to name the file?")
    file_name = get_input('File name:')
    if not file_name or file_name in ("none", "None"):
        speak("I didn't catch the file name. Please try again.")
        return

    file_name = re.sub(r"\b(create|make|new|file|text|document)\b", '', file_name).strip()
    if not file_name:
        speak("Please provide a valid file name.")
        return

    if not any(file_name.endswith(ext) for ext in ('.txt', '.py', '.md', '.html', '.js', '.css')):
        file_name = file_name + '.txt'

    file_name = re.sub(r'[^\w\s.\-]', '', file_name).replace(' ', '_')
    target_path = os.path.join(os.getcwd(), file_name)
    try:
        with open(target_path, 'w') as f:
            f.write(f"File created by voice assistant on {datetime.datetime.now().isoformat()}\n")
        speak(f"File '{file_name}' created at {target_path}")
    except Exception as e:
        print(f"Error creating file: {e}")
        speak("Sorry, I couldn't create the file.")

def delete_file():
    speak("Which file would you like to delete?")
    file_name = get_input('File name to delete:')
    if not file_name or file_name in ("none", "None"):
        speak("I didn't catch the file name. Please try again.")
        return

    file_name = re.sub(r"\b(delete|remove|file|document)\b", '', file_name).strip()
    if not file_name:
        speak("Please provide a valid file name.")
        return

    # search current directory and home
    candidates = []
    for root in (os.getcwd(), os.path.expanduser('~')):
        for f in os.listdir(root):
            if file_name.lower() in f.lower():
                candidates.append(os.path.join(root, f))

    if not candidates:
        speak(f"No files found matching {file_name} in current or home directory.")
        return

    if len(candidates) > 1:
        speak(f"I found multiple files. I will delete the first match: {os.path.basename(candidates[0])}")

    try:
        os.remove(candidates[0])
        speak(f"File {os.path.basename(candidates[0])} deleted.")
    except Exception as e:
        print(f"Error deleting file: {e}")
        speak("Sorry, I couldn't delete the file.")

def read_file():
    global stop_reading
    stop_reading = False
    speak("Which file would you like me to read? Please say the file name.")
    file_name = get_input('File name to read:')
    if not file_name or file_name in ("none", "None"):
        speak("I didn't catch the file name. Please try again.")
        return

    file_name = re.sub(r"\b(read|open|file|show)\b", '', file_name).strip()
    if not file_name:
        speak("Please provide a valid file name.")
        return

    # Search common locations shallowly
    search_locations = [os.getcwd(), os.path.expanduser('~'), os.path.expanduser('~/Documents'), os.path.expanduser('~/Desktop')]
    found_files = []
    for loc in search_locations:
        if os.path.exists(loc):
            for root, dirs, files in os.walk(loc):
                for f in files:
                    if file_name.lower() in f.lower():
                        found_files.append(os.path.join(root, f))
                break

    if not found_files:
        speak(f"Sorry, I couldn't find a file named {file_name} in common locations.")
        return

    if len(found_files) > 1:
        speak(f"I found multiple files. I will read the first match: {os.path.basename(found_files[0])}")

    file_path = found_files[0]
    try:
        if os.path.getsize(file_path) > 200000:  # avoid huge files
            speak("The file is too large to read aloud. I can only read small text files.")
            return

        with open(file_path, 'r', errors='ignore') as f:
            content = f.read()

        if not content.strip():
            speak("The file is empty.")
            return

        speak(f"Reading contents of {os.path.basename(file_path)}. Say 'stop' at any time to stop.")
        # Split into sentences for interruptible reading
        sentences = re.split(r'[.!?]+', content)
        sentences = [s.strip() for s in sentences if s.strip()]
        for sentence in sentences:
            if stop_reading:
                speak("Stopped reading.")
                stop_reading = False
                return
            engine.say(sentence)
            engine.runAndWait()
            # brief window to listen for stop
            if listen_for_stop_command(timeout=1):
                stop_speaking()
                stop_reading = True
                speak("Stopped reading.")
                return
            time.sleep(0.2)
        speak("Finished reading the file.")
    except Exception as e:
        print(f"Error reading file: {e}")
        speak("Sorry, I couldn't read the file.")

def search_files():
    speak("What file or type of file are you looking for?")
    search_term = get_input('Search term:')
    if not search_term or search_term in ("none", "None"):
        speak("I didn't catch the search term. Please try again.")
        return

    search_term = re.sub(r"\b(find|search|look for|file)\b", '', search_term).strip()
    if not search_term:
        speak("Please tell me what to search for.")
        return

    speak(f"Searching for files containing {search_term}...")
    search_locations = [os.getcwd(), os.path.expanduser('~'), os.path.expanduser('~/Documents')]
    found_files = []
    max_results = 5
    for loc in search_locations:
        if os.path.exists(loc):
            try:
                for root, dirs, files in os.walk(loc):
                    for f in files:
                        if search_term.lower() in f.lower():
                            found_files.append(os.path.join(root, f))
                            if len(found_files) >= max_results:
                                break
                    if len(found_files) >= max_results:
                        break
            except Exception as e:
                print(f"Search error in {loc}: {e}")

    if found_files:
        speak(f"I found {len(found_files)} files:")
        for i, path in enumerate(found_files[:3], 1):
            speak(f"File {i}: {os.path.basename(path)} in {os.path.basename(os.path.dirname(path))}")
        if len(found_files) > 3:
            speak(f"... and {len(found_files) - 3} more files.")
    else:
        speak(f"No files found containing {search_term}.")

def list_files_in_directory():
    speak("Which directory would you like to list files from? Say 'current', 'desktop', 'documents', or 'downloads'.")
    location = get_input('Directory (current/desktop/documents/downloads):')
    if not location or location in ("none", "None"):
        speak("I'll show files in the current directory.")
        location = 'current'

    location_map = {
        'current': os.getcwd(),
        'desktop': os.path.expanduser('~/Desktop'),
        'documents': os.path.expanduser('~/Documents'),
        'downloads': os.path.expanduser('~/Downloads')
    }
    target_dir = location_map.get(location, os.getcwd())
    if not os.path.exists(target_dir):
        speak(f"The {location} directory doesn't exist.")
        return

    try:
        items = os.listdir(target_dir)
        files = [item for item in items if os.path.isfile(os.path.join(target_dir, item))]
        folders = [item for item in items if os.path.isdir(os.path.join(target_dir, item))]
        speak(f"Found {len(folders)} folders and {len(files)} files in {location} directory.")
        if folders:
            speak(f"Folders: {', '.join(folders[:5])}")
        if files:
            speak(f"Files: {', '.join(files[:5])}")
    except Exception as e:
        print(f"Error listing directory: {e}")
        speak("Sorry, I couldn't list the directory contents.")

def list_folders():
    current_dir = os.getcwd()
    try:
        folders = [f for f in os.listdir(current_dir) if os.path.isdir(os.path.join(current_dir, f))]
        if folders:
            speak(f"I found {len(folders)} folders in current directory: {', '.join(folders[:5])}")
        else:
            speak("No folders found in current directory.")
    except Exception as e:
        print(f"Error listing folders: {e}")
        speak("Sorry, I couldn't list the folders.")

def _detect_volume_backend():
    # prefer pactl/pamixer/amixer
    if shutil.which('pactl'):
        return 'pactl'
    if shutil.which('pamixer'):
        return 'pamixer'
    if shutil.which('amixer'):
        return 'amixer'
    return None

def _get_current_volume(backend=None):
    backend = backend or _detect_volume_backend()
    try:
        if backend == 'pactl':
            out = subprocess.check_output(['pactl', 'get-sink-volume', '@DEFAULT_SINK@'], text=True)
            m = re.search(r'\s(\d+)%', out)
            if m:
                return int(m.group(1))
        elif backend == 'pamixer':
            out = subprocess.check_output(['pamixer', '--get-volume'], text=True)
            return int(float(out.strip()))
        elif backend == 'amixer':
            out = subprocess.check_output(['amixer', 'get', 'Master'], text=True)
            m = re.search(r"(\d+)%", out)
            if m:
                return int(m.group(1))
    except Exception:
        pass
    return None

def set_volume(percent):
    percent = max(0, min(100, int(percent)))
    backend = _detect_volume_backend()
    try:
        if backend == 'pactl':
            subprocess.run(['pactl', 'set-sink-volume', '@DEFAULT_SINK@', f'{percent}%'])
        elif backend == 'pamixer':
            subprocess.run(['pamixer', '--set-volume', str(percent)])
        elif backend == 'amixer':
            subprocess.run(['amixer', 'set', 'Master', f'{percent}%'])
        else:
            speak('No supported volume control found on this system.')
            return
        speak(f'Volume set to {percent} percent.')
    except Exception as e:
        print(f'Volume set error: {e}')
        speak('Failed to set volume.')

def change_volume(delta):
    backend = _detect_volume_backend()
    cur = _get_current_volume(backend)
    if cur is None:
        speak('Could not determine current volume.')
        return
    set_volume(cur + delta)

def volume_up(step=10):
    change_volume(step)

def volume_down(step=10):
    change_volume(-step)

def mute():
    backend = _detect_volume_backend()
    try:
        if backend == 'pactl':
            subprocess.run(['pactl', 'set-sink-mute', '@DEFAULT_SINK@', '1'])
        elif backend == 'pamixer':
            subprocess.run(['pamixer', '--mute'])
        elif backend == 'amixer':
            subprocess.run(['amixer', 'set', 'Master', 'mute'])
        else:
            speak('No supported mute control found.')
            return
        speak('Muted.')
    except Exception as e:
        print(f'Mute error: {e}')
        speak('Failed to mute.')

def unmute():
    backend = _detect_volume_backend()
    try:
        if backend == 'pactl':
            subprocess.run(['pactl', 'set-sink-mute', '@DEFAULT_SINK@', '0'])
        elif backend == 'pamixer':
            subprocess.run(['pamixer', '--unmute'])
        elif backend == 'amixer':
            subprocess.run(['amixer', 'set', 'Master', 'unmute'])
        else:
            speak('No supported unmute control found.')
            return
        speak('Unmuted.')
    except Exception as e:
        print(f'Unmute error: {e}')
        speak('Failed to unmute.')

def open_app(app_name=None):
    """Open an application by command or desktop name."""
    if not app_name:
        speak('Which application would you like to open?')
        app_name = get_input('Application to open:')
        if not app_name or app_name in ('none', 'None'):
            speak('No application name provided.')
            return
    # sanitize
    app_name = app_name.strip()
    # try as command
    cmd = app_name.split()
    if shutil.which(cmd[0]):
        try:
            subprocess.Popen(cmd)
            speak(f'Launched {cmd[0]}.')
            return
        except Exception as e:
            print(f'Error launching {cmd}: {e}')
    # try xdg-open for files/urls
    try:
        subprocess.Popen(['xdg-open', app_name])
        speak(f'Opened {app_name}.')
        return
    except Exception:
        pass
    speak(f'Could not open {app_name}.')

def close_app(app_name=None):
    if not app_name:
        speak('Which application should I close?')
        app_name = get_input('Application to close:')
        if not app_name or app_name in ('none', 'None'):
            speak('No application name provided.')
            return
    app_name = app_name.strip()
    try:
        # try pkill -f
        subprocess.run(['pkill', '-f', app_name])
        speak(f'Tried to close {app_name}.')
    except Exception as e:
        print(f'Error closing app: {e}')
        speak('Failed to close the application.')

def get_weather(city_name=None):
    """Get weather information for a city using OpenWeatherMap."""
    try:
        if not city_name:
            speak("Which city's weather would you like to know?")
            city_name = get_input('City for weather:')
            if not city_name or city_name == "none" or city_name == "None":
                speak("I'll check the weather for your current location.")
                city_name = "London"

        # Clean up city name
        city_name = re.sub(r'\b(weather|in|for|city|of|the)\b', '', city_name).strip()
        if not city_name:
            city_name = "London"

        speak(f"Getting weather information for {city_name}...")

        params = {
            'q': city_name,
            'appid': WEATHER_API_KEY,
            'units': 'metric'
        }

        response = requests.get(WEATHER_API_URL, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return format_weather_data(data)
        else:
            print('Weather API response:', response.status_code, response.text)
            return f"Sorry, I couldn't fetch weather data for {city_name}. Please check the city name."
    except requests.exceptions.RequestException as e:
        print(f"Weather API error: {e}")
        return "Sorry, I'm having trouble connecting to the weather service."
    except Exception as e:
        print(f"Weather error: {e}")
        return "Sorry, I couldn't get the weather information at the moment."

def format_weather_data(data):
    try:
        city = data.get('name')
        country = data.get('sys', {}).get('country')
        temp = data.get('main', {}).get('temp')
        feels_like = data.get('main', {}).get('feels_like')
        humidity = data.get('main', {}).get('humidity')
        weather_desc = data.get('weather', [{}])[0].get('description')
        wind_speed = data.get('wind', {}).get('speed')

        weather_report = (
            f"Current weather in {city}, {country}: "
            f"{weather_desc}. Temperature is {temp:.1f}°C, "
            f"feels like {feels_like:.1f}°C. "
            f"Humidity is {humidity}% and wind speed is {wind_speed} meters per second."
        )
        return weather_report
    except Exception as e:
        print(f"Error formatting weather data: {e}")
        return "Sorry, I couldn't process the weather information."

def get_weather_forecast(city_name=None):
    try:
        if not city_name:
            speak("Which city's forecast would you like to know?")
            city_name = get_input('City for forecast:')
            if not city_name or city_name == "none" or city_name == "None":
                city_name = "London"

        city_name = re.sub(r'\b(weather|forecast|in|for|city|of|the)\b', '', city_name).strip()

        params = {
            'q': city_name,
            'appid': WEATHER_API_KEY,
            'units': 'metric'
        }

        response = requests.get(FORECAST_API_URL, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return format_forecast_data(data)
        else:
            print('Forecast API response:', response.status_code, response.text)
            return f"Sorry, I couldn't fetch the forecast for {city_name}."
    except Exception as e:
        print(f"Forecast error: {e}")
        return "Sorry, I couldn't get the weather forecast."

def format_forecast_data(data):
    try:
        city = data.get('city', {}).get('name')
        country = data.get('city', {}).get('country')

        # Get forecast for next 12 hours (every 3 hours entries)
        forecasts = []
        for i, item in enumerate(data.get('list', [])[:4]):
            time_str = item.get('dt_txt')
            temp = item.get('main', {}).get('temp')
            desc = item.get('weather', [{}])[0].get('description')
            time_obj = datetime.datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S')
            time_readable = time_obj.strftime('%I %p')
            forecasts.append(f"{time_readable}: {temp:.1f}°C with {desc}")

        forecast_text = f"12-hour forecast for {city}, {country}: " + ". ".join(forecasts)
        return forecast_text
    except Exception as e:
        print(f"Error formatting forecast: {e}")
        return "Sorry, I couldn't process the forecast information."

class VLCMusicController:
    def __init__(self):
        self.instance = vlc.Instance('--quiet')
        self.player = self.instance.media_list_player_new()
        self.list = self.instance.media_list_new()
        self.player.set_media_list(self.list)
        self.tracks = []
        self.loaded = False

    def load_music(self, directory):
        self.tracks = [os.path.join(directory, f) for f in os.listdir(directory) if f.endswith('.mp3')]
        if not self.tracks:
            return False
        for track in self.tracks:
            media = self.instance.media_new(track)
            self.list.add_media(media)
        self.loaded = True
        return True

    def play(self):
        if self.loaded:
            self.player.play()
            speak("Playing music.")
        else:
            speak("No music loaded. Say 'play music' first.")

    def pause(self):
        self.player.pause()
        speak("Music paused.")

    def resume(self):
        self.player.play()
        speak("Music resumed.")

    def next_track(self):
        self.player.next()
        self.player.play()
        speak("Playing next track.")

    def stop(self):
        self.player.stop()
        speak("Music stopped.")

controller = VLCMusicController()

def play_music():
    music_dir = '/home/mr-blackdevil/Music/songs'
    if controller.load_music(music_dir):
        speak(f"Songs found: {[os.path.basename(song) for song in controller.tracks]}")
        controller.play()
    else:
        speak("No MP3 files found in your music folder.")

def play_alarm_sound():
    global alarm_sound
    try:
        sound_file = "/usr/share/sounds/ubuntu/notifications/Mallet.ogg"
        speak("Wake up! This is your alarm.")

        # Prefer system audio players to avoid locking PyAudio device
        player_cmd = None
        if os.path.exists(sound_file):
            if shutil.which('paplay'):
                player_cmd = ['paplay', sound_file]
            elif shutil.which('aplay'):
                player_cmd = ['aplay', sound_file]

        if player_cmd:
            proc = None
            for _ in range(5):
                if alarm_cancel:
                    break
                try:
                    proc = subprocess.Popen(player_cmd)
                    # let it play briefly, then continue loop (non-blocking)
                    time.sleep(2)
                except Exception as e:
                    print(f"Error launching player: {e}")
                    break
            # ensure process cleaned up
            try:
                if proc and proc.poll() is None:
                    proc.terminate()
            except Exception:
                pass
        else:
            # Fallback to pygame mixer but ensure we stop and re-init after
            try:
                if os.path.exists(sound_file):
                    alarm_sound = mixer.Sound(sound_file)
                else:
                    # small silent buffer as fallback
                    alarm_sound = mixer.Sound(buffer=bytearray([0x80] * 8000 * 1))

                for _ in range(5):
                    if alarm_cancel:
                        break
                    alarm_sound.play()
                    time.sleep(2)
            except Exception as e:
                print("Error playing alarm sound via mixer:", e)
                speak("Wake up! This is your alarm.")
    except Exception as e:
        print("Error playing alarm sound:", e)
        speak("Wake up! This is your alarm.")
    finally:
        # Stop and reinitialize pygame mixer to avoid leaving audio device locked
        try:
            mixer.stop()
        except Exception:
            pass
        try:
            mixer.quit()
        except Exception:
            pass
        try:
            mixer.init()
        except Exception:
            pass

def set_alarm(alarm_time):
    global alarm_cancel
    speak(f"Alarm set for {alarm_time}")
    while True:
        if alarm_cancel:
            speak("Alarm cancelled.")
            alarm_cancel = False
            return
        current_time = datetime.datetime.now().strftime("%H:%M")
        if current_time == alarm_time:
            play_alarm_sound()
            return
        time.sleep(10)  # Check every 10 seconds to reduce CPU usage

def set_timer(seconds):
    global timer_cancel
    try:
        seconds = int(seconds)
        if seconds <= 0:
            speak("Timer duration must be positive.")
            return
            
        speak(f"Timer set for {seconds} seconds.")
        start_time = time.time()
        
        while True:
            if timer_cancel:
                speak("Timer cancelled.")
                timer_cancel = False
                return
                
            elapsed = time.time() - start_time
            remaining = seconds - elapsed
            
            if remaining <= 0:
                speak("Time's up!")
                play_alarm_sound()  # Use the same alarm sound for timer
                return
                
            # Announce remaining time at intervals
            if remaining > 60 and remaining % 60 == 0:
                mins = int(remaining // 60)
                speak(f"{mins} minute{'s' if mins > 1 else ''} remaining.")
            elif 10 < remaining <= 60 and remaining % 30 == 0:
                speak(f"{int(remaining)} seconds remaining.")
            elif remaining <= 10:
                speak(str(int(remaining)))
                
            time.sleep(1)
    except Exception as e:
        print("Timer error:", e)
        speak("Sorry, there was an error with the timer.")

def parse_time_input(text):
    """Parse natural language time input into 24-hour HH:MM format"""
    try:
        # Handle formats like "7:30", "7 30", "07 45", etc.
        match = re.search(r"(\d{1,2})[:\s]?(\d{2})", text)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2))

            
            # Convert to 24-hour format
            if "pm" in text and hour < 12:
                hour += 12
            elif "am" in text and hour == 12:
                hour = 0
                
            return f"{hour:02d}:{minute:02d}"
        
        # Handle formats like "seven thirty", "seven thirty pm", etc.
        time_words = {
            'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
            'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
            'eleven': 11, 'twelve': 12, 'thirteen': 13, 'fourteen': 14,
            'fifteen': 15, 'sixteen': 16, 'seventeen': 17, 'eighteen': 18,
            'nineteen': 19, 'twenty': 20, 'thirty': 30, 'forty': 40, 'fifty': 50
        }
        
        words = re.findall(r"\w+", text.lower())
        hour = 0
        minute = 0
        is_pm = False
        
        for i, word in enumerate(words):
            if word in time_words:
                if hour == 0:
                    hour = time_words[word]
                else:
                    minute = time_words[word]
            elif word == 'pm':
                is_pm = True
            elif word == 'am':
                is_pm = False
            elif word == 'o''clock':
                minute = 0
            elif word == 'thirty':
                minute = 30
            elif word == 'fifteen':
                minute = 15
            elif word == 'forty':
                minute = 40
            elif word == 'forty-five':
                minute = 45
            elif word == 'fifty':
                minute = 50
        
        if is_pm and hour < 12:
            hour += 12
        elif not is_pm and hour == 12:
            hour = 0
            
        return f"{hour:02d}:{minute:02d}"
    
    except Exception as e:
        print("Time parsing error:", e)
        return None

def parse_duration_input(text):
    """Parse natural language duration into seconds"""
    try:
        total_seconds = 0
        
        # Handle formats like "2 minutes 30 seconds", "1 hour 5 minutes", etc.
        time_units = {
            'second': 1,
            'seconds': 1,
            'minute': 60,
            'minutes': 60,
            'hour': 3600,
            'hours': 3600
        }
        
        matches = re.findall(r"(\d+)\s*(second|seconds|minute|minutes|hour|hours)", text)
        for value, unit in matches:
            total_seconds += int(value) * time_units[unit.lower()]
        
        # Handle simple numbers (assume minutes if no unit specified)
        if total_seconds == 0:
            numbers = re.findall(r"\d+", text)
            if numbers:
                total_seconds = int(numbers[0]) * 60  # Default to minutes
        
        return total_seconds if total_seconds > 0 else None
    
    except Exception as e:
        print("Duration parsing error:", e)
        return None

def wait_for_wake_word():
    # Try porcupine first (low-latency keyword spotting). If initialization
    # or runtime fails (missing libs, device errors, invalid access key),
    # fall back to a simple recognizer-based loop that listens for the
    # keyword via Google's recognizer. The fallback is less efficient but
    # works on systems without porcupine or when audio capture setup fails.
    keyword = 'jarvis'
    try:
        porcupine = pvporcupine.create(
            access_key="N60wWGI6AjfAFSH26lORbi78w42Rvak+VQPE50w8nFfADIV8PyJ0cg==",
            keywords=[keyword]
        )
        pa = pyaudio.PyAudio()
        audio_stream = pa.open(rate=porcupine.sample_rate, channels=1, format=pyaudio.paInt16,
                               input=True, frames_per_buffer=porcupine.frame_length)

        print("Listening for wake word (Porcupine)...")
        try:
            while True:
                try:
                    pcm_bytes = audio_stream.read(porcupine.frame_length, exception_on_overflow=False)
                    pcm = struct.unpack_from('h' * porcupine.frame_length, pcm_bytes)
                    keyword_index = porcupine.process(pcm)
                    if keyword_index >= 0:
                        print("Wake word detected (Porcupine)!")
                        break
                except IOError:
                    # audio read hiccup — continue reading
                    continue
        finally:
            try:
                audio_stream.close()
            except Exception:
                pass
            try:
                pa.terminate()
            except Exception:
                pass
            try:
                porcupine.delete()
            except Exception:
                pass
        return
    except Exception as e:
        logging.exception('Porcupine wake-word init failed, falling back to recognizer: %s', e)

    # Fallback: use speech_recognition to listen in short segments and look
    # for the keyword in recognized text. This is more CPU/network heavy but
    # works without porcupine.
    print('Porcupine unavailable — listening for wake word via SpeechRecognition...')
    r = sr.Recognizer()
    mic = None
    try:
        mic = sr.Microphone()
    except Exception as e:
        logging.exception('Microphone initialization failed: %s', e)
        # Give up if no microphone available
        raise

    with mic as source:
        r.adjust_for_ambient_noise(source, duration=1)
        print('Listening for wake word (recognizer)...')
        while True:
            try:
                audio = r.listen(source, timeout=5, phrase_time_limit=4)
                try:
                    text = r.recognize_google(audio, language='en-in')
                    print('Heard (fallback):', text)
                    if keyword in text.lower():
                        print('Wake word detected (fallback)!')
                        break
                except sr.UnknownValueError:
                    continue
                except sr.RequestError as re:
                    logging.exception('Recognizer request failed: %s', re)
                    # If recognizer can't reach service, continue but don't crash
                    continue
            except sr.WaitTimeoutError:
                # timed out waiting — loop again
                continue
def process_query(query: str) -> bool:
    """Process a single user query string.

    Returns True when the main loop should exit, False otherwise.
    """
    global alarm_thread, timer_thread, alarm_cancel, timer_cancel

    if not query:
        return False

    # Keep the original command handlers from the main loop.
    if 'wikipedia' in query:
        speak('Searching Wikipedia...')
        topic = query.replace("wikipedia", "").strip()
        if not topic:
            speak("Please tell me what you want to search on Wikipedia.")
            return False
        try:
            results = wikipedia.summary(topic, sentences=2)
            speak("According to Wikipedia")
            print(results)
            speak(results)
        except Exception as e:
            print(e)
            speak("Sorry, I could not find any results on Wikipedia.")
        return False

    # Weather Commands
    if 'weather' in query and 'forecast' not in query:
        if offline_mode.is_offline():
            speak('Sorry, weather lookup requires online mode. Please switch to online mode.')
            return False
        if 'in' in query:
            city_name = query.split('in')[-1].strip()
            weather_info = get_weather(city_name)
        else:
            weather_info = get_weather()
        speak(weather_info)
        return False

    if 'weather forecast' in query or 'forecast' in query:
        if offline_mode.is_offline():
            speak('Sorry, forecast lookup requires online mode. Please switch to online mode.')
            return False
        if 'in' in query:
            city_name = query.split('in')[-1].strip()
            forecast_info = get_weather_forecast(city_name)
        else:
            forecast_info = get_weather_forecast()
        speak(forecast_info)
        return False

    if 'temperature' in query or 'how hot' in query or 'how cold' in query:
        if 'in' in query:
            city_name = query.split('in')[-1].strip()
            weather_info = get_weather(city_name)
        else:
            weather_info = get_weather()
        speak(weather_info)
        return False

    if 'stop wikipedia' in query or 'cancel wikipedia' in query:
        speak("Okay, skipping Wikipedia lookup.")
        return False

    if 'open youtube' in query:
        if offline_mode.is_offline():
            speak('Cannot open websites while offline.')
        else:
            webbrowser.open("youtube.com")
        return False

    if 'open google' in query:
        if offline_mode.is_offline():
            speak('Cannot open websites while offline.')
        else:
            webbrowser.open("google.com")
        return False

    if 'open github' in query:
        if offline_mode.is_offline():
            speak('Cannot open websites while offline.')
        else:
            webbrowser.open("github.com")
        return False

    if 'open wikipedia' in query:
        webbrowser.open("wikipedia.org")
        return False

    for site_phrase, url in (('open deepseek', 'deepseek.com'), ('open bard', 'bard.google.com'),
                            ('open chatgpt', 'chat.openai.com'), ('open midjourney', 'midjourney.com'),
                            ('open gmail', 'mail.google.com'), ('google drive', 'drive.google.com'),
                            ('open google meet', 'meet.google.com'), ('open gemini', 'gemini.com')):
        if site_phrase in query:
            webbrowser.open(url)
            return False

    if 'open firefox' in query:
        webbrowser.open("firefox.com")
        return False
    if 'open google calender.com' in query or 'open google calendar' in query:
        webbrowser.open("calendar.google.com")
        return False 
    if 'open stackoverflow' in query:
        webbrowser.open("stackoverflow.com")
        return False

    if 'close google' in query or 'close youtube' in query or 'close browser' in query:
        close_browser()
        return False

    if 'play music' in query:
        play_music()
        return False

    if 'pause music' in query:
        controller.pause()
        return False

    if 'resume music' in query:
        controller.resume()
        return False

    if 'next song' in query or 'next track' in query:
        controller.next_track()
        return False

    if 'stop music' in query:
        controller.stop()
        return False

    if 'the time' in query:
        strTime = datetime.datetime.now().strftime("%H:%M:%S")
        speak(f"The time is {strTime}")
        return False

    if 'open code' in query:
        codePath = "/usr/bin/code"
        os.system(f"{codePath} &")
        return False

    if 'create folder' in query or 'make folder' in query or 'new folder' in query:
        create_folder()
        return False

    if 'delete folder' in query or 'remove folder' in query:
        delete_folder()
        return False

    if 'create file' in query or 'make file' in query or 'new file' in query:
        create_file()
        return False

    if 'delete file' in query or 'remove file' in query:
        delete_file()
        return False

    if 'read file' in query or 'open file' in query:
        read_file()
        return False

    if 'search file' in query or 'find file' in query or 'look for file' in query:
        search_files()
        return False

    if 'list files' in query or 'show files' in query:
        list_files_in_directory()
        return False

    if 'list folders' in query:
        list_folders()
        return False

    if 'volume up' in query or 'increase volume' in query:
        # optional percent
        m = re.search(r"(\d+)%?", query)
        step = int(m.group(1)) if m else 10
        volume_up(step)
        return False

    if 'volume down' in query or 'decrease volume' in query:
        m = re.search(r"(\d+)%?", query)
        step = int(m.group(1)) if m else 10
        volume_down(step)
        return False

    if 'mute' in query and 'unmute' not in query:
        mute()
        return False

    if 'unmute' in query:
        unmute()
        return False

    if 'start conversation' in query or 'conversation mode' in query or 'chat with me' in query:
        # Enter safe conversational mode: non-operational, just chit-chat
        try:
            # Pass the assistant's unified input function so conversation mode
            # uses text-mode provider when available.
            conversation_mode.start_conversation(speak, get_input)
        except Exception as e:
            logging.exception('Conversation mode failed: %s', e)
            speak('Failed to start conversation mode.')
        return False

    # Allow entering text mode via voice
    if 'text mode' in query or 'text more' in query or 'enter text' in query:
        try:
            import text_mode
            text_mode.start_text_mode(speak, process_query)
        except Exception as e:
            logging.exception('Text mode failed: %s', e)
            speak('Failed to start text mode.')
        return False

    # Calendar commands
    if 'create event' in query or 'schedule event' in query or ('create' in query and 'event' in query):
        create_event_via_voice()
        return False

    if 'view events' in query or 'show events' in query or 'list events' in query:
        # detect range
        if 'daily' in query or 'today' in query:
            view_events('daily')
        elif 'weekly' in query or 'this week' in query:
            view_events('weekly')
        elif 'monthly' in query or 'this month' in query:
            view_events('monthly')
        else:
            view_events('upcoming')
        return False

    if 'list my events' in query or 'list all events' in query or ('list' in query and 'my events' in query):
        list_events()
        return False

    if 'edit event' in query or 'modify event' in query:
        edit_event_via_voice()
        return False

    if 'delete event' in query or 'remove event' in query:
        delete_event_via_voice()
        return False

    if 'search event' in query or 'find event' in query or 'search events' in query:
        # ask for search term
        speak('What should I search for in events?')
        term = get_input('Event search term:')
        res = search_events(term)
        if not res:
            speak('No matching events found.')
        else:
            speak(f'Found {len(res)} events:')
            for e in res[:5]:
                speak(f"{e.get('id')}: {e.get('title')} at {e.get('when')}")
        return False

    if 'set reminder' in query or 'remind me' in query:
        # Simple flow: ask which event id and how long before
        speak('For which event id should I set a reminder?')
        eid = get_input('Event id for reminder:')
        ev = find_event_by_id(eid)
        if not ev:
            speak('Event not found.')
            return False
        speak('When should I remind you? For example say "10 minutes" or "2 hours" or a specific time.')
        rem = get_input('Reminder time:')
        dur = parse_duration_input(rem)
        if dur:
            # store reminder as seconds-before in event
            ev['reminder_seconds_before'] = int(dur)
            save_events()
            speak('Reminder set.')
        else:
            speak('Could not understand the reminder time.')
        return False

    # Brightness controls
    if 'brightness' in query and ('set' in query or 'to' in query):
        m = re.search(r"(\d{1,3})%?", query)
        if m:
            val = int(m.group(1))
            if set_brightness(val):
                speak(f'Brightness set to {val} percent')
            else:
                speak('Failed to set brightness. You may need elevated permissions or no supported backend.')
        else:
            speak('Please say the brightness percent to set, for example set brightness to 70 percent.')
        return False

    if 'increase brightness' in query or 'brightness up' in query:
        m = re.search(r"(\d{1,3})%?", query)
        step = int(m.group(1)) if m else 10
        if increase_brightness(step):
            speak('Increased brightness.')
        else:
            speak('Could not increase brightness.')
        return False

    if 'decrease brightness' in query or 'brightness down' in query or 'dim' in query:
        m = re.search(r"(\d{1,3})%?", query)
        step = int(m.group(1)) if m else 10
        if decrease_brightness(step):
            speak('Decreased brightness.')
        else:
            speak('Could not decrease brightness.')
        return False

    if 'what is the brightness' in query or 'current brightness' in query or ('brightness' in query and 'current' in query):
        cur = get_brightness()
        if cur is not None:
            speak(f'Current brightness is {cur} percent')
        else:
            speak('Brightness information is not available on this system.')
        return False

    if 'set volume' in query:
        m = re.search(r"(\d+)%?", query)
        if m:
            set_volume(int(m.group(1)))
        else:
            speak('Please say a volume percent, for example set volume to 50.')
        return False

    if 'open' in query and 'open code' not in query and 'open google' not in query:
        # parse application name
        app = query.replace('open', '', 1).strip()
        open_app(app)
        return False

    if 'close' in query and 'close browser' not in query:
        app = query.replace('close', '', 1).strip()
        close_app(app)
        return False

    if 'set alarm' in query:
        speak("Please tell me the time for the alarm (e.g., '7:30 am' or 'nineteen fifteen').")
        alarm_input = get_input('Alarm time:')
        if alarm_input != "None":
            alarm_time = parse_time_input(alarm_input)
            if alarm_time:
                # Check if the time is valid
                try:
                    hour, minute = map(int, alarm_time.split(':'))
                    if 0 <= hour < 24 and 0 <= minute < 60:
                        alarm_thread = threading.Thread(target=set_alarm, args=(alarm_time,))
                        alarm_thread.start()
                    else:
                        speak("That's not a valid time. Please try again.")
                except:
                    speak("Sorry, I couldn't understand the time. Please try again.")
            else:
                speak("Sorry, I couldn't understand the time. Please try again.")
        else:
            speak("I didn't catch the alarm time. Please try again.")
        return False

    if 'cancel alarm' in query:
        if alarm_thread and alarm_thread.is_alive():
            alarm_cancel = True
            speak("Alarm cancelled.")
        else:
            speak("There is no active alarm to cancel.")
        return False

    if 'set timer' in query:
        speak("How long should I set the timer for? (e.g., '5 minutes', '2 hours 30 minutes', or '90 seconds')")
        timer_input = get_input('Timer duration:')
        if timer_input != "None":
            duration = parse_duration_input(timer_input)
            if duration:
                timer_thread = threading.Thread(target=set_timer, args=(duration,))
                timer_thread.start()
            else:
                speak("Sorry, I couldn't understand the duration. Please try again.")
        else:
            speak("I didn't catch the timer duration. Please try again.")
        return False

    if 'cancel timer' in query:
        if timer_thread and timer_thread.is_alive():
            timer_cancel = True
            speak("Timer cancelled.")
        else:
            speak("There is no active timer to cancel.")
        return False

    # Monitoring and maintenance
    if 'start monitoring' in query or 'enable monitoring' in query:
        load_config()
        enable_monitoring()
        return False

    if 'stop monitoring' in query or 'disable monitoring' in query:
        disable_monitoring()
        return False

    if 'show config' in query or 'show configuration' in query:
        load_config()
        show_config()
        return False

    if 'set thresholds' in query or 'configure thresholds' in query:
        set_thresholds_via_voice()
        return False

    if 'check updates' in query or 'system updates' in query:
        if offline_mode.is_offline():
            speak('System update checks require online mode. Please switch to online mode.')
            return False
        speak('Checking for system updates. This may take a moment.')
        updates = check_for_system_updates()
        speak('Update check complete. Here is a summary:')
        # Limit verbose output
        speak(updates.splitlines()[0] if updates and '\n' in updates else (updates if updates else 'No updates found.'))
        return False

    if 'clear temp' in query or 'clear temporary files' in query:
        speak('Clearing temporary files older than 24 hours.')
        clear_temp_files(older_than_hours=24)
        return False

    if 'save config' in query:
        save_config()
        return False

    if 'battery status' in query or 'battery level' in query or 'battery' in query:
        batt = get_battery_info()
        if batt.get('battery_pct') is not None:
            charging = 'charging' if batt.get('battery_charging') else 'not charging'
            speak(f"Battery at {batt['battery_pct']} percent and {charging}.")
        else:
            speak('Battery information not available on this system.')
        return False

    if 'set battery threshold' in query or 'battery threshold' in query:
        set_battery_threshold_via_voice()
        return False

    if 'go offline' in query or 'set offline' in query or 'offline mode' in query:
        offline_mode.set_offline(True)
        speak('Assistant set to offline mode. Online features are disabled until you go back online.')
        return False

    if 'go online' in query or 'set online' in query or 'online mode' in query:
        offline_mode.set_offline(False)
        speak('Assistant set to online mode. Online features restored.')
        return False

    if 'are you offline' in query or 'offline status' in query or 'what mode' in query:
        speak('I am currently in offline mode.' if offline_mode.is_offline() else 'I am currently online and can use web features.')
        return False

    if 'remember' in query or 'remember that' in query:
        set_user_data_via_voice()
        return False

    if 'recall' in query or 'what is' in query or "what's" in query:
        # try to parse key after phrases
        m = re.search(r"(?:recall|what is|what's)\s+(.*)", query)
        key = m.group(1) if m else None
        val = get_user_data(key)
        if val:
            speak(f'{key if key else "That"} is {val}')
        else:
            speak('I do not have that saved.')
        return False

    if 'forget' in query or 'forget that' in query:
        forget_user_data_via_voice()
        return False

    if 'show user data' in query or 'show my data' in query:
        show_all_user_data()
        return False

    if 'exit' in query or 'bye' in query:
        speak("Goodbye!")
        # Clean up
        if alarm_thread and alarm_thread.is_alive():
            alarm_cancel = True
        if timer_thread and timer_thread.is_alive():
            timer_cancel = True
        # save user data and config
        save_user_data()
        save_config()
        pygame.quit()
        return True

    return False


def main_assistant_loop():
    wishMe()
    load_user_data()
    global alarm_thread, timer_thread, alarm_cancel, timer_cancel
    while True:
        query = takeCommand()
        if not query or query in ("none", "None"):
            continue
        should_exit = process_query(query.lower())
        if should_exit:
            break

if __name__ == "__main__":
    wait_for_wake_word()
    # Simple shutdown wrapper: confirm voice shutdown, then start assistant loop.
    _original_takeCommand = takeCommand

    def takeCommand():
        query = _original_takeCommand()
        if query and query != "None" and "shutdown" in query:
            speak("Are you sure you want to shut down the system? Say 'yes' to confirm or 'no' to cancel.")
            confirmation = _original_takeCommand()
            if confirmation and confirmation != "None" and any(w in confirmation.lower() for w in ("yes", "y", "confirm", "sure", "yep", "yeah")):
                speak("Shutting down now. Goodbye!")
                try:
                    if os.name == 'nt':
                        subprocess.call(["shutdown", "/s", "/t", "0"])
                    else:
                        subprocess.call(["shutdown", "-h", "now"])
                except Exception as e:
                    print("Shutdown failed:", e)
                    speak("I couldn't shut down the system. Please check permissions.")
                finally:
                    os._exit(0)
            else:
                speak("Shutdown cancelled.")
            return "None"
        return query

    main_assistant_loop()
