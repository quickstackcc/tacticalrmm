from datetime import datetime, timedelta, timezone

import pytest

from tripwire_watch.models import AuditRow
from tripwire_watch.rules import apply_dedupe, evaluate, new_state
from tripwire_watch.settings import TripwireSettings

NOW = datetime(2026, 7, 16, 18, 0, 0, tzinfo=timezone.utc)  # 1pm CDT — business hours


@pytest.fixture
def cfg(monkeypatch):
    monkeypatch.setenv("TRIPWIRE_DB_DSN", "postgresql://unused")
    monkeypatch.setenv("TRIPWIRE_SLACK_WEBHOOK", "https://hooks.slack.example/unused")
    return TripwireSettings()


def row(id=1, action="login", object_type="user", ts=NOW, username="jim", **kw):
    return AuditRow(
        id=id, entry_time=ts, username=username, action=action, object_type=object_type, **kw
    )


def rules_fired(alerts):
    return {a.rule for a in alerts}


class TestBulkExec:
    def test_fires_above_threshold_within_window(self, cfg):
        st = new_state()
        rows = [
            row(id=i, action="execute_script", object_type="agent", agent_id=f"agent-{i}",
                ts=NOW - timedelta(seconds=30))
            for i in range(4)
        ]
        alerts = evaluate(rows, st, NOW, cfg)
        assert "bulk_exec" in rules_fired(alerts)

    def test_quiet_at_threshold(self, cfg):
        st = new_state()
        rows = [
            row(id=i, action="execute_script", object_type="agent", agent_id=f"agent-{i}",
                ts=NOW - timedelta(seconds=30))
            for i in range(3)
        ]
        assert "bulk_exec" not in rules_fired(evaluate(rows, st, NOW, cfg))

    def test_window_prunes_old_events(self, cfg):
        st = new_state()
        old = [
            row(id=i, action="execute_script", object_type="agent", agent_id=f"agent-{i}",
                ts=NOW - timedelta(seconds=300))
            for i in range(3)
        ]
        evaluate(old, st, NOW - timedelta(seconds=240), cfg)
        fresh = [row(id=10, action="execute_script", object_type="agent", agent_id="agent-x")]
        alerts = evaluate(fresh, st, NOW, cfg)
        assert "bulk_exec" not in rules_fired(alerts)
        assert len(st["exec_events"]) == 1

    def test_same_agent_repeatedly_does_not_fire(self, cfg):
        st = new_state()
        rows = [
            row(id=i, action="execute_script", object_type="agent", agent_id="agent-1",
                ts=NOW - timedelta(seconds=10))
            for i in range(10)
        ]
        assert "bulk_exec" not in rules_fired(evaluate(rows, st, NOW, cfg))


class TestBulkAction:
    def test_any_bulk_action_fires(self, cfg):
        alerts = evaluate(
            [row(action="bulk_action", object_type="bulk", message="bulk script on Site A")],
            new_state(), NOW, cfg,
        )
        assert "bulk_action" in rules_fired(alerts)


class TestIsolationKeyword:
    def test_keyword_match_fires(self, cfg):
        alerts = evaluate(
            [row(action="execute_command", object_type="agent", agent_id="a1",
                 message="ran: netsh advfirewall set allprofiles state on")],
            new_state(), NOW, cfg,
        )
        assert "isolation_keyword" in rules_fired(alerts)

    def test_benign_command_quiet(self, cfg):
        alerts = evaluate(
            [row(action="execute_command", object_type="agent", agent_id="a1",
                 message="ran: Get-Process")],
            new_state(), NOW, cfg,
        )
        assert "isolation_keyword" not in rules_fired(alerts)


class TestScriptAndUserChanges:
    def test_script_modify_fires(self, cfg):
        alerts = evaluate(
            [row(action="modify", object_type="script", message="edited Win Defender Purge")],
            new_state(), NOW, cfg,
        )
        assert "script_change" in rules_fired(alerts)

    def test_script_view_quiet(self, cfg):
        alerts = evaluate(
            [row(action="view", object_type="script", message="viewed a script")],
            new_state(), NOW, cfg,
        )
        assert rules_fired(alerts) == set()

    def test_user_add_fires(self, cfg):
        alerts = evaluate(
            [row(action="add", object_type="user", message="jim added user eve")],
            new_state(), NOW, cfg,
        )
        assert "user_added" in rules_fired(alerts)

    def test_role_modify_fires(self, cfg):
        alerts = evaluate(
            [row(action="modify", object_type="role", message="modified Administrator role")],
            new_state(), NOW, cfg,
        )
        assert "role_change" in rules_fired(alerts)


class TestLoginRules:
    def test_new_ip_fires_and_becomes_known(self, cfg):
        st = new_state()
        st["known_ips"] = {"jim": ["1.2.3.4"]}
        r = row(action="login", debug_info={"ip": "5.6.7.8"})
        alerts = evaluate([r], st, NOW, cfg)
        assert "login_new_ip" in rules_fired(alerts)
        # Second login from the same IP is now known -> quiet.
        alerts2 = evaluate([row(id=2, action="login", debug_info={"ip": "5.6.7.8"})], st, NOW, cfg)
        assert "login_new_ip" not in rules_fired(alerts2)

    def test_known_ip_quiet(self, cfg):
        st = new_state()
        st["known_ips"] = {"jim": ["1.2.3.4"]}
        alerts = evaluate([row(action="login", debug_info={"ip": "1.2.3.4"})], st, NOW, cfg)
        assert "login_new_ip" not in rules_fired(alerts)

    def test_nested_ip_extraction(self, cfg):
        st = new_state()
        nested = {"login_method": {"provider": "google", "ip": "9.9.9.9"}}
        r = row(action="login", debug_info=nested)
        alerts = evaluate([r], st, NOW, cfg)
        assert "login_new_ip" in rules_fired(alerts)

    def test_failed_login_burst(self, cfg):
        st = new_state()
        rows = [
            row(id=i, action="failed_login", ts=NOW - timedelta(seconds=i * 10))
            for i in range(3)
        ]
        alerts = evaluate(rows, st, NOW, cfg)
        assert "failed_login_burst" in rules_fired(alerts)

    def test_two_failures_quiet(self, cfg):
        st = new_state()
        rows = [row(id=i, action="failed_login") for i in range(2)]
        assert "failed_login_burst" not in rules_fired(evaluate(rows, st, NOW, cfg))


class TestOffhoursSession:
    def test_3am_local_fires(self, cfg):
        ts = datetime(2026, 7, 16, 8, 0, 0, tzinfo=timezone.utc)  # 3am CDT
        alerts = evaluate(
            [row(action="remote_session", object_type="agent", agent_id="a1", ts=ts)],
            new_state(), ts, cfg,
        )
        assert "offhours_session" in rules_fired(alerts)

    def test_1pm_local_quiet(self, cfg):
        alerts = evaluate(
            [row(action="remote_session", object_type="agent", agent_id="a1", ts=NOW)],
            new_state(), NOW, cfg,
        )
        assert "offhours_session" not in rules_fired(alerts)

    def test_11pm_local_fires(self, cfg):
        ts = datetime(2026, 7, 17, 4, 0, 0, tzinfo=timezone.utc)  # 11pm CDT Jul 16
        alerts = evaluate(
            [row(action="remote_session", object_type="agent", agent_id="a1", ts=ts)],
            new_state(), ts, cfg,
        )
        assert "offhours_session" in rules_fired(alerts)


class TestDedupe:
    def test_repeat_alert_suppressed_within_window(self, cfg):
        st = new_state()
        r = [row(action="modify", object_type="script", message="edited X")]
        first = apply_dedupe(evaluate(r, st, NOW, cfg), st, NOW, cfg)
        assert len(first) == 1
        again = apply_dedupe(
            evaluate([row(id=2, action="modify", object_type="script", message="edited X")],
                     st, NOW, cfg),
            st, NOW + timedelta(seconds=60), cfg,
        )
        assert again == []

    def test_repeat_alert_allowed_after_window(self, cfg):
        st = new_state()
        r = [row(action="modify", object_type="script", message="edited X")]
        apply_dedupe(evaluate(r, st, NOW, cfg), st, NOW, cfg)
        later = NOW + timedelta(seconds=cfg.dedupe_seconds + 1)
        again = apply_dedupe(
            evaluate([row(id=2, action="modify", object_type="script", message="edited X")],
                     st, later, cfg),
            st, later, cfg,
        )
        assert len(again) == 1

    def test_distinct_scripts_not_deduped(self, cfg):
        st = new_state()
        alerts = evaluate(
            [
                row(id=1, action="modify", object_type="script", message="edited X"),
                row(id=2, action="modify", object_type="script", message="edited Y"),
            ],
            st, NOW, cfg,
        )
        assert len(apply_dedupe(alerts, st, NOW, cfg)) == 2


class TestState:
    def test_last_id_advances(self, cfg):
        st = new_state()
        evaluate([row(id=41), row(id=42)], st, NOW, cfg)
        assert st["last_id"] == 42
