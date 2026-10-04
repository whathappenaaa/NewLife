import pytest
from niulai_player.hotkeys import HotkeyService, parse_hotkey


class Backend:
    def __init__(self):
        self.active = {}
        self.blocked = set()

    def RegisterHotKey(self, window, identifier, modifiers, key):
        pair = (modifiers & ~0x4000, key)
        if pair in self.blocked or pair in self.active.values():
            return False
        self.active[identifier] = pair
        return True

    def UnregisterHotKey(self, window, identifier):
        self.active.pop(identifier, None)
        return True


def test_conflict_restores_existing_bindings():
    backend = Backend()
    service = HotkeyService(lambda _: None, backend=backend)
    service.replace({"stop": "Ctrl+Alt+S"})
    before = backend.active.copy()
    backend.blocked.add(parse_hotkey("Ctrl+Alt+A"))
    with pytest.raises(ValueError, match="占用"):
        service.replace({"new": "Ctrl+Alt+A"})
    assert service.bindings == {"stop": "Ctrl+Alt+S"}
    assert backend.active == before
    service.close()


def test_duplicate_detected_without_unregistering_old_key():
    backend = Backend()
    service = HotkeyService(lambda _: None, backend=backend)
    service.replace({"stop": "F9"})
    before = backend.active.copy()
    with pytest.raises(ValueError, match="重复"):
        service.replace({"a": "Ctrl+Alt+1", "b": "Alt+Ctrl+1"})
    assert backend.active == before
    service.close()


def test_capture_suspends_and_restores_all_keys():
    backend = Backend()
    service = HotkeyService(lambda _: None, backend=backend)
    service.replace({"stop": "F9", "mark": "Ctrl+M"})
    before = backend.active.copy()
    service.suspend(True)
    assert not backend.active
    service.suspend(False)
    assert backend.active == before
    service.close()


@pytest.mark.parametrize("text", ["", "A", "Ctrl+Ctrl+A, B", "Ctrl+F25", "Ctrl+🐮"])
def test_unsupported_or_typing_shortcuts_rejected(text):
    with pytest.raises(ValueError):
        parse_hotkey(text)


def test_modifiers_and_function_key_parse():
    assert parse_hotkey("Ctrl+Alt+Shift+F12") == (7, 0x7B)


def test_portable_restore_disables_only_conflicting_bindings():
    backend = Backend()
    backend.blocked.add(parse_hotkey("Ctrl+Alt+A"))
    service = HotkeyService(lambda _: None, backend=backend)
    definitions = {"stop": "F9", "imported": "Ctrl+Alt+A", "first": "Ctrl+Alt+1", "duplicate": "Alt+Ctrl+1"}
    disabled = service.replace_available(definitions)
    assert set(disabled) == {"imported", "duplicate"}
    assert service.bindings == {"stop": "F9", "first": "Ctrl+Alt+1"}
    assert len(backend.active) == 2
    assert definitions["imported"] == "Ctrl+Alt+A"
    service.close()


def test_portable_restore_keeps_good_bindings_when_one_definition_is_invalid():
    service = HotkeyService(lambda _: None, enabled=False)
    disabled = service.replace_available({"good": "F10", "bad": "unsupported key"})
    assert service.bindings == {"good": "F10"}
    assert set(disabled) == {"bad"}
    service.close()
