-- ============================================================
-- Fix: predict() returns 0 results for ALL years because
-- course_name / admission_authority values in the DB don't
-- match what the frontend always sends ("B.E./B.Tech" and
-- "CET CELL"). Different upload batches used different text
-- for the same thing.
-- ============================================================

-- Check before (run first to see what's about to change)
SELECT cap_year, course_name, admission_authority, COUNT(*)
FROM predictor_data_be_btech
GROUP BY cap_year, course_name, admission_authority
ORDER BY cap_year;

-- Fix 1: 2025-26 batch used "B.Tech" instead of "B.E./B.Tech"
UPDATE predictor_data_be_btech
SET course_name = 'B.E./B.Tech'
WHERE course_name = 'B.Tech';

-- Fix 2: 2026-27 batch used the full authority name instead of "CET CELL"
UPDATE predictor_data_be_btech
SET admission_authority = 'CET CELL'
WHERE admission_authority ILIKE '%common entrance test%';

-- Verify after — should now show a single consistent
-- course_name/admission_authority pair across both years
SELECT cap_year, course_name, admission_authority, COUNT(*)
FROM predictor_data_be_btech
GROUP BY cap_year, course_name, admission_authority
ORDER BY cap_year;