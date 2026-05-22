import json
import os
from datetime import datetime, timedelta
import threading
from runtime.sdk.audio import speak

CONFIG_PATH = os.path.join(os.path.dirname(__file__), '../config.json')
RESPONSES_PATH = os.path.join(os.path.dirname(__file__), '../assets/responses/')
_ALARM_LOCK = threading.Lock()
_ALARM_STOP = threading.Event()
_ALARM_THREAD = None

def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'r') as f:
            return json.load(f)
    return {}

def save_config(cfg):
    with open(CONFIG_PATH, 'w') as f:
        json.dump(cfg, f)

def _stop_alarm_worker(wait=False):
    global _ALARM_THREAD
    with _ALARM_LOCK:
        thread = _ALARM_THREAD
        _ALARM_STOP.set()
        _ALARM_THREAD = None
    if wait and thread is not None and thread.is_alive():
        thread.join(timeout=1.0)

def set_alarm(time_str):
    global _ALARM_THREAD
    alarm_time = datetime.strptime(time_str, "%H:%M").time()
    now = datetime.now()
    alarm_dt = datetime.combine(now.date(), alarm_time)
    if alarm_dt < now:
        alarm_dt += timedelta(days=1)

    cfg = {"alarm": alarm_dt.isoformat()}
    save_config(cfg)
    speak("Будильник установлен", emotion="happy")

    def wait_and_ring():
        delay = max(0.0, (alarm_dt - datetime.now()).total_seconds())
        if _ALARM_STOP.wait(delay):
            return
        print("[ALARM] Время вставать!")  # отправка в аудио-плеер

    _stop_alarm_worker(wait=False)
    _ALARM_STOP.clear()
    with _ALARM_LOCK:
        _ALARM_THREAD = threading.Thread(target=wait_and_ring, name="alarm-skill-wait", daemon=True)
        _ALARM_THREAD.start()

def cancel_alarm():
    save_config({})
    _stop_alarm_worker(wait=True)
    speak("Будильник отменён", emotion="sad")

def handle(intent, entities):
    if intent == "set_alarm":
        time_str = entities.get("time", "07:00")
        set_alarm(time_str)
    elif intent == "cancel":
        cancel_alarm()
