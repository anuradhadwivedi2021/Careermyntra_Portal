import pdfplumber
import re
import pandas as pd

pdf_path = r"C:\Users\anura\Downloads\ANURADHA-28-05-2026\BTECH-DATA\BE BTech Cut-offs CAP III.pdf"
output_xlsx = r"C:\Users\anura\Downloads\ANURADHA-28-05-2026\BTECH-DATA\cutoff_data_BTech_cap3.xlsx"

extracted_data = []

college_pattern = re.compile(r"^\s*(\d{5})\s*-\s*(.+)", re.MULTILINE)
course_code_re = re.compile(r"(\d{10}[A-Za-z]?)")
course_full_re = re.compile(r"(\d{10}[A-Za-z]?)\s*-\s*(.+)")
status_pattern = re.compile(r"Status:\s*(.+)")
home_university_pattern = re.compile(r"Home University\s*:\s*(.+)")

category_keywords = ["OPEN", "OBC", "SC", "ST", "NTA", "NTB", "NTC", "NTD",
                     "SEBC", "MI", "EWS", "TFWS", "ORPHAN"]

def get_category1(category):
    if category.startswith("PWD"):
        return "PWD"
    for kw in category_keywords:
        if kw in category:
            return kw
    return category[1:-1] if len(category) > 2 else category

def extract_rows(table, institute_code, university_name, branch_code, course_name, status, university):
    rows_data = []
    if len(table) < 2:
        return rows_data
    categories = [c.strip() if c else "" for c in table[0][1:]]
    for row in table[1:]:
        for col_idx, category in enumerate(categories):
            if not category:
                continue
            cell_idx = col_idx + 1
            if cell_idx >= len(row):
                continue
            cell = row[cell_idx]
            if not cell or "(" not in cell:
                continue
            cell = cell.replace("\n", "")
            rank_str, rest = cell.split("(", 1)
            percentile_str = rest.rstrip(")")
            rank_str = rank_str.strip()
            percentile_str = percentile_str.strip()
            if not rank_str:
                continue
            gender = category[0] if category else ""
            quota = category[-1] if category else ""
            cat1 = get_category1(category)
            rows_data.append([
                institute_code, university_name, branch_code, course_name,
                status, university, category, rank_str, percentile_str,
                gender, quota, cat1, "B.Tech", "2025", "3"
            ])
    return rows_data

with pdfplumber.open(pdf_path) as pdf:
    total = len(pdf.pages)
    print(f"Total pages: {total}")

    for page_num, page in enumerate(pdf.pages):
        text = page.extract_text()
        if not text:
            continue
        college_match = college_pattern.search(text)
        if not college_match:
            continue
        institute_code = int(college_match.group(1).strip())
        university_name = college_match.group(2).strip()
        status_match = status_pattern.search(text)
        home_university_match = home_university_pattern.search(text)
        status = status_match.group(1).strip() if status_match else "Unknown"
        university = home_university_match.group(1).strip() if home_university_match else "Unknown"

        words = page.extract_words()
        course_positions = []
        for w in words:
            if course_code_re.fullmatch(w["text"].strip()):
                course_positions.append((w["top"], w["text"].strip()))

        course_name_map = {}
        for m in course_full_re.finditer(text):
            course_name_map[m.group(1).strip()] = m.group(2).strip()

        course_positions.sort(key=lambda x: x[0])
        if not course_positions:
            continue

        found_tables = page.find_tables()

        for ft in found_tables:
            table_top = ft.bbox[1]
            table_data = ft.extract()
            if not table_data or len(table_data) < 2:
                continue
            assigned_course = None
            best_y = -1
            for (cy, code) in course_positions:
                if cy <= table_top and cy > best_y:
                    best_y = cy
                    assigned_course = code
            if not assigned_course:
                assigned_course = course_positions[0][1]
            cname = course_name_map.get(assigned_course, assigned_course)
            rows = extract_rows(table_data, institute_code, university_name,
                                assigned_course, cname, status, university)
            extracted_data.extend(rows)

        if (page_num + 1) % 100 == 0:
            print(f"  {page_num+1}/{total} pages done — {len(extracted_data)} records so far")

print(f"\nTotal records: {len(extracted_data)}")

columns = ["institute code", "institute name", "branch code", "course name", "status",
           "university", "category", "rank", "percentile", "gender", "quota",
           "category(1)", "branch", "year", "cap round"]

df = pd.DataFrame(extracted_data, columns=columns)
df["institute code"] = df["institute code"].astype(int)
df["rank"] = pd.to_numeric(df["rank"], errors="coerce")
df["percentile"] = pd.to_numeric(df["percentile"], errors="coerce")
df["cap round"] = df["cap round"].astype(int)

df.to_excel(output_xlsx, index=False)
print(f"Saved: {output_xlsx}")
print(df.head(5).to_string())