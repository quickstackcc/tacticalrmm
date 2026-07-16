import json
from datetime import datetime, timezone

from tripwire_watch.export import export_rows
from tripwire_watch.models import AuditRow

ROW = AuditRow(
    id=7,
    entry_time=datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc),
    username="jim",
    action="login",
    object_type="user",
    debug_info={"ip": "1.2.3.4"},
)


def test_export_appends_jsonl(tmp_path):
    path = str(tmp_path / "sub" / "audit.jsonl")
    export_rows(path, [ROW])
    export_rows(path, [ROW])
    lines = open(path).read().splitlines()
    assert len(lines) == 2
    rec = json.loads(lines[0])
    assert rec["audit_id"] == 7
    assert rec["username"] == "jim"
    assert rec["debug_info"] == {"ip": "1.2.3.4"}


def test_disabled_when_path_empty(tmp_path):
    export_rows("", [ROW])  # must not raise


def test_failure_swallowed():
    export_rows("/proc/definitely/not/writable/audit.jsonl", [ROW])  # must not raise
