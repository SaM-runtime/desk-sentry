"""Task 16r user-facing meeting camera documentation."""

from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_readme_explains_exclusive_auto_yield_detection_limits() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "相容模式：Teams、Zoom 等會議軟體開麥克風時自動讓出" in readme
    assert "獨占攝影機" in readme and "攝影機使用紀錄" in readme
    assert "Google Meet" in readme and "Teams 網頁版" in readme
    assert "暫時讓出攝影機" in readme
    assert "共用後端成功時不會自動讓出" in readme
    assert "開會途中啟動 Desk Sentry" in readme
    assert "被 Windows 擋下" in readme
    assert "不會留下攝影機使用紀錄" in readme
    assert "主要依指定會議軟體的麥克風紀錄" in readme
    assert "程式不會自動觸發讓出" in readme
    assert "只有在其他 App 已先取得攝影機時" in readme


def test_changelog_202_has_task16r_user_visible_changes() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    section = changelog.split("## [2.0.2]", 1)[1].split("## [2.0.1]", 1)[0]

    assert "無法確認已讓出攝影機" in section
    assert "電腦鎖定中" in section and "非監看時段" in section
    assert "Teams、Zoom 等會議軟體開麥克風" in section
