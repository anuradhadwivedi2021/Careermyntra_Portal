-- ============================================================
-- MIGRATION: Exam & Admissions Reminder Manager
-- File:      add_reminders.sql
-- Run once:  psql -U postgres -d careermyntra_portal -f add_reminders.sql
-- ============================================================

-- ── 1. Categories ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS reminder_categories (
    id         SERIAL PRIMARY KEY,
    name       VARCHAR(100) NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ── 2. Sub Categories ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS reminder_subcategories (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(100) NOT NULL UNIQUE,
    category_id INTEGER REFERENCES reminder_categories(id) ON DELETE SET NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ── 3. Events ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS reminder_events (
    id             SERIAL PRIMARY KEY,
    title          VARCHAR(300) NOT NULL,
    category_id    INTEGER REFERENCES reminder_categories(id) ON DELETE SET NULL,
    subcategory_id INTEGER REFERENCES reminder_subcategories(id) ON DELETE SET NULL,
    description    TEXT,
    event_date     DATE NOT NULL,
    event_time     TIME,
    start_dt       TIMESTAMP,
    end_dt         TIMESTAMP,
    priority       VARCHAR(20)  DEFAULT 'medium' CHECK (priority IN ('high','medium','low')),
    status         VARCHAR(20)  DEFAULT 'upcoming' CHECK (status IN ('upcoming','completed','cancelled')),
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ── 4. Event Attachments ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS reminder_event_attachments (
    id         SERIAL PRIMARY KEY,
    event_id   INTEGER NOT NULL REFERENCES reminder_events(id) ON DELETE CASCADE,
    filename   VARCHAR(300) NOT NULL,
    filepath   VARCHAR(500) NOT NULL,
    filesize   INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ── 5. Reminder Schedules ────────────────────────────────────
CREATE TABLE IF NOT EXISTS reminder_schedules (
    id         SERIAL PRIMARY KEY,
    event_id   INTEGER NOT NULL REFERENCES reminder_events(id) ON DELETE CASCADE,
    remind_at  TIMESTAMP NOT NULL,
    label      VARCHAR(50),        -- e.g. "7 Days Before", "1 Hour Before"
    is_sent    BOOLEAN DEFAULT FALSE,
    sent_at    TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ── 6. Event Recipients ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS reminder_recipients (
    id         SERIAL PRIMARY KEY,
    event_id   INTEGER NOT NULL REFERENCES reminder_events(id) ON DELETE CASCADE,
    type       VARCHAR(20) NOT NULL CHECK (type IN ('email','whatsapp')),
    value      VARCHAR(200) NOT NULL,   -- email address or phone number
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ── 7. Notification Templates ────────────────────────────────
CREATE TABLE IF NOT EXISTS reminder_templates (
    id             SERIAL PRIMARY KEY,
    channel        VARCHAR(20) NOT NULL UNIQUE CHECK (channel IN ('email','whatsapp','sms')),
    subject        VARCHAR(300),        -- only for email
    body           TEXT NOT NULL,
    updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ── 8. Notification Logs (master) ────────────────────────────
CREATE TABLE IF NOT EXISTS reminder_notification_logs (
    id           SERIAL PRIMARY KEY,
    event_id     INTEGER REFERENCES reminder_events(id) ON DELETE SET NULL,
    schedule_id  INTEGER REFERENCES reminder_schedules(id) ON DELETE SET NULL,
    channel      VARCHAR(20) NOT NULL,
    recipient    VARCHAR(200) NOT NULL,
    status       VARCHAR(20) DEFAULT 'pending' CHECK (status IN ('pending','sent','failed')),
    error_msg    TEXT,
    sent_at      TIMESTAMP,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ── 9. WhatsApp Logs ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS reminder_whatsapp_logs (
    id           SERIAL PRIMARY KEY,
    log_id       INTEGER REFERENCES reminder_notification_logs(id) ON DELETE CASCADE,
    to_number    VARCHAR(50) NOT NULL,
    message_body TEXT,
    twilio_sid   VARCHAR(100),
    status       VARCHAR(20),
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ── 10. Email Logs ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS reminder_email_logs (
    id           SERIAL PRIMARY KEY,
    log_id       INTEGER REFERENCES reminder_notification_logs(id) ON DELETE CASCADE,
    to_email     VARCHAR(200) NOT NULL,
    subject      VARCHAR(300),
    body         TEXT,
    smtp_response TEXT,
    status       VARCHAR(20),
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ── Seed: Default Categories ─────────────────────────────────
INSERT INTO reminder_categories (name) VALUES
    ('Entrance Exam'),
    ('Admission Process'),
    ('CAP Round'),
    ('Counseling'),
    ('Document Verification'),
    ('Scholarship'),
    ('Fee Payment'),
    ('College Reporting'),
    ('Other')
ON CONFLICT (name) DO NOTHING;

-- ── Seed: Default Sub Categories ─────────────────────────────
INSERT INTO reminder_subcategories (name) VALUES
    ('MHT-CET'),
    ('JEE Main'),
    ('JEE Advanced'),
    ('NEET UG'),
    ('FYJC'),
    ('Polytechnic'),
    ('Pharmacy'),
    ('Engineering Admission'),
    ('MBA/MCA'),
    ('Law'),
    ('Nursing')
ON CONFLICT (name) DO NOTHING;

-- ── Seed: Default Notification Templates ─────────────────────
INSERT INTO reminder_templates (channel, subject, body) VALUES
(
    'email',
    'Reminder: {{EventTitle}} on {{EventDate}}',
    'Dear Student,

This is a reminder for:

Event: {{EventTitle}}
Category: {{Category}}
Date: {{EventDate}}
Time: {{EventTime}}

Description:
{{EventDescription}}

Please complete the required action before the deadline.

Regards,
CareerMyntra Admission Guidance Team'
),
(
    'whatsapp',
    NULL,
    '🔔 Reminder Alert

Event: {{EventTitle}}
📅 Date: {{EventDate}}
⏰ Time: {{EventTime}}
Category: {{Category}}

{{EventDescription}}

This event is scheduled within the next {{ReminderDuration}}.

CareerMyntra
Admission Guidance Team'
)
ON CONFLICT (channel) DO NOTHING;

-- ── Done ─────────────────────────────────────────────────────
SELECT 'Migration complete ✅' AS status;
SELECT COUNT(*) AS categories FROM reminder_categories;
SELECT COUNT(*) AS subcategories FROM reminder_subcategories;
SELECT COUNT(*) AS templates FROM reminder_templates;