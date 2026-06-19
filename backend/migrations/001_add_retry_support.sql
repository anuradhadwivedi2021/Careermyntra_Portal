# migrations/001_add_retry_support.sql
# Run this migration to add retry tracking to reminder_schedules

-- Add columns to track retries
ALTER TABLE reminder_schedules 
ADD COLUMN IF NOT EXISTS retry_count INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS last_retry_at TIMESTAMP,
ADD COLUMN IF NOT EXISTS next_retry_at TIMESTAMP;

-- Add index for efficient retry queries
CREATE INDEX IF NOT EXISTS idx_reminder_schedules_retry 
ON reminder_schedules(is_sent, next_retry_at);

-- Add index for efficient log queries
CREATE INDEX IF NOT EXISTS idx_reminder_notification_logs_status 
ON reminder_notification_logs(status, created_at DESC);

-- Add index for efficient recipient queries
CREATE INDEX IF NOT EXISTS idx_reminder_recipients_event 
ON reminder_recipients(event_id);

-- Create view for statistics
CREATE OR REPLACE VIEW reminder_stats AS
SELECT 
    'total_events' as metric,
    COUNT(DISTINCT e.id)::text as value
FROM reminder_events e
UNION ALL
SELECT 
    'upcoming_events',
    COUNT(DISTINCT e.id)::text
FROM reminder_events e
WHERE e.status = 'upcoming' AND e.event_date >= CURRENT_DATE
UNION ALL
SELECT 
    'total_recipients',
    COUNT(DISTINCT r.id)::text
FROM reminder_recipients r
UNION ALL
SELECT 
    'sent_reminders',
    COUNT(*)::text
FROM reminder_notification_logs
WHERE status = 'sent'
UNION ALL
SELECT 
    'failed_reminders',
    COUNT(*)::text
FROM reminder_notification_logs
WHERE status = 'failed'
UNION ALL
SELECT 
    'pending_reminders',
    COUNT(*)::text
FROM reminder_schedules
WHERE is_sent = FALSE AND remind_at <= CURRENT_TIMESTAMP;

-- Verify migration
SELECT '✓ Migration completed' as status;