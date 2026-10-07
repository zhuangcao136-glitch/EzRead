"""Prepare the shared selection conversation once, without blocking local reading."""
import threading

import codex_bridge


class SelectionSession:
    def __init__(self):
        self.lock = threading.RLock()
        self.worker = None
        self.state = {'status': 'idle', 'message': ''}

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def report(self, value):
        with self.lock:
            self.state['message'] = value.get('text', '')

    def start(self, app):
        with self.lock:
            if app.SHUTDOWN.is_set():
                return {'status': 'stopped', 'message': '应用正在关闭。'}
            if self.state['status'] != 'idle':
                return self.snapshot()
            self.state.update(status='preparing', message='正在后台准备划线翻译对话…')
            self.worker = threading.Thread(target=self._prepare, args=(app,), daemon=True, name='ezread-selection-prepare')
            try:
                self.worker.start()
            except RuntimeError:
                self.state.update(status='failed', message='无法准备划线翻译对话，请在翻译时重试。')
            return self.snapshot()

    def _prepare(self, app):
        try:
            # A fixed, short test turn warms the same conversation used for selections.
            # It contains no paper data; absent options use Codex's model defaults.
            settings = app.settings()
            model = settings.get('selection_translation_model') or None
            effort = settings.get('selection_translation_reasoning_effort') or None
            codex_bridge.prepare_selection(model=model, reasoning_effort=effort, cancel_event=app.SHUTDOWN, on_progress=self.report)
            with self.lock:
                if app.SHUTDOWN.is_set():
                    self.state.update(status='stopped', message='应用已关闭。')
                else:
                    self.state.update(status='ready', message='划线翻译对话已准备好。')
        except codex_bridge.TranslationError as exc:
            with self.lock:
                self.state.update(status='stopped' if app.SHUTDOWN.is_set() else 'failed', message=str(exc))
        except Exception:
            with self.lock:
                self.state.update(status='failed', message='划线翻译对话准备失败，请在翻译时重试。')
