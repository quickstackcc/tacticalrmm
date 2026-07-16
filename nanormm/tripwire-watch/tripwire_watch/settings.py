from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TripwireSettings(BaseSettings):
    """Environment-driven configuration for tripwire-watch.

    Deliberately standalone: no dependency on trmm-mcp settings. A security
    tripwire should not share config (or failure modes) with the services it
    observes. The DB DSN should point at a read-only role limited to
    logs_auditlog (see README).
    """

    model_config = SettingsConfigDict(extra="ignore")

    db_dsn: str = Field(alias="TRIPWIRE_DB_DSN")
    slack_webhook: str = Field(alias="TRIPWIRE_SLACK_WEBHOOK")

    poll_seconds: int = Field(default=20, alias="TRIPWIRE_POLL_SECONDS")
    state_path: str = Field(
        default="/var/lib/nanormm/tripwire-state.json", alias="TRIPWIRE_STATE_PATH"
    )

    # Rule tuning. Defaults implement docs/quickstack/admin-hardening.md Phase 3.
    tz: str = Field(default="America/Chicago", alias="TRIPWIRE_TZ")
    offhours_start: int = Field(default=22, alias="TRIPWIRE_OFFHOURS_START")  # 10pm local
    offhours_end: int = Field(default=6, alias="TRIPWIRE_OFFHOURS_END")  # 6am local
    bulk_agent_threshold: int = Field(default=3, alias="TRIPWIRE_BULK_AGENT_THRESHOLD")
    bulk_window_seconds: int = Field(default=60, alias="TRIPWIRE_BULK_WINDOW_SECONDS")
    failed_login_threshold: int = Field(default=3, alias="TRIPWIRE_FAILED_LOGIN_THRESHOLD")
    failed_login_window_seconds: int = Field(
        default=600, alias="TRIPWIRE_FAILED_LOGIN_WINDOW_SECONDS"
    )
    dedupe_seconds: int = Field(default=600, alias="TRIPWIRE_DEDUPE_SECONDS")
    heartbeat_hours: int = Field(default=24, alias="TRIPWIRE_HEARTBEAT_HOURS")
    known_ip_bootstrap_days: int = Field(default=90, alias="TRIPWIRE_KNOWN_IP_BOOTSTRAP_DAYS")

    # When set, every consumed audit row is appended as a JSON line to this
    # file (tailed by the Ops Agent -> Cloud Logging -> GCS for immutable
    # off-VM retention). Empty string disables the export.
    audit_export_path: str = Field(default="", alias="TRIPWIRE_AUDIT_EXPORT_PATH")
