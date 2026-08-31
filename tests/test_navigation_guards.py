"""Arrival verification, template-threshold pinning, and adb injection retry.

These cover the failure that took out a whole production run: an icon tap that
lands on the WRONG icon, reports success, and leaves the task reading a screen
it never reached.
"""
import json
import subprocess

import pytest

import cmd_program.screen_action as sa
import core.core as cc


class TestEnsureScreen:
    def test_accepts_matching_title(self, monkeypatch):
        monkeypatch.setattr(cc, "req_text", lambda k: [["Pet Adventure", [0, 0, 1, 1]]])
        assert cc.ensure_screen("k", "Pet Adventure")

    def test_accepts_despite_ocr_noise(self, monkeypatch):
        monkeypatch.setattr(cc, "req_text", lambda k: [["Pet Adventurе", [0, 0, 1, 1]]])
        assert cc.ensure_screen("k", "Pet Adventure")

    def test_rejects_a_different_screen(self, monkeypatch):
        # The real failure: landed on Mail, read a mail subject.
        monkeypatch.setattr(cc, "req_text",
                            lambda k: [["Gathering Income Report", [0, 0, 1, 1]]])
        assert not cc.ensure_screen("k", "Pet Adventure")

    def test_rejects_when_nothing_reads_back(self, monkeypatch):
        monkeypatch.setattr(cc, "req_text", lambda k: [])
        assert not cc.ensure_screen("k", "Pet Adventure")
        monkeypatch.setattr(cc, "req_text", lambda k: None)
        assert not cc.ensure_screen("k", "Pet Adventure")

    def test_survives_a_malformed_result(self, monkeypatch):
        monkeypatch.setattr(cc, "req_text", lambda k: [[]])
        assert not cc.ensure_screen("k", "Pet Adventure")

    def test_is_case_insensitive(self, monkeypatch):
        monkeypatch.setattr(cc, "req_text", lambda k: [["INFANTRY", [0, 0, 1, 1]]])
        assert cc.ensure_screen("k", "infantry")


class TestTemplateThresholdPinning:
    """A pinned threshold must be authoritative, not a decaying starting point."""

    def test_ambiguous_home_icons_are_pinned_high(self):
        cfg = json.load(open("references/icon/template_config.json"))
        # Measured on a real home screen: an absent icon peaks at 0.38-0.61
        # (Home.Pet hits 0.614 over Mail), a present one scores 0.92-0.97.
        for name in ("Home.Pet", "Home.Mail", "Home.Missions", "Home.Store",
                     "Home.Labyrinth", "Home.ChiefOrder", "Home.Arena",
                     "Global.SidePanel"):
            assert cfg[name]["threshold"] >= 0.85, name

    def test_power_icon_keeps_its_deliberate_low_threshold(self):
        cfg = json.load(open("references/icon/template_config.json"))
        assert cfg["Home.PowerIcon"]["threshold"] == 0.1

    def test_pinned_threshold_suppresses_decay(self, monkeypatch):
        """The bug: decay fired whenever the CALLER omitted a threshold, even
        with one pinned in config, sliding to a 0.6 floor that matches the
        wrong icon."""
        monkeypatch.setitem(cc.template_area, "Home.Pet", {"threshold": 0.85})
        seen = []
        monkeypatch.setattr(cc, "req_temp_match",
                            lambda name, threshold, **k: seen.append(threshold) or None)
        monkeypatch.setattr(cc.time, "sleep", lambda s: None)
        cc.tap_on_template("Home.Pet", wait=1.2)
        assert seen, "no match attempted"
        assert min(seen) >= 0.85, f"threshold decayed below the pin: {seen}"

    def test_unpinned_template_still_decays(self, monkeypatch):
        monkeypatch.delitem(cc.template_area, "Nope.Unpinned", raising=False)
        seen = []
        monkeypatch.setattr(cc, "req_temp_match",
                            lambda name, threshold, **k: seen.append(threshold) or None)
        monkeypatch.setattr(cc.time, "sleep", lambda s: None)
        cc.tap_on_template("Nope.Unpinned", wait=1.2)
        assert min(seen) < 0.8, f"expected decay for an unpinned template: {seen}"


class TestInjectionRetry:
    INJECT = ("Exception occurred while executing 'swipe': "
              "java.lang.SecurityException: Injecting to another application "
              "requires INJECT_EVENTS permission")

    @pytest.fixture(autouse=True)
    def _device(self, monkeypatch):
        monkeypatch.setattr(sa, "get_adb_devices", lambda: ["serial-a"])
        monkeypatch.setattr(sa.time, "sleep", lambda s: None)
        sa._device_id = None

    def test_classifier_only_fires_for_input_commands(self):
        assert sa.is_transient_injection_error(
            ["shell", "input", "swipe", "1", "2", "3", "4", "5"], self.INJECT)
        assert not sa.is_transient_injection_error(["shell", "dumpsys", "window"], self.INJECT)
        assert not sa.is_transient_injection_error(["shell", "input", "tap", "1", "1"], "")

    def test_transient_injection_is_retried_then_succeeds(self, monkeypatch):
        calls = {"n": 0}

        def flaky(cmd, **kwargs):
            calls["n"] += 1
            if calls["n"] < 2:
                raise subprocess.CalledProcessError(1, cmd, stderr=self.INJECT)
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(sa.subprocess, "run", flaky)
        sa.run_adb_command(["shell", "input", "swipe", "1", "2", "3", "4", "5"])
        assert calls["n"] == 2

    def test_injection_failure_does_not_invalidate_the_device(self, monkeypatch):
        def always(cmd, **kwargs):
            raise subprocess.CalledProcessError(1, cmd, stderr=self.INJECT)

        monkeypatch.setattr(sa.subprocess, "run", always)
        sa.resolve_device()
        with pytest.raises(RuntimeError, match="INJECT_EVENTS"):
            sa.run_adb_command(["shell", "input", "tap", "1", "1"])
        # The emulator was never gone; the cached serial must survive.
        assert sa._device_id == "serial-a"

    def test_retry_is_bounded(self, monkeypatch):
        calls = {"n": 0}

        def always(cmd, **kwargs):
            calls["n"] += 1
            raise subprocess.CalledProcessError(1, cmd, stderr=self.INJECT)

        monkeypatch.setattr(sa.subprocess, "run", always)
        with pytest.raises(RuntimeError):
            sa.run_adb_command(["shell", "input", "tap", "1", "1"])
        assert calls["n"] == sa.INJECT_RETRY_ATTEMPTS

    def test_non_injection_error_is_not_retried(self, monkeypatch):
        calls = {"n": 0}

        def boom(cmd, **kwargs):
            calls["n"] += 1
            raise subprocess.CalledProcessError(1, cmd, stderr="device 'serial-a' not found")

        monkeypatch.setattr(sa.subprocess, "run", boom)
        with pytest.raises(RuntimeError, match="not found"):
            sa.run_adb_command(["shell", "input", "tap", "1", "1"])
        assert calls["n"] == 1
        assert sa._device_id is None      # real loss still drops the cache
