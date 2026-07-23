-- ═══════════════════════════════════════════════════════════
-- Migration: Course-based College Predictor architecture
-- Har course ka apna table, master list predictor_courses mein
-- ═══════════════════════════════════════════════════════════

-- 1. Master table: available courses list (cards ke liye)
CREATE TABLE IF NOT EXISTS predictor_courses (
    id SERIAL PRIMARY KEY,
    slug VARCHAR(50) UNIQUE NOT NULL,          -- 'be_btech', 'pharmacy', 'mbbs' etc.
    display_name VARCHAR(100) NOT NULL,        -- 'B.E. / B.Tech'
    icon VARCHAR(20) DEFAULT '📁',
    table_name VARCHAR(100) UNIQUE NOT NULL,   -- 'predictor_data_be_btech'
    is_active BOOLEAN DEFAULT true,
    display_order INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. B.E./B.Tech course ka apna dedicated data table
--    (structure same as cap_cutoff_data, jisse existing logic reuse ho)
CREATE TABLE IF NOT EXISTS predictor_data_be_btech (
    id SERIAL PRIMARY KEY,
    college_code VARCHAR(30) NOT NULL,
    college_name VARCHAR(300) NOT NULL,
    branch_name VARCHAR(200) NOT NULL,
    branch_code TEXT,
    district VARCHAR(100),
    university VARCHAR(300),
    cap_year VARCHAR(20) NOT NULL,
    cap_round VARCHAR(50) NOT NULL,
    category VARCHAR(50) NOT NULL,
    sub_category VARCHAR(50),
    seat_type VARCHAR(50) DEFAULT 'AI',
    exam_type VARCHAR(50) DEFAULT 'MHT-CET',
    gender VARCHAR(10),
    quota_code VARCHAR(10) DEFAULT 'S',
    course_name VARCHAR(200),
    cutoff_percentile NUMERIC(7,4),
    cutoff_score NUMERIC(8,2),
    fees NUMERIC(10,2),
    naac_grade VARCHAR(10),
    nba_accredited VARCHAR(10) DEFAULT 'No',
    placement_highest NUMERIC(12,2),
    placement_average NUMERIC(8,2),
    website VARCHAR(300),
    address TEXT,
    location TEXT,
    admission_authority VARCHAR(200),
    is_autonomous BOOLEAN DEFAULT false,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT be_btech_unique UNIQUE (
        college_name, branch_name, cap_year, cap_round, category,
        sub_category, seat_type, gender, quota_code, course_name, exam_type
    )
);

CREATE INDEX IF NOT EXISTS idx_be_btech_branch   ON predictor_data_be_btech (branch_name);
CREATE INDEX IF NOT EXISTS idx_be_btech_district  ON predictor_data_be_btech (district);
CREATE INDEX IF NOT EXISTS idx_be_btech_filter    ON predictor_data_be_btech (exam_type, category, cap_year);

-- 3. Register B.E./B.Tech in the master courses list
INSERT INTO predictor_courses (slug, display_name, icon, table_name, display_order)
VALUES ('be_btech', 'B.E. / B.Tech', '⚙️', 'predictor_data_be_btech', 1)
ON CONFLICT (slug) DO NOTHING;

-- 4. Migrate existing B.Tech data from cap_cutoff_data into the new table
INSERT INTO predictor_data_be_btech (
    college_code, college_name, branch_name, branch_code, district, university,
    cap_year, cap_round, category, sub_category, seat_type, exam_type,
    gender, quota_code, course_name, cutoff_percentile, cutoff_score, fees,
    naac_grade, nba_accredited, placement_highest, placement_average,
    website, address, location, admission_authority, is_autonomous
)
SELECT
    college_code, college_name, branch_name, branch_code, district, university,
    cap_year, cap_round, category, sub_category, seat_type, exam_type,
    gender, quota_code, course_name, cutoff_percentile, cutoff_score, fees,
    naac_grade, nba_accredited, placement_highest, placement_average,
    website, address, location, admission_authority, is_autonomous
FROM cap_cutoff_data
ON CONFLICT ON CONSTRAINT be_btech_unique DO NOTHING;

-- 5. Verify migration
SELECT 'predictor_data_be_btech' AS table_name, COUNT(*) FROM predictor_data_be_btech
UNION ALL
SELECT 'cap_cutoff_data (old)', COUNT(*) FROM cap_cutoff_data;