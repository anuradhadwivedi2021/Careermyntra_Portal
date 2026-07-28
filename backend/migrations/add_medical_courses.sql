-- migrations/add_medical_courses.sql
-- ============================================================================
-- Medical Admission Prediction Module — separate from predictor_courses
-- (Engineering) so the two systems can never collide or affect each other.
--
-- Run this once on the database:
--   psql -h localhost -p 5432 -U careermyntra_user -d careermyntra_db -f add_medical_courses.sql
--
-- Creates:
--   1. medical_courses          -> registry of medical courses (MBBS, BDS, ...)
--   2. medical_data_mbbs        -> MBBS cutoff data (NEET-based)
--   3. medical_data_bds         -> BDS cutoff data (NEET-based)
--
-- Adding a future course (BAMS, BHMS, Nursing, etc.) NEVER requires touching
-- this file again — use the Admin "Add New Medical Course" feature
-- (POST /medical-predictor/courses/new), which creates a new
-- medical_data_<slug> table + registers it here automatically, exactly the
-- same pattern already proven for the Engineering module's predictor_courses.
-- ============================================================================

CREATE TABLE IF NOT EXISTS medical_courses (
    id SERIAL PRIMARY KEY,
    slug VARCHAR(50) UNIQUE NOT NULL,
    display_name VARCHAR(200) NOT NULL,
    icon VARCHAR(10) DEFAULT '⚕️',
    table_name VARCHAR(100) NOT NULL,
    exam_type VARCHAR(50) DEFAULT 'NEET',
    display_order INT DEFAULT 0,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Template for every medical course table. All columns are NEET-oriented
-- (neet_marks_cutoff, neet_rank_cutoff) instead of the percentile columns
-- used by the Engineering module — this is the core requirement: medical
-- predictions run on NEET Marks/Rank, completely independent logic.
CREATE TABLE IF NOT EXISTS medical_data_mbbs (
    id SERIAL PRIMARY KEY,
    college_code VARCHAR(30) NOT NULL,
    college_name VARCHAR(300) NOT NULL,
    course_name VARCHAR(100) NOT NULL DEFAULT 'MBBS',
    category VARCHAR(50) NOT NULL,
    sub_category VARCHAR(50),
    seat_type VARCHAR(50) DEFAULT 'Government',   -- Government / Private / Deemed / Trust
    quota_code VARCHAR(50) DEFAULT 'State',        -- State / AIQ / Management / NRI / Institutional
    gender VARCHAR(20),
    cap_year VARCHAR(20) NOT NULL,
    cap_round VARCHAR(50) NOT NULL,
    neet_marks_cutoff NUMERIC(7,2),
    neet_rank_cutoff INTEGER,
    fees NUMERIC(12,2),
    university VARCHAR(300),
    district VARCHAR(100),
    location TEXT,
    address TEXT,
    naac_grade VARCHAR(10),
    nba_accredited VARCHAR(10) DEFAULT 'No',
    website VARCHAR(300),
    admission_authority VARCHAR(200),
    is_autonomous BOOLEAN DEFAULT false,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT mbbs_unique UNIQUE (
        college_name, course_name, cap_year, cap_round, category,
        sub_category, seat_type, quota_code, gender
    )
);

CREATE TABLE IF NOT EXISTS medical_data_bds (
    id SERIAL PRIMARY KEY,
    college_code VARCHAR(30) NOT NULL,
    college_name VARCHAR(300) NOT NULL,
    course_name VARCHAR(100) NOT NULL DEFAULT 'BDS',
    category VARCHAR(50) NOT NULL,
    sub_category VARCHAR(50),
    seat_type VARCHAR(50) DEFAULT 'Government',
    quota_code VARCHAR(50) DEFAULT 'State',
    gender VARCHAR(20),
    cap_year VARCHAR(20) NOT NULL,
    cap_round VARCHAR(50) NOT NULL,
    neet_marks_cutoff NUMERIC(7,2),
    neet_rank_cutoff INTEGER,
    fees NUMERIC(12,2),
    university VARCHAR(300),
    district VARCHAR(100),
    location TEXT,
    address TEXT,
    naac_grade VARCHAR(10),
    nba_accredited VARCHAR(10) DEFAULT 'No',
    website VARCHAR(300),
    admission_authority VARCHAR(200),
    is_autonomous BOOLEAN DEFAULT false,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT bds_unique UNIQUE (
        college_name, course_name, cap_year, cap_round, category,
        sub_category, seat_type, quota_code, gender
    )
);

CREATE INDEX IF NOT EXISTS idx_mbbs_district ON medical_data_mbbs (district);
CREATE INDEX IF NOT EXISTS idx_mbbs_filter   ON medical_data_mbbs (category, cap_year);
CREATE INDEX IF NOT EXISTS idx_bds_district  ON medical_data_bds (district);
CREATE INDEX IF NOT EXISTS idx_bds_filter    ON medical_data_bds (category, cap_year);

-- Seed the two initial courses. ON CONFLICT DO NOTHING so re-running this
-- file is always safe.
INSERT INTO medical_courses (slug, display_name, icon, table_name, exam_type, display_order, is_active)
VALUES
    ('mbbs', 'MBBS', '🩺', 'medical_data_mbbs', 'NEET', 1, true),
    ('bds',  'BDS',  '🦷', 'medical_data_bds',  'NEET', 2, true)
ON CONFLICT (slug) DO NOTHING;