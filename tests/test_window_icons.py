"""Profile isolation checks; no native window or user data is changed."""
from pathlib import Path
import unittest
from window_icons import matching_browser_processes, follow_browser_windows, WM_GETICON, WM_SETICON, ICON_SMALL, ICON_BIG

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / 'data/browser-profile'


class WindowIconIsolationTests(unittest.TestCase):
    def matches(self, *rows):
        return matching_browser_processes(rows, PROFILE, lambda value: value.split('|'))

    def row(self, pid, profile=PROFILE, browser='msedge.exe', extra=''):
        command = f'C:/browser/{browser}|--user-data-dir={profile}|--app=http://127.0.0.1:47831'
        return {'ProcessId':pid, 'CommandLine':command + ('|' + extra if extra else '')}

    def test_exact_profile_accepts_browser_process(self):
        self.assertEqual(self.matches(self.row(7), self.row(8, browser='chrome.exe')), {7, 8})

    def test_similar_prefix_and_other_profile_are_excluded(self):
        self.assertEqual(self.matches(self.row(7, str(PROFILE) + '-backup'),
                                     self.row(8, ROOT / 'data/another-profile')), set())

    def test_renderer_is_excluded_even_with_profile_argument(self):
        self.assertEqual(self.matches(self.row(7, extra='--type=renderer')), set())

    def test_other_executable_is_excluded(self):
        self.assertEqual(self.matches(self.row(7, browser='other.exe')), set())

    def test_missing_command_is_excluded(self):
        self.assertEqual(self.matches({'ProcessId':7, 'CommandLine':None}), set())


class RestartingBrowser:
    """The old process closes before a rapid reopen creates a new PID."""
    def __init__(self):
        self.now=0.0
        self.sets=[]
        self.icons={(1001,ICON_SMALL):7,(1001,ICON_BIG):70,
                    (2001,ICON_SMALL):8,(2001,ICON_BIG):80}
        self.refreshed=False

    def pause(self, duration):
        self.now+=duration

    def browser_pids(self, _):
        return {100} if self.now<.75 else {200} if self.now<3 else set()

    def windows(self, pids):
        active=self.browser_pids(None)
        if self.now>=1.5 and not self.refreshed:
            self.icons[(2001,ICON_SMALL)]=8
            self.refreshed=True
        return [1001 if active=={100} else 2001] if active and active==pids else []

    def message(self, hwnd, message, kind, value=0):
        if message==WM_GETICON:
            return self.icons[(hwnd,kind)]
        assert message==WM_SETICON
        self.sets.append((hwnd,kind,value))
        self.icons[(hwnd,kind)]=value


class WindowIconLifecycleTests(unittest.TestCase):
    def run_owner(self):
        api=RestartingBrowser()
        follow_browser_windows(api,PROFILE,99,clock=lambda:api.now,pause=api.pause)
        return api

    def test_quick_reopen_discovers_replacement_process(self):
        api=self.run_owner()
        self.assertIn((1001,ICON_SMALL,99),api.sets)
        self.assertIn((2001,ICON_SMALL,99),api.sets)

    def test_browser_refresh_restores_caption_only(self):
        api=self.run_owner()
        self.assertEqual(api.sets.count((2001,ICON_SMALL,99)),2)
        self.assertTrue(all(kind==ICON_SMALL for _,kind,_ in api.sets))
        self.assertEqual(api.icons[(1001,ICON_BIG)],70)
        self.assertEqual(api.icons[(2001,ICON_BIG)],80)

    def test_owner_exits_after_last_window_closes(self):
        api=self.run_owner()
        self.assertGreater(api.now,6)
        self.assertLess(api.now,8)


if __name__ == '__main__':
    unittest.main()
