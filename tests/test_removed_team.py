"""Removed team research cannot enqueue, execute or expose old stored results."""
import queue
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from ezread import tasks


class RemovedTeamTests(unittest.TestCase):
    def test_removed_research_is_rejected_before_reading_a_paper_or_calling_a_model(self):
        app = SimpleNamespace(get_doc=Mock(side_effect=AssertionError('Should reject before accessing the library')))
        with self.assertRaisesRegex(ValueError, '团队查询功能已取消'):
            tasks.enqueue(app, '0123456789abcdef', 'team')
        app.get_doc.assert_not_called()

    def test_legacy_queued_team_work_is_discarded_without_changing_the_document(self):
        app = SimpleNamespace(JOBS=queue.Queue(), SHUTDOWN=threading.Event(), LOCK=threading.RLock(),
                              QUEUED={('0123456789abcdef', 'team')}, RESUME_REQUESTED={('0123456789abcdef', 'team')},
                              enqueue=Mock(side_effect=AssertionError('Removed job must not resume')),
                              get_doc=Mock(side_effect=AssertionError('Removed queued job must not read a paper')))
        app.JOBS.put(('0123456789abcdef', 'team')); app.JOBS.put(None)
        tasks.worker(app)
        self.assertEqual(app.QUEUED, set())
        self.assertEqual(app.JOBS.unfinished_tasks, 0)
        app.get_doc.assert_not_called()
        app.enqueue.assert_not_called()


if __name__ == '__main__': unittest.main()
