-- ============================================================
-- MIGRATION: Add Stream Master Table
-- File:      add_streams.sql
-- Run once:  psql -U postgres -d careermyntra_portal -f add_streams.sql
-- ============================================================

-- ── 1. Create streams table ──────────────────────────────────
CREATE TABLE IF NOT EXISTS streams (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(100) NOT NULL UNIQUE,
    icon        VARCHAR(10)  DEFAULT '📚',
    description TEXT,
    color       VARCHAR(20)  DEFAULT '#1565c0',
    created_at  TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
);

-- ── 2. Add stream_id column to courses ──────────────────────
ALTER TABLE courses ADD COLUMN IF NOT EXISTS stream_id INT REFERENCES streams(id) ON DELETE SET NULL;

-- ── 3. Seed default streams ──────────────────────────────────
INSERT INTO streams (name, icon, description, color) VALUES
    ('Medical',     '🏥', 'Medical professional courses including MBBS, BDS, Nursing and Allied Health Sciences', '#dc2626'),
    ('Engineering', '⚙️', 'Technical engineering courses including B.Tech, M.Tech and Diploma programs',         '#d97706'),
    ('Management',  '💼', 'Business and management courses including MBA, BBA, PGDM and BMS',                    '#7c3aed'),
    ('Law',         '⚖️', 'Legal studies including LLB, BA LLB and BBA LLB programs',                           '#065f46')
ON CONFLICT (name) DO NOTHING;

-- ── 4. Seed default courses under each stream ────────────────
-- (These are inserted only if they don't already exist by name)

-- Medical stream
WITH s AS (SELECT id FROM streams WHERE name = 'Medical')
INSERT INTO courses (name, exam, icon, stream_id)
SELECT unnested.name, unnested.exam, unnested.icon, s.id
FROM s, (VALUES
    ('MBBS',                 'NEET UG',  '🩺'),
    ('BDS',                  'NEET UG',  '🦷'),
    ('BAMS',                 'NEET UG',  '🌿'),
    ('BUMS',                 'NEET UG',  '☪️'),
    ('Nursing',              'NEET UG',  '💊'),
    ('Allied Health Sciences','NEET UG', '🏨'),
    ('Pharmacy',             'NEET UG',  '💉')
) AS unnested(name, exam, icon)
WHERE NOT EXISTS (SELECT 1 FROM courses c WHERE LOWER(c.name) = LOWER(unnested.name));

-- Engineering stream
WITH s AS (SELECT id FROM streams WHERE name = 'Engineering')
INSERT INTO courses (name, exam, icon, stream_id)
SELECT unnested.name, unnested.exam, unnested.icon, s.id
FROM s, (VALUES
    ('B.Tech',             'JEE Main',   '🔧'),
    ('M.Tech',             'GATE',       '⚙️'),
    ('Diploma Engineering','MHT-CET',    '📐')
) AS unnested(name, exam, icon)
WHERE NOT EXISTS (SELECT 1 FROM courses c WHERE LOWER(c.name) = LOWER(unnested.name));

-- Management stream
WITH s AS (SELECT id FROM streams WHERE name = 'Management')
INSERT INTO courses (name, exam, icon, stream_id)
SELECT unnested.name, unnested.exam, unnested.icon, s.id
FROM s, (VALUES
    ('MBA',  'CAT / MAH-MBA CET', '📊'),
    ('BBA',  'MHT-CET',           '📈'),
    ('PGDM', 'CAT / XAT',         '🎓'),
    ('BMS',  'MHT-CET',           '💹')
) AS unnested(name, exam, icon)
WHERE NOT EXISTS (SELECT 1 FROM courses c WHERE LOWER(c.name) = LOWER(unnested.name));

-- Law stream
WITH s AS (SELECT id FROM streams WHERE name = 'Law')
INSERT INTO courses (name, exam, icon, stream_id)
SELECT unnested.name, unnested.exam, unnested.icon, s.id
FROM s, (VALUES
    ('LLB',     'CLAT',          '📜'),
    ('BA LLB',  'CLAT / MH CET', '⚖️'),
    ('BBA LLB', 'CLAT / MH CET', '🏛️')
) AS unnested(name, exam, icon)
WHERE NOT EXISTS (SELECT 1 FROM courses c WHERE LOWER(c.name) = LOWER(unnested.name));

-- ── 5. Map existing courses by fuzzy name match ──────────────
-- (Try to auto-assign stream_id to existing courses based on name keywords)

UPDATE courses SET stream_id = (SELECT id FROM streams WHERE name = 'Medical')
WHERE stream_id IS NULL AND (
    LOWER(name) LIKE '%mbbs%' OR LOWER(name) LIKE '%bds%' OR
    LOWER(name) LIKE '%bams%' OR LOWER(name) LIKE '%bums%' OR
    LOWER(name) LIKE '%nursing%' OR LOWER(name) LIKE '%pharmacy%' OR
    LOWER(name) LIKE '%medical%' OR LOWER(name) LIKE '%allied%' OR
    LOWER(name) LIKE '%neet%'
);

UPDATE courses SET stream_id = (SELECT id FROM streams WHERE name = 'Engineering')
WHERE stream_id IS NULL AND (
    LOWER(name) LIKE '%engineering%' OR LOWER(name) LIKE '%b.tech%' OR
    LOWER(name) LIKE '%btech%' OR LOWER(name) LIKE '%m.tech%' OR
    LOWER(name) LIKE '%mtech%' OR LOWER(name) LIKE '%diploma%' OR
    LOWER(name) LIKE '%b.e.%' OR LOWER(name) LIKE '%jee%'
);

UPDATE courses SET stream_id = (SELECT id FROM streams WHERE name = 'Management')
WHERE stream_id IS NULL AND (
    LOWER(name) LIKE '%mba%' OR LOWER(name) LIKE '%bba%' OR
    LOWER(name) LIKE '%pgdm%' OR LOWER(name) LIKE '%bms%' OR
    LOWER(name) LIKE '%management%' OR LOWER(name) LIKE '%business%'
);

UPDATE courses SET stream_id = (SELECT id FROM streams WHERE name = 'Law')
WHERE stream_id IS NULL AND (
    LOWER(name) LIKE '%llb%' OR LOWER(name) LIKE '%law%' OR
    LOWER(name) LIKE '%legal%' OR LOWER(name) LIKE '%clat%'
);

-- ── Done ─────────────────────────────────────────────────────
SELECT 'Migration complete ✅' AS status;
SELECT s.name AS stream, COUNT(c.id) AS courses
FROM streams s
LEFT JOIN courses c ON c.stream_id = s.id
GROUP BY s.name ORDER BY s.name;