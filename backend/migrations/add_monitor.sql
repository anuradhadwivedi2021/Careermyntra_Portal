-- Monitor config table
CREATE TABLE IF NOT EXISTS monitor_config (
    id               SERIAL PRIMARY KEY,
    alert_email      VARCHAR(200) NOT NULL,
    app_password     VARCHAR(200) NOT NULL,
    interval_seconds INTEGER DEFAULT 120,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- URLs to monitor
CREATE TABLE IF NOT EXISTS monitor_urls (
    id         SERIAL PRIMARY KEY,
    url        TEXT NOT NULL,
    label      VARCHAR(200),
    is_active  BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Saved snapshots for comparison
CREATE TABLE IF NOT EXISTS monitor_snapshots (
    url_id     INTEGER PRIMARY KEY REFERENCES monitor_urls(id),
    content    TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Default URL insert
INSERT INTO monitor_urls (url, label)
VALUES ('https://mahafyjcadmissions.in/landing', 'MahaFYJC Admissions')
ON CONFLICT DO NOTHING;