import sys
import time

STOP_CMD = 'stop text'


def start_text_mode(speak, process_query):
    """Run a terminal text-mode loop. User types queries; each line is handled by process_query.

    The function returns when the user types 'stop text'.
    """
    speak('Entering text mode. Type commands into the terminal. Type "stop text" to return to voice mode.')
    print('--- Text Mode (type commands; "stop text" to exit) ---')
    import importlib
    va = None
    try:
        va = importlib.import_module('voice_assistant')
        # register provider
        va.set_text_mode_provider(lambda prompt=None: input('> '))
    except Exception:
        va = None

    try:
        while True:
            try:
                line = input('> ')
            except EOFError:
                # treat EOF as exit
                speak('Exiting text mode.')
                if va:
                    try:
                        va.clear_text_mode_provider()
                    except Exception:
                        pass
                return
            if not line:
                continue
            if line.strip().lower() == STOP_CMD:
                speak('Exiting text mode and returning to voice mode.')
                if va:
                    try:
                        va.clear_text_mode_provider()
                    except Exception:
                        pass
                return
            # Pass the typed line as if it were a spoken query
            process_query(line.strip().lower())
            # small pause
            time.sleep(0.1)
    except KeyboardInterrupt:
        speak('Text mode interrupted. Returning to voice mode.')
        if va:
            try:
                va.clear_text_mode_provider()
            except Exception:
                pass
        return
