CREATE TABLE IF NOT EXISTS nanormm_actions (
    id              BIGSERIAL PRIMARY KEY,
    action_id       TEXT NOT NULL UNIQUE,
    tool_name       TEXT NOT NULL,
    args            JSONB NOT NULL,
    policy_decision TEXT NOT NULL,
    pending_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    approved_by     TEXT,
    approved_at     TIMESTAMPTZ,
    rejected_by     TEXT,
    rejected_at     TIMESTAMPTZ,
    reject_reason   TEXT,
    executed_at     TIMESTAMPTZ,
    result          JSONB,
    slack_message_ts TEXT
);

CREATE INDEX IF NOT EXISTS idx_nanormm_actions_tool ON nanormm_actions (tool_name);
CREATE INDEX IF NOT EXISTS idx_nanormm_actions_pending_at ON nanormm_actions (pending_at DESC);
