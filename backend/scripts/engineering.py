import pdfplumber
import re
import pandas as pd
import os
import gc  # Added for memory cleanup

college_pattern      = re.compile(r"^\s*(\d{5})\s*-\s*(.+)", re.MULTILINE)
course_code_re       = re.compile(r"(\d{10}[A-Za-z]?)")
course_full_re       = re.compile(r"(\d{10}[A-Za-z]?)\s*-\s*(.+)")
status_pattern       = re.compile(r"Status:\s*(.+)")
home_university_pattern = re.compile(r"Home University\s*:\s*(.+)")

CATEGORY_KEYWORDS = [
    "OPEN", "OBC", "SC", "ST", "NTA", "NTB", "NTC", "NTD",
    "SEBC", "MI", "EWS", "TFWS", "ORPHAN", "VJ", "PWD"
]

COLUMNS = [
    "institute code", "institute name", "branch code", "course name",
    "status", "university", "category", "rank", "percentile",
    "gender", "quota", "category(1)", "branch", "year", "cap round"
]

def get_category1(category: str) -> str:
    if category.startswith("PWD"):
        return "PWD"
    for kw in CATEGORY_KEYWORDS:
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
            if not category or col_idx + 1 >= len(row):
                continue
            cell = row[col_idx + 1]
            if not cell or "(" not in cell:
                continue
            cell = cell.replace("\n", "")
            try:
                rank_str, rest = cell.split("(", 1)
                percentile_str = rest.rstrip(")")
                rank_str, percentile_str = rank_str.strip(), percentile_str.strip()
                if not rank_str:
                    continue
            except Exception:
                continue
            rows_data.append([
                institute_code, university_name, branch_code, course_name,
                status, university, category, rank_str, percentile_str,
                category[0] if category else "", category[-1] if category else "",
                get_category1(category), "B.Tech", "2025", "3"
            ])
    return rows_data

def process(pdf_path: str, output_path: str, progress_callback=None) -> dict:
    extracted_data = []
    def update(pct, msg):
        if progress_callback: progress_callback(pct, msg)
        print(f"[{pct}%] {msg}")

    try:
        update(5, "Opening PDF...")
        with pdfplumber.open(pdf_path) as pdf:
            total = len(pdf.pages)
            update(10, f"PDF loaded — {total} pages")
            for page_num, page in enumerate(pdf.pages):
                text = page.extract_text()
                if not text:
                    page.flush_cache()
                    continue
                college_match = college_pattern.search(text)
                if not college_match:
                    page.flush_cache()
                    continue
                institute_code = int(college_match.group(1).strip())
                university_name = college_match.group(2).strip()
                status = status_pattern.search(text).group(1).strip() if status_pattern.search(text) else "Unknown"
                university = home_university_pattern.search(text).group(1).strip() if home_university_pattern.search(text) else "Unknown"
                
                words = page.extract_words(x_tolerance=3, y_tolerance=3)
                course_positions = [(w["top"], w["text"].strip()) for w in words if course_code_re.fullmatch(w["text"].strip())]
                course_positions.sort(key=lambda x: x[0])
                if not course_positions:
                    page.flush_cache()
                    continue

                course_name_map = {m.group(1).strip(): m.group(2).strip() for m in course_full_re.finditer(text)}
                found_tables = page.find_tables(table_settings={"vertical_strategy": "lines", "horizontal_strategy": "lines", "snap_tolerance": 3})
                
                for ft in found_tables:
                    table_top, table_data = ft.bbox[1], ft.extract()
                    if not table_data or len(table_data) < 2: continue
                    assigned_course = next((code for cy, code in reversed(course_positions) if cy <= table_top), course_positions[0][1])
                    cname = course_name_map.get(assigned_course, assigned_course)
                    extracted_data.extend(extract_rows(table_data, institute_code, university_name, assigned_course, cname, status, university))
                
                # Critical Production Fixes for Memory Protection
                page.flush_cache()
                if (page_num + 1) % 25 == 0: gc.collect()
                if (page_num + 1) % 50 == 0 or page_num == total - 1:
                    update(10 + int(((page_num + 1) / total) * 75), f"Processing page {page_num+1}/{total}")

        gc.collect()
        update(88, "Building Excel...")
        df = pd.DataFrame(extracted_data, columns=COLUMNS)
        df["institute code"] = pd.to_numeric(df["institute code"], errors="coerce").astype("Int64")
        df["rank"] = pd.to_numeric(df["rank"], errors="coerce")
        df["percentile"] = pd.to_numeric(df["percentile"], errors="coerce")
        df["cap round"] = pd.to_numeric(df["cap round"], errors="coerce").astype("Int64")

        with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Cut-off Data")
            ws = writer.sheets["Cut-off Data"]
            
            from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
            from openpyxl.utils import get_column_letter

            header_fill, header_font = PatternFill("solid", fgColor="1565C0"), Font(color="FFFFFF", bold=True, size=11)
            thin = Side(style="thin", color="D0D7E3")
            border, center_nowrap = Border(left=thin, right=thin, top=thin, bottom=thin), Alignment(horizontal="center", vertical="center")
            
            for cell in ws[1]:
                cell.fill, cell.font, cell.alignment, cell.border = header_fill, header_font, center_nowrap, border

            col_widths = {"institute code": 14, "institute name": 40, "branch code": 14, "course name": 35, "status": 22, "university": 28}
            for i, col in enumerate(COLUMNS, 1):
                ws.column_dimensions[get_column_letter(i)].width = col_widths.get(col, 12)

            # Optimised Cell Level Painter
            light = PatternFill("solid", fgColor="EFF4FF")
            for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
                is_even = row[0].row % 2 == 0
                for cell in row:
                    if is_even: cell.fill = light
                    cell.alignment, cell.border = center_nowrap, border
            ws.freeze_panes, ws.auto_filter.ref = "A2", ws.dimensions

        update(100, "Done!")
        return {"success": True, "records": len(df), "output_path": output_path, "error": None}
    except Exception as e:
        return {"success": False, "records": 0, "output_path": None, "error": str(e)}