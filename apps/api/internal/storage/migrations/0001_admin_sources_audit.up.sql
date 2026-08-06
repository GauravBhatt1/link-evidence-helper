BEGIN;

CREATE TABLE admin_sources (
    id text PRIMARY KEY,
    display_name text NOT NULL,
    kind text NOT NULL,
    endpoint text NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    revision bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    CONSTRAINT admin_sources_id_format CHECK (id ~ '^[A-Za-z0-9_-]{1,80}$'),
    CONSTRAINT admin_sources_display_name_length CHECK (char_length(display_name) BETWEEN 1 AND 120),
    CONSTRAINT admin_sources_kind_allowed CHECK (kind IN ('http-json', 'http-html', 'browser-html')),
    CONSTRAINT admin_sources_endpoint_scheme CHECK (endpoint ~ '^https?://'),
    CONSTRAINT admin_sources_revision_positive CHECK (revision > 0),
    CONSTRAINT admin_sources_time_order CHECK (updated_at >= created_at)
);

CREATE INDEX admin_sources_enabled_id_idx ON admin_sources (enabled, id);

CREATE TABLE admin_audit_events (
    event_id text PRIMARY KEY,
    correlation_id text NOT NULL,
    actor text NOT NULL,
    action text NOT NULL,
    resource text NOT NULL,
    outcome text NOT NULL,
    occurred_at timestamptz NOT NULL,
    CONSTRAINT admin_audit_event_id_length CHECK (char_length(event_id) BETWEEN 1 AND 160),
    CONSTRAINT admin_audit_correlation_id_length CHECK (char_length(correlation_id) BETWEEN 1 AND 160),
    CONSTRAINT admin_audit_actor_allowed CHECK (actor = 'admin'),
    CONSTRAINT admin_audit_action_allowed CHECK (action IN ('source.create', 'source.update', 'source.disable')),
    CONSTRAINT admin_audit_resource_length CHECK (char_length(resource) BETWEEN 1 AND 160),
    CONSTRAINT admin_audit_outcome_allowed CHECK (outcome IN ('success', 'failure'))
);

CREATE INDEX admin_audit_events_occurred_at_idx ON admin_audit_events (occurred_at DESC, event_id DESC);
CREATE INDEX admin_audit_events_resource_idx ON admin_audit_events (resource, occurred_at DESC);

COMMIT;
