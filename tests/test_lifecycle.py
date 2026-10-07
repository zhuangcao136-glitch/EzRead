"""Shutdown identity, durable paused state and owned-process boundaries."""
import copy
import queue
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import server
from ezread import processes
from ezread.selection_requests import SelectionRequests

PID = '0123456789abcdef'


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd() / 'work', prefix='exit-state-')
        self.folder = Path(self.temp.name)
        self.event = threading.Event()
        self.patches = [patch.object(server, 'DATA', self.folder), patch.object(server, 'LIBRARY', self.folder / 'library'),
                        patch.object(server, 'SHUTDOWN', threading.Event()), patch.object(server, 'INSTANCE_ID', 'a' * 32),
                        patch.object(server, 'CANCEL', {(PID, 'translate'): self.event}), patch.object(server, 'RESUME_REQUESTED', {(PID, 'translate')}),
                        patch.object(server, 'JOBS', queue.Queue()), patch.object(server, 'STRUCTURE_JOBS', queue.Queue()),
                        patch.object(server, 'SELECTION_REQUESTS', SelectionRequests())]
        for item in self.patches: item.start()
        self.addCleanup(self.temp.cleanup)
        for item in self.patches: self.addCleanup(item.stop)
        server.init_db()
        self.doc = {'id': PID, 'hash': 'synthetic-exit-hash', 'title': 'Synthetic exit fixture', 'deleted': False, 'notes': 'saved note',
                    'blocks': [{'id': 'one', 'text': 'Source.', 'translation': '已有译文'}],
                    'translation': {'status': 'running', 'staging': {'one': '未提交的重译草稿'}, 'config': {'model': 'fixture'}},
                    'reading_structure': {'status': 'queued'}, 'summarize_status': 'running'}
        server.put_doc(self.doc)
    def test_close_cancels_requests_and_preserves_translation_draft_notes_and_source(self):
        selection = server.SELECTION_REQUESTS.begin(PID, {'client_id': 'b' * 32, 'request_id': 'c' * 32})
        response = server.prepare_shutdown({'instance_id': 'a' * 32})
        self.assertTrue(response['closing'])
        self.assertTrue(server.SHUTDOWN.is_set())
        self.assertTrue(self.event.is_set())
        self.assertTrue(selection.is_set())
        self.assertEqual(server.RESUME_REQUESTED, set())
        doc = server.get_doc(PID)
        self.assertEqual(doc['translation']['status'], 'paused')
        self.assertEqual(doc['translation']['staging'], self.doc['translation']['staging'])
        self.assertEqual(doc['notes'], self.doc['notes'])
        self.assertEqual(doc['blocks'], self.doc['blocks'])
        self.assertEqual(doc['summarize_status'], 'paused')
        self.assertEqual(doc['reading_structure']['status'], 'needs_review')
    def test_wrong_instance_never_stops_service_or_changes_library(self):
        before = copy.deepcopy(server.get_doc(PID))
        for data in ({'instance_id': 'd' * 32}, {}, {'instance_id': []}, {'instance_id': 'a' * 32, 'extra': 1}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                server.prepare_shutdown(data)
        self.assertFalse(server.SHUTDOWN.is_set())
        self.assertFalse(self.event.is_set())
        self.assertEqual(server.get_doc(PID), before)
    def test_workers_exit_on_sentinel_and_closing_rejects_new_tasks(self):
        server.JOBS.put(None); server.STRUCTURE_JOBS.put(None)
        for callback in (server.worker, server.structure_worker):
            worker = threading.Thread(target=callback)
            worker.start(); worker.join(1)
            self.assertFalse(worker.is_alive())
        server.SHUTDOWN.set()
        with self.assertRaisesRegex(ValueError, '正在关闭'):
            server.enqueue(PID, 'translate')
        self.assertTrue(server.JOBS.empty())
    def test_registry_stops_only_its_own_processes_and_prevents_late_spawns(self):
        owned, foreign = Mock(), Mock()
        stopped = []
        with patch.object(processes, '_processes', set()), patch.object(processes, '_closing', False), \
             patch.object(processes.subprocess, 'Popen', return_value=owned) as popen:
            processes.spawn(['synthetic-process'])
            processes.shutdown(stopped.append)
            self.assertEqual(stopped, [owned])
            self.assertNotIn(foreign, stopped)
            with self.assertRaises(processes.Closing): processes.spawn(['late-process'])
            self.assertEqual(popen.call_count, 1)


if __name__ == '__main__': unittest.main()
