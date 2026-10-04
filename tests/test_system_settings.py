import json
from pathlib import Path

from niulai_player.system_settings import TemporarySystemSettings, PersistentSystemSettings
from niulai_player.windows_audio import Endpoint, WindowsAudioBackend


class FakeWindows:
    def __init__(self):
        self.default = "original-mic"
        self.available = {"original-mic", "cable", "new-choice"}
        self.calls = []
        self.on_set = None

    def get_default_communications_input(self):
        return self.default

    def endpoint_available(self, identity):
        return identity in self.available

    def set_default_communications_input(self, identity):
        self.calls.append(identity)
        if self.on_set:
            self.on_set(identity)
        self.default = identity


def test_persistent_configuration_never_restores_on_disconnect_or_exit(tmp_path):
    backend = FakeWindows()
    service = PersistentSystemSettings(tmp_path, backend)
    assert service.recover()["ok"]
    assert service.apply("cable")["ok"]
    assert not service.journal.exists()
    service.restore("disconnect")
    service.close()
    assert backend.default == "cable"
    assert backend.calls == ["cable"]


def test_upgrade_retires_old_recovery_record_without_changing_system(tmp_path):
    backend = FakeWindows()
    old = TemporarySystemSettings(tmp_path, backend)
    old.apply("cable")
    old._owner.close()
    old._owner = None
    service = PersistentSystemSettings(tmp_path, backend)
    try:
        assert service.recover()["ok"]
        assert backend.default == "cable" and backend.calls == ["cable"]
        assert not service.journal.exists()
        assert len(list(tmp_path.glob("communications-legacy-*.json"))) == 1
    finally:
        service.close()


def test_durable_journal_precedes_setting_and_repeated_apply_keeps_original(tmp_path):
    backend = FakeWindows()
    service = TemporarySystemSettings(tmp_path, backend)

    def verify_journal(identity):
        entry = json.loads(service.journal.read_text(encoding="utf-8"))
        assert entry["original_id"] == "original-mic"
        assert entry["target_id"] == "cable"
        assert entry["role"] == 2 and entry["flow"] == "capture"
        if identity == "cable":
            assert entry["phase"] == "prepared"

    backend.on_set = verify_journal
    try:
        assert service.apply("cable")["ok"]
        assert service.apply("cable")["status"] == "active"
        assert backend.calls == ["cable"]
        assert service.restore()["status"] == "restored"
        assert backend.calls == ["cable", "original-mic"]
        assert not service.journal.exists()
        assert service.restore()["status"] == "idle"
    finally:
        service.close()


def test_external_change_is_preserved(tmp_path):
    backend = FakeWindows()
    service = TemporarySystemSettings(tmp_path, backend)
    service.apply("cable")
    backend.default = "new-choice"
    assert service.restore()["status"] == "external_change"
    assert backend.default == "new-choice"
    assert backend.calls == ["cable"]
    service.close()


def test_original_already_target_does_not_claim_ownership(tmp_path):
    backend = FakeWindows()
    backend.default = "cable"
    service = TemporarySystemSettings(tmp_path, backend)
    assert service.apply("cable")["status"] == "unchanged"
    assert not service.journal.exists()
    service.close()
    assert not backend.calls


def test_unavailable_original_retains_pending_until_next_start(tmp_path):
    backend = FakeWindows()
    service = TemporarySystemSettings(tmp_path, backend)
    service.apply("cable")
    backend.available.remove("original-mic")
    assert service.close()["status"] == "pending"
    assert service.journal.exists()
    backend.available.add("original-mic")
    recovery = TemporarySystemSettings(tmp_path, backend)
    assert recovery.recover()["status"] == "restored"
    assert backend.default == "original-mic"
    recovery.close()


def test_stale_prepared_journal_recovers_after_simulated_process_death(tmp_path):
    backend = FakeWindows()
    service = TemporarySystemSettings(tmp_path, backend)
    service.apply("cable")
    entry = json.loads(service.journal.read_text(encoding="utf-8"))
    entry["phase"] = "prepared"
    service._write(entry)
    service._owner.close()  # The OS also releases this handle on process death.
    service._owner = None
    recovery = TemporarySystemSettings(tmp_path, backend)
    assert recovery.recover()["status"] == "restored"
    assert backend.default == "original-mic"
    recovery.close()
    service.close()


def test_second_owner_cannot_apply_or_restore_first_owner(tmp_path):
    backend = FakeWindows()
    first = TemporarySystemSettings(tmp_path, backend)
    second = TemporarySystemSettings(tmp_path, backend)
    try:
        assert first.apply("cable")["ok"]
        assert second.apply("cable")["status"] == "busy"
        assert second.recover()["status"] == "busy"
        second.close()
        assert backend.default == "cable"
    finally:
        first.close()
    assert backend.default == "original-mic"


def test_journal_write_failure_prevents_windows_mutation(tmp_path, monkeypatch):
    backend = FakeWindows()
    service = TemporarySystemSettings(tmp_path, backend)
    monkeypatch.setattr(service, "_write", lambda _: (_ for _ in ()).throw(OSError("disk full")))
    assert service.apply("cable")["status"] == "failed"
    assert backend.calls == []
    service.close()


def test_setter_that_changes_default_then_raises_is_rolled_back(tmp_path):
    backend = FakeWindows()
    service = TemporarySystemSettings(tmp_path, backend)

    def partially_fail(identity):
        if identity == "cable":
            backend.default = identity
            raise OSError("readback failed")

    backend.on_set = partially_fail
    assert service.apply("cable")["status"] == "failed"
    assert backend.default == "original-mic"
    assert backend.calls == ["cable", "original-mic"]
    assert not service.journal.exists()
    service.close()


def test_corrupt_journal_is_retained_without_setting_windows(tmp_path):
    backend = FakeWindows()
    service = TemporarySystemSettings(tmp_path, backend)
    service.journal.write_text('{"version": 500}', encoding="utf-8")
    assert service.recover()["status"] == "pending"
    assert backend.calls == []
    assert service.journal.exists()
    service.close()


def test_standard_cable_pair_does_not_guess_16_channel_or_ambiguous_endpoints(monkeypatch):
    backend = WindowsAudioBackend()
    render = Endpoint("render", "CABLE Input (VB-Audio Virtual Cable)", 1, "render")
    capture = Endpoint("capture", "CABLE Output (VB-Audio Virtual Cable)", 1, "capture")
    extended = Endpoint("16ch", "CABLE In 16ch (VB-Audio Virtual Cable)", 1, "render")
    monkeypatch.setattr(backend, "render_endpoints", lambda: [extended, render])
    monkeypatch.setattr(backend, "capture_endpoints", lambda: [capture])
    assert backend.discover_standard_cable_pair().render.id == "render"
    assert backend.find_cable_capture("wasapi:output:25:CABLE Input (VB-Audio Virtual Cable)").id == "capture"
    assert backend.find_cable_capture(extended.name) is None
    monkeypatch.setattr(backend, "capture_endpoints", lambda: [capture, capture])
    assert backend.discover_standard_cable_pair() is None

