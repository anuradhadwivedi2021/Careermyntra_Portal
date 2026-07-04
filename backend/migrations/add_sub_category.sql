ALTER TABLE cap_cutoff_data ADD COLUMN IF NOT EXISTS sub_category VARCHAR(50);

ALTER TABLE cap_cutoff_data DROP CONSTRAINT IF EXISTS cap_cutoff_unique;

ALTER TABLE cap_cutoff_data ADD CONSTRAINT cap_cutoff_unique
    UNIQUE (college_name, branch_name, cap_year, cap_round, category, sub_category, seat_type, gender, quota_code);