import json

from tripwire_watch.state import load_state, save_state


def test_load_missing_returns_default(tmp_path):
    st = load_state(str(tmp_path / "nope.json"))
    assert st["last_id"] == 0
    assert st["known_ips"] == {}


def test_roundtrip(tmp_path):
    path = str(tmp_path / "state.json")
    st = load_state(path)
    st["last_id"] = 99
    st["known_ips"]["jim"] = ["1.2.3.4"]
    save_state(path, st)
    st2 = load_state(path)
    assert st2["last_id"] == 99
    assert st2["known_ips"] == {"jim": ["1.2.3.4"]}


def test_partial_file_merged_with_defaults(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"last_id": 7}))
    st = load_state(str(path))
    assert st["last_id"] == 7
    assert "exec_events" in st and "last_alert" in st
