"""Task queue, cancellation, quota pause and local Codex status cache."""
from __future__ import annotations
from .context import ApplicationContext
from .overview import overview_text
import sys
import threading
import time


def codex_status(app: ApplicationContext, force=False):
    if force or time.time() - app.STATUS_CACHE['at'] > 30:
        try:
            import codex_bridge
            value = codex_bridge.status()
        except Exception as exc:
            value = {'available': False, 'authenticated': False, 'method': '', 'message': str(exc)}
        app.STATUS_CACHE.update(at=time.time(), value=value)
    return app.STATUS_CACHE['value']


def enqueue(app: ApplicationContext, pid, kind, options=None):
    if kind not in ('translate', 'summarize'):
        raise ValueError('作者与团队查询功能已取消。' if kind == 'team' else '此任务已取消或不受支持。')
    app.get_doc(pid)
    with app.LOCK:
        if app.SHUTDOWN.is_set():
            raise ValueError('应用正在关闭，任务未加入队列。')
        if (pid, kind) in app.QUEUED:
            if kind == 'translate' and options:
                check = app.get_doc(pid)
                app.prepare_translation(check, options)
                if check['translation'].get('run_id') != app.get_doc(pid)['translation'].get('run_id'):
                    raise ValueError('请先暂停当前翻译并等待批次停止，再开始重译。')
            if app.CANCEL.get((pid, kind), threading.Event()).is_set():
                app.RESUME_REQUESTED.add((pid, kind))
                if kind == 'translate':
                    app.update_doc(pid, lambda d: d['translation'].update(status='queued', error='等待当前批次暂停后继续。'))
            return
        if kind == 'translate' and not app.translation_counts(app.get_doc(pid))['total']:
            raise ValueError('没有可翻译文字；当前版本主要支持出版社的文字型 PDF。')
        if kind == 'translate':
            app.update_doc(pid, lambda d: app.prepare_translation(d, options))
        elif kind == 'summarize':
            import paper_ai
            with app.db() as con:
                defaults = app.settings()
                locked = paper_ai.current(con, pid)
                paper_ai.lock_model(con, pid, locked['model'] or defaults['translation_model'],
                                    locked['reasoning_effort'] or defaults['translation_reasoning_effort'])
        app.QUEUED.add((pid, kind))
        app.QUOTA_PAUSED.clear()  # Only an explicit user action retries after quota limits.
        app.CANCEL[(pid, kind)] = threading.Event()
        if kind == 'translate':
            app.update_doc(pid, lambda d: d['translation'].update(status='queued', error=''))
        else:
            app.update_doc(pid, lambda d: d.update(**{kind + '_status': 'queued', kind + '_error': ''}))
        app.JOBS.put((pid, kind))


def batches(blocks, char_limit=9000):
    current, size = [], 0
    for block in blocks:
        if current and size + len(block['text']) > char_limit:
            yield current
            current, size = [], 0
        current.append(block)
        size += len(block['text'])
    if current:
        yield current


def worker(app: ApplicationContext):
    import codex_bridge
    while True:
        job = app.JOBS.get()
        if job is None:
            app.JOBS.task_done()
            return
        pid, kind = job
        try:
            if app.SHUTDOWN.is_set() or kind not in ('translate', 'summarize'):
                continue
            doc = app.get_doc(pid)
            event = app.CANCEL[(pid, kind)]
            if event.is_set():
                continue
            if app.QUOTA_PAUSED.is_set():
                raise codex_bridge.TranslationError('订阅额度已受限，队列已暂停。额度恢复后请点击继续。', code='quota')
            if kind == 'translate':
                app.execute_translation(pid, event)
            elif kind == 'summarize':
                app.update_doc(pid, lambda d: d.update(summarize_status='running'))
                text = '\n\n'.join(b['text'] for b in doc.get('blocks', []) if b.get('kind') != 'reference')
                import paper_ai
                with app.db() as con:
                    ai = paper_ai.current(con, pid)
                result = codex_bridge.summarize_paper(text, cancel_event=event,
                    model=ai['model'], reasoning_effort=ai['reasoning_effort'])
                if event.is_set():
                    raise codex_bridge.TranslationError('速览生成已取消，保留原有内容。', code='cancelled')
                allowed = ('title_zh', 'summary', 'overview_sections', 'tags')
                app.update_doc(pid, lambda d: d.update(**{k: result[k] for k in allowed if k in result}, summarize_status='completed'))
                with app.db() as con:
                    paper_ai.add_message(con,pid,ai['generation'],'assistant','summary',
                        overview_text(result))
        except Exception as exc:
            message = str(exc)[:1800]
            limited = getattr(exc, 'code', '') == 'quota'
            if limited:
                app.QUOTA_PAUSED.set()
            try:
                if kind == 'translate':
                    app.update_doc(pid, lambda d: d['translation'].update(status='paused' if limited or app.CANCEL.get((pid, kind), threading.Event()).is_set() else 'error', error=message))
                else:
                    app.update_doc(pid, lambda d: d.update(**{kind + '_status': 'paused' if app.SHUTDOWN.is_set() else 'error', kind + '_error': message}))
            except Exception:
                pass
            print(f'[job {kind} {pid}] {message}', file=sys.stderr, flush=True)
        finally:
            with app.LOCK:
                app.QUEUED.discard((pid, kind))
                resume = (pid, kind) in app.RESUME_REQUESTED
                app.RESUME_REQUESTED.discard((pid, kind))
                if resume and kind in ('translate', 'summarize') and not app.QUOTA_PAUSED.is_set() and not app.SHUTDOWN.is_set():
                    try:
                        app.enqueue(pid, kind)
                    except ValueError:
                        pass
            app.JOBS.task_done()
