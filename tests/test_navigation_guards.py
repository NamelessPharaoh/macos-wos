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


class TestSidePanelIsOpen:
    """A template score is not proof the panel opened."""

    def test_the_city_tab_proves_it(self, monkeypatch):
        monkeypatch.setattr(cc, "req_text", lambda k: [["City", [0, 0, 1, 1]]])
        assert cc.side_panel_is_open()

    def test_the_wilderness_tab_proves_it(self, monkeypatch):
        seen = []
        def read(key):
            seen.append(key)
            return [["Wilderness", [0, 0, 1, 1]]] if "Wilderness" in key else []
        monkeypatch.setattr(cc, "req_text", read)
        assert cc.side_panel_is_open()
        assert any("City" in k for k in seen)          # tried City first, fell through

    def test_neither_tab_means_the_panel_never_opened(self, monkeypatch):
        # Global.SidePanel peaks at 0.518 on a home screen with NO panel open, so
        # the threshold=0.5 four call sites used reported success against the map.
        monkeypatch.setattr(cc, "req_text", lambda k: [])
        assert not cc.side_panel_is_open()

    def test_a_wrong_screen_does_not_count(self, monkeypatch):
        monkeypatch.setattr(cc, "req_text", lambda k: [["Chief Profile", [0, 0, 1, 1]]])
        assert not cc.side_panel_is_open()

    def test_it_lives_in_exactly_one_place(self):
        # It was copy-pasted into collect.py and training_troops.py, identical but
        # for the tab order. Re-duplicating it is the regression to catch.
        import pathlib
        hits = [p for p in pathlib.Path("usecases").glob("*.py")
                if "def side_panel_is_open" in p.read_text()
                or "def _side_panel_is_open" in p.read_text()]
        assert hits == [], f"side_panel_is_open re-duplicated into {hits}"


class TestTrainingTroopsGuards:
    def _quiet(self, monkeypatch):
        import usecases.training_troops as tt
        monkeypatch.setattr(tt, "recalibrate", lambda: None)
        monkeypatch.setattr(tt, "tap_on_template", lambda *a, **k: True)
        monkeypatch.setattr(tt.time, "sleep", lambda *_: None)
        return tt

    @pytest.mark.parametrize("fn", ["train", "train_infantry", "train_lancer",
                                    "train_marksman"])
    def test_every_variant_bails_when_the_panel_did_not_open(self, monkeypatch, fn):
        tt = self._quiet(monkeypatch)
        monkeypatch.setattr(tt, "side_panel_is_open", lambda: False)
        monkeypatch.setattr(tt, "tap_on_text",
                            lambda *a, **k: pytest.fail("searched the wrong view"))
        args = () if fn == "train" else (1,)
        assert getattr(tt, fn)(*args) is None

    @pytest.mark.parametrize("fn", ["train_infantry", "train_lancer", "train_marksman"])
    def test_amount_is_required(self, monkeypatch, fn):
        # Amount=None reached `while(trained < Amount)` and raised TypeError deep
        # inside the task. These spend in-game resources: fail at the call site.
        tt = self._quiet(monkeypatch)
        with pytest.raises(TypeError):
            getattr(tt, fn)()

    @pytest.mark.parametrize("fn", ["train_infantry", "train_lancer", "train_marksman"])
    def test_no_variant_still_passes_the_defeated_threshold(self, fn):
        import inspect
        import usecases.training_troops as tt
        src = inspect.getsource(getattr(tt, fn))
        assert "threshold=0.5" not in src.replace("# threshold=0.5", "")


class TestAllianceArrival:
    """Migrating five hand-rolled checks must not flip their polarity."""

    def _arm(self, monkeypatch, arrived):
        import usecases.alliance as al
        calls = {"recalibrate": 0}
        monkeypatch.setattr(al, "ensure_screen", lambda *a, **k: arrived)
        monkeypatch.setattr(al, "recalibrate",
                            lambda: calls.__setitem__("recalibrate",
                                                      calls["recalibrate"] + 1))
        monkeypatch.setattr(al, "tap_on_text", lambda *a, **k: True)
        monkeypatch.setattr(al, "tap_on_template", lambda *a, **k: True)
        monkeypatch.setattr(al.time, "sleep", lambda *_: None)
        return al, calls

    def test_already_there_means_no_round_trip(self, monkeypatch):
        al, calls = self._arm(monkeypatch, arrived=True)
        al.tech_contribution()
        assert calls["recalibrate"] == 0

    def test_wrong_screen_re_navigates(self, monkeypatch):
        al, calls = self._arm(monkeypatch, arrived=False)
        al.tech_contribution()
        assert calls["recalibrate"] == 1

    def test_a_failed_read_still_re_navigates(self, monkeypatch):
        # Polarity preservation: the old code left `title` as req_text's raw list
        # on a failed read, so `!= "alliance"` was True and it re-navigated.
        # ensure_screen returns False there, so `not` must do the same.
        import usecases.alliance as al
        monkeypatch.setattr(al, "req_text", lambda *a, **k: None)
        al2, calls = self._arm(monkeypatch, arrived=False)
        al2.tech_contribution()
        assert calls["recalibrate"] == 1

    def test_no_hand_rolled_checks_remain(self):
        import pathlib
        src = pathlib.Path("usecases/alliance.py").read_text()
        assert 'title != "alliance"' not in src
        assert src.count("ensure_screen(") == 5
