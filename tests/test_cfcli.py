import json

from malt import cfcli


def test_cf_command_uses_default_profile(monkeypatch):
    monkeypatch.setattr(cfcli.settings, "get", lambda key: "")
    assert cfcli.command("zones", "list") == ["cf", "zones", "list"]


def test_cf_command_uses_named_profile_once(monkeypatch):
    monkeypatch.setattr(cfcli.settings, "get", lambda key: "avaid")
    assert cfcli.command("auth", "whoami") == ["cf", "--profile=avaid", "auth", "whoami"]


def test_profile_is_used_by_json_wrappers(monkeypatch):
    seen = []

    def fake_json(args, timeout=20):
        seen.append(args)
        return []

    monkeypatch.setattr(cfcli.settings, "get", lambda key: "avaid")
    monkeypatch.setattr(cfcli, "_run_json", fake_json)
    cfcli.invalidate()
    cfcli.list_zones()
    cfcli.list_tunnels()
    cfcli.dns_records("example.com")
    assert all(args[1] == "--profile=avaid" for args in seen)
