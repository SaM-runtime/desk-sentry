from collections.abc import Iterable
from pathlib import Path

import pytest

from desk_sentry.meeting import UsageEntry, camera_in_use


class CapabilityReader:
    def __init__(self, entries: dict[str, Iterable[UsageEntry]]) -> None:
        self._entries = entries

    def entries(self, capability: str) -> Iterable[UsageEntry]:
        return self._entries.get(capability, ())


@pytest.mark.parametrize(
    "identity",
    [
        r"C:\Program Files\Browser\browser.exe",
        r"C:\Windows\System32\SoundRecorder.exe",
    ],
)
def test_non_meeting_microphone_users_do_not_count_as_a_meeting(identity: str) -> None:
    reader = CapabilityReader({"microphone": [UsageEntry(identity, 1, 0)]})

    assert not camera_in_use(reader)


@pytest.mark.parametrize(
    "identity",
    [
        "MSTeams_8wekyb3d8bbwe",
        "MicrosoftTeams_8wekyb3d8bbwe",
        r"C:\Program Files\WindowsApps\MS-TEAMS.EXE",
        r"C:\Program Files\Zoom\bin\zoom.exe",
    ],
)
def test_supported_meeting_microphone_users_count_as_a_meeting(identity: str) -> None:
    reader = CapabilityReader({"microphone": [UsageEntry(identity, 1, 0)]})

    assert camera_in_use(reader)


def test_any_other_webcam_user_counts_as_a_meeting() -> None:
    reader = CapabilityReader(
        {"webcam": [UsageEntry(r"C:\Program Files\Browser\browser.exe", 1, 0)]}
    )

    assert camera_in_use(reader)


@pytest.mark.parametrize("capability", ["microphone", "webcam"])
def test_own_active_device_record_is_excluded(capability: str) -> None:
    own = Path(r"C:\Desk Sentry\teams.exe")
    encoded_own = str(own).replace("\\", "#")
    reader = CapabilityReader({capability: [UsageEntry(encoded_own, 1, 0)]})

    assert not camera_in_use(reader, self_executable=own)
