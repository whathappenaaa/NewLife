"""Translation boundaries: nested diagnostics are translated; user data is not."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QLineEdit, QWidget

from niulai_player import i18n


@pytest.fixture
def translation_app():
    app = QApplication.instance() or QApplication([])
    previous = i18n.language()
    yield app
    i18n.set_language(previous)


@pytest.mark.parametrize("name", ["设置", "单次播放", "我说{声音} / 100%", "牛来\n声音"])
def test_user_sound_name_is_preserved_in_formatted_and_explicit_templates(name):
    template = "播放范围 · {name}"
    expected = "Playback range · " + name
    assert i18n.tr(template, "en", name=name) == expected
    assert i18n.tr(template.format(name=name), "en") == expected
    assert i18n.tr(template, "zh_CN", name=name) == "播放范围 · " + name


def test_paths_and_device_names_are_not_recursively_translated():
    path = r"D:\设置\单次播放\{原始声音}%20.wav"
    assert i18n.tr("已保存：" + path, "en") == "Saved: " + path
    assert i18n.tr("已另存：{path}", "en", path=path) == "Exported: " + path
    for device in ("麦克风", "设置", "{耳机} (USB Audio)"):
        translated = i18n.tr(f"设备已断开：{device}；请重新选择后手动恢复", "en")
        assert translated.startswith("Device disconnected: " + device + ".")


def test_nested_system_error_retains_unknown_os_details_and_paths():
    detail = "[WinError 5] Access denied: 'D:\\设置\\{恢复记录}.json' (0x80070005)"
    source = "恢复未完成：音频暂不可用：" + detail + "；可在 Windows 声音设置中手动恢复"
    translated = i18n.tr(source, "en")
    assert translated.startswith("Restoration is incomplete: Audio is unavailable: ")
    assert detail in translated
    assert "restore it manually in Windows Sound settings" in translated


def test_nested_error_translation_matches_before_or_after_formatting():
    template = "临时配置失败：{error}"
    error = "原麦克风当前不可用，已保留恢复记录"
    rendered = i18n.tr(template.format(error=error), "en")
    assert i18n.tr(template, "en", error=error) == rendered
    assert "The original microphone is unavailable." in rendered


def test_library_keyerror_keeps_exception_quotes_but_translates_known_message():
    raw = str(KeyError("音频不在媒体库中，请先刷新文件夹"))
    translated = i18n.tr(raw, "en")
    assert translated.startswith("'") and translated.endswith("'")
    assert "Refresh the folder." in translated
    unknown = str(KeyError("user-{private}-name.wav"))
    assert i18n.tr(unknown, "en") == unknown


def test_recording_recovery_warning_translates_the_nested_error():
    source = "录音保留在临时文件中，可下次恢复：音源在导出期间被截断，已取消保存"
    translated = i18n.tr(source, "en")
    assert "Recording kept in a temporary file" in translated
    assert "The source audio was truncated during export." in translated


def test_language_round_trip_preserves_editable_values_and_device_identity(translation_app):
    root = QWidget()
    editable = QLineEdit("设置", root)
    editable.setPlaceholderText("搜索音频")
    user_label = QLabel("单次播放", root)
    user_label.setProperty("i18n_skip", True)
    status = QLabel("范围已保存", root)
    combo = QComboBox(root)
    combo.setProperty("i18n_device_names", True)
    combo.addItem("请选择设备", "")
    combo.addItem("麦克风", "stable-device-guid")
    combo.addItem("设置", "second-device-guid")
    combo.setCurrentIndex(1)
    changes = []
    combo.currentIndexChanged.connect(changes.append)
    try:
        for _ in range(2):
            i18n.set_language("en")
            i18n.retranslate(root)
            assert status.text() == "Range saved"
            assert editable.placeholderText() == "Search sounds"
            assert editable.text() == "设置" and user_label.text() == "单次播放"
            assert combo.itemText(0) == "Select a device"
            assert [combo.itemText(1), combo.itemText(2)] == ["麦克风", "设置"]
            assert combo.currentData() == "stable-device-guid"
            i18n.set_language("zh_CN")
            i18n.retranslate(root)
            assert status.text() == "范围已保存"
            assert editable.placeholderText() == "搜索音频"
            assert combo.itemText(0) == "请选择设备"
        assert changes == []
    finally:
        root.close()
        root.deleteLater()
        translation_app.processEvents()


@pytest.mark.parametrize("source,expected", [
    ("背景已保存", "Background saved"),
    ("音轨已保存", "Audio track saved"),
    ("正在连接通话…", "Connecting call…"),
    ("配置保存未完成：\n配置包已被其他操作修改，请刷新后重试", "Configuration save incomplete:\nConfiguration package changed elsewhere; refresh and try again"),
    ("配置包不可用：配置包损坏，已保留原包", "Configuration package unavailable: Damaged configuration package; original package preserved"),
])
def test_v03_configuration_and_routing_messages_translate_nested_errors(source, expected):
    assert i18n.tr(source, "en") == expected
    assert i18n.tr(source, "zh_CN") == source


def test_track_translation_preserves_user_title_and_language_tag():
    text = i18n.tr("音轨 {number} · {track_language} · {codec} · {channels} 声道", "en", number=2, track_language="chi", codec="aac", channels=6)
    assert text == "Track 2 · chi · aac · 6 channels"


def test_v06_deletion_error_translation_keeps_conflicting_file_name():
    detail = "D:\\音频\\{私人名称}.wav: 原位置已有同名文件，未覆盖；请移开该文件后再次撤销"
    translated = i18n.tr("撤销未完成，删除暂存文件已保留：{error}", "en", error=detail)
    assert translated.startswith("Undo incomplete. Staged files have been kept:")
    assert "D:\\音频\\{私人名称}.wav" in translated
    # A composite OS/path diagnostic is preserved rather than interpreted as a
    # filename translation template. Its surrounding message is translated.
    assert i18n.tr("原位置已有同名文件，未覆盖；请移开该文件后再次撤销", "en").startswith("A file with the same name exists")


def test_v06_import_counts_and_recording_source_translate_after_formatting():
    assert i18n.tr("已导入 2 项，跳过 1 项，失败 3 项；详情中可查看原因", "en") == "Imported 2, skipped 1, failed 3. See Details for reasons."
    assert i18n.tr("● 正在录制麦克风 · 00:12", "en") == "● Recording Microphone · 00:12"
