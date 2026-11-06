import threading
from pathlib import Path

from desk_sentry.meeting import (
    MeetingMonitor,
    MeetingState,
    UsageEntry,
    WinRegistryReader,
    camera_in_use,
)


class FakeReader:
    def __init__(self, entries=(), error: Exception | None = None):
        self.found = list(entries)
        self.error = error

    def entries(self, capability):
        if self.error is not None:
            raise self.error
        return list(self.found)


class FakeKey:
    def __init__(self, module, path):
        self.module = module
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


class FakeWinreg:
    HKEY_CURRENT_USER = object()

    def __init__(self, tree):
        self.tree = tree

    def OpenKey(self, _root, path):  # noqa: N802
        if path not in self.tree:
            raise FileNotFoundError(path)
        return FakeKey(self, path)

    def EnumKey(self, key, index):  # noqa: N802
        names = self.tree[key.path].get("children", [])
        if index >= len(names):
            error = OSError("no more items")
            error.winerror = 259
            raise error
        return names[index]

    def QueryValueEx(self, key, name):  # noqa: N802
        values = self.tree[key.path].get("values", {})
        if name not in values:
            raise FileNotFoundError(name)
        return values[name], 0


def test_win_registry_reader_recurses_packaged_and_nonpackaged_entries():
    base = WinRegistryReader.ROOT + r"\webcam"
    nonpackaged = base + r"\NonPackaged"
    encoded = nonpackaged + r"\C:#Apps#Call#other.exe"
    packaged = base + r"\MSTeams_8wekyb3d8bbwe"
    winreg = FakeWinreg(
        {
            base: {"children": ["NonPackaged", "MSTeams_8wekyb3d8bbwe"]},
            nonpackaged: {"children": ["C:#Apps#Call#other.exe"]},
            encoded: {"values": {"LastUsedTimeStart": 10, "LastUsedTimeStop": 0}},
            packaged: {"values": {"LastUsedTimeStart": 20, "LastUsedTimeStop": 0}},
        }
    )
    entries = list(WinRegistryReader(winreg).entries("webcam"))

    assert entries == [
        UsageEntry(r"C:\Apps\Call\other.exe", 10, 0),
        UsageEntry("MSTeams_8wekyb3d8bbwe", 20, 0),
    ]


def test_monitor_child_disappearing_keeps_active_and_restarts_clear_delay():
    now = [0.0]
    base = WinRegistryReader.ROOT + r"\webcam"
    child = base + r"\meeting.package"
    winreg = FakeWinreg(
        {
            base: {"children": ["meeting.package"]},
            child: {"values": {"LastUsedTimeStart": 1, "LastUsedTimeStop": 0}},
        }
    )
    seen = []
    monitor = MeetingMonitor(
        seen.append,
        reader=WinRegistryReader(winreg),
        clock=lambda: now[0],
    )

    assert monitor.poll_once() is True

    winreg.tree[base]["children"] = []
    now[0] = 1
    assert monitor.poll_once() is None

    winreg.tree[base]["children"] = ["meeting.package"]
    del winreg.tree[child]
    now[0] = 9
    assert monitor.poll_once() is None
    assert seen == [True] and monitor.active

    winreg.tree[base]["children"] = []
    now[0] = 10
    assert monitor.poll_once() is None
    now[0] = 19.999
    assert monitor.poll_once() is None
    now[0] = 20
    assert monitor.poll_once() is False
    assert seen == [True, False]


def test_active_rule_checks_both_capabilities_and_excludes_only_exact_self():
    self_path = Path(r"C:\Desk Sentry\python\pythonw.exe")
    entries = [
        UsageEntry(str(self_path).replace("\\", "#"), 10, 0),
        UsageEntry(r"C:\Other App\pythonw.exe", 12, 0),
        UsageEntry("finished.package", 20, 21),
        UsageEntry("never-started.package", 0, 0),
    ]
    reader = FakeReader(entries)

    assert camera_in_use(reader, self_executable=self_path)
    assert not camera_in_use(
        FakeReader([entries[0], entries[2], entries[3]]), self_executable=self_path
    )


def test_meeting_state_enters_immediately_and_resumes_after_stable_clear():
    state = MeetingState(clear_delay=10)

    assert state.update(False, 0) is None
    assert state.update(True, 1) is True
    assert state.update(False, 2) is None
    assert state.update(False, 11.999) is None
    assert state.update(True, 12) is None
    assert state.update(False, 20) is None
    assert state.update(False, 30) is False
    assert state.active is False


def test_monitor_scan_failure_does_not_falsely_resume_active_state():
    now = [0.0]
    reader = FakeReader([UsageEntry("meeting.package", 1, 0)])
    seen = []
    monitor = MeetingMonitor(seen.append, reader=reader, clock=lambda: now[0])

    assert monitor.poll_once() is True
    reader.error = PermissionError("registry denied")
    now[0] = 50
    assert monitor.poll_once() is None
    assert seen == [True] and monitor.active
    reader.error = None
    reader.found = []
    now[0] = 51
    assert monitor.poll_once() is None
    now[0] = 60.9
    assert monitor.poll_once() is None
    now[0] = 61
    assert monitor.poll_once() is False


def test_monitor_background_poll_uses_wait_boundary_without_blocking_caller():
    callback_called = threading.Event()
    waits = []

    def callback(active):
        assert active is True
        callback_called.set()

    def wait(interval):
        waits.append(interval)
        return True

    monitor = MeetingMonitor(
        callback,
        reader=FakeReader([UsageEntry("meeting.package", 1, 0)]),
        wait=wait,
    )
    monitor.start()
    assert callback_called.wait(1)
    monitor.stop()

    assert waits == [2.0]
