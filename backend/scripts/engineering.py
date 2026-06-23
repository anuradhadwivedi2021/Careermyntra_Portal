# engineering.py — CareerMyntra Backend Script
# Processes: BE/BTech Cut-offs CAP Round PDF
# Output: Structured Excel with 15 columns

import pdfplumber
import re
import pandas as pd
import os

# ─── Patterns ───────────────────────────────────────────────
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

# ─── Helpers ────────────────────────────────────────────────
def get_category1(category: str) -> str:
    """Map raw category code to simplified category name."""
    if category.startswith("PWD"):
        return "PWD"
    for kw in CATEGORY_KEYWORDS:
        if kw in category:
            return kw
    return category[1:-1] if len(category) > 2 else category


def extract_rows(table, institute_code, university_name,
                 branch_code, course_name, status, university):
    """Extract rows from a pdfplumber table."""
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
            try:
                rank_str, rest = cell.split("(", 1)
                percentile_str = rest.rstrip(")")
                rank_str = rank_str.strip()
                percentile_str = percentile_str.strip()
                if not rank_str:
                    continue
            except Exception:
                continue

            gender  = category[0] if category else ""
            quota   = category[-1] if category else ""
            cat1    = get_category1(category)

            rows_data.append([
                institute_code, university_name, branch_code, course_name,
                status, university, category, rank_str, percentile_str,
                gender, quota, cat1, "B.Tech", "2025", "3"
            ])

    return rows_data


# ─── Main Process Function ───────────────────────────────────
def process(pdf_path: str, output_path: str, progress_callback=None) -> dict:
    """
    Main function called by Flask backend.
    
    Args:
        pdf_path: Path to uploaded PDF file
        output_path: Path where output Excel should be saved
        progress_callback: Optional function(percent, message) for live updates
    
    Returns:
        dict with keys: success, records, output_path, error
    """
    extracted_data = []

    def update(pct, msg):
        if progress_callback:
            progress_callback(pct, msg)
        print(f"[{pct}%] {msg}")

    try:
        update(5, "Opening PDF...")

        with pdfplumber.open(pdf_path) as pdf:
            total = len(pdf.pages)
            update(10, f"PDF loaded — {total} pages found")

            for page_num, page in enumerate(pdf.pages):
                # ── Extract text ──
                text = page.extract_text()
                if not text:
                    continue

                # ── College info ──
                college_match = college_pattern.search(text)
                if not college_match:
                    continue

                institute_code = int(college_match.group(1).strip())
                university_name = college_match.group(2).strip()

                status_match = status_pattern.search(text)
                home_match   = home_university_pattern.search(text)

                status     = status_match.group(1).strip() if status_match else "Unknown"
                university = home_match.group(1).strip()   if home_match   else "Unknown"

                # ── Course codes from word positions (only if college found) ──
                words = page.extract_words(x_tolerance=3, y_tolerance=3)
                course_positions = [
                    (w["top"], w["text"].strip())
                    for w in words
                    if course_code_re.fullmatch(w["text"].strip())
                ]
                course_positions.sort(key=lambda x: x[0])

                if not course_positions:
                    continue

                # ── Course name map ──
                course_name_map = {
                    m.group(1).strip(): m.group(2).strip()
                    for m in course_full_re.finditer(text)
                }

                # ── Tables (lazy — only when course positions found) ──
                found_tables = page.find_tables(table_settings={
                    "vertical_strategy": "lines",
                    "horizontal_strategy": "lines",
                    "snap_tolerance": 3,
                    "join_tolerance": 3,
                    "edge_min_length": 3,
                    "min_words_vertical": 1,
                    "min_words_horizontal": 1,
                })
                for ft in found_tables:
                    table_top  = ft.bbox[1]
                    table_data = ft.extract()
                    if not table_data or len(table_data) < 2:
                        continue

                    # Assign nearest course above this table
                    assigned_course = None
                    best_y = -1
                    for (cy, code) in course_positions:
                        if cy <= table_top and cy > best_y:
                            best_y = cy
                            assigned_course = code

                    if not assigned_course:
                        assigned_course = course_positions[0][1]

                    cname = course_name_map.get(assigned_course, assigned_course)
                    rows  = extract_rows(
                        table_data, institute_code, university_name,
                        assigned_course, cname, status, university
                    )
                    extracted_data.extend(rows)

                # ── Progress ──
                pct = 10 + int(((page_num + 1) / total) * 75)
                if (page_num + 1) % 50 == 0 or page_num == total - 1:
                    update(pct, f"Processing page {page_num+1}/{total} — {len(extracted_data)} records")

        update(88, "Building Excel file...")

        # ── Build DataFrame ──
        df = pd.DataFrame(extracted_data, columns=COLUMNS)
        df["institute code"] = pd.to_numeric(df["institute code"], errors="coerce").astype("Int64")
        df["rank"]           = pd.to_numeric(df["rank"],           errors="coerce")
        df["percentile"]     = pd.to_numeric(df["percentile"],     errors="coerce")
        df["cap round"]      = pd.to_numeric(df["cap round"],      errors="coerce").astype("Int64")

        update(93, "Applying Excel formatting...")

        # ── Write Excel with formatting ──
        with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Cut-off Data")

            wb = writer.book
            ws = writer.sheets["Cut-off Data"]

            from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
            from openpyxl.utils import get_column_letter

            # Header style
            header_fill = PatternFill("solid", fgColor="1565C0")
            header_font = Font(color="FFFFFF", bold=True, size=11)
            thin = Side(style="thin", color="D0D7E3")
            border = Border(left=thin, right=thin, top=thin, bottom=thin)

            for cell in ws[1]:
                cell.fill      = header_fill
                cell.font      = header_font
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                cell.border    = border

            ws.row_dimensions[1].height = 30

            # Column widths
            col_widths = {
                "institute code": 14, "institute name": 40, "branch code": 14,
                "course name": 35,    "status": 22,         "university": 28,
                "category": 12,       "rank": 10,           "percentile": 14,
                "gender": 8,          "quota": 8,            "category(1)": 14,
                "branch": 10,         "year": 8,             "cap round": 10
            }
            for i, col in enumerate(COLUMNS, 1):
                ws.column_dimensions[get_column_letter(i)].width = col_widths.get(col, 14)

            # Alternate row colors
            light = PatternFill("solid", fgColor="EFF4FF")
            for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
                for cell in row:
                    if cell.row % 2 == 0:
                        cell.fill = light
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                    cell.border = border

            # Freeze header
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions

        update(100, f"Done! {len(df)} records saved.")

        return {
            "success": True,
            "records": len(df),
            "output_path": output_path,
            "error": None
        }

    except Exception as e:
        update(0, f"Error: {str(e)}")
        return {
            "success": False,
            "records": 0,
            "output_path": None,
            "error": str(e)
        }


# ─── Standalone Run ──────────────────────────────────────────
if __name__ == "__main__":
    import sys
    pdf  = sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\anura\Downloads\ANURADHA-28-05-2026\BTECH-DATA\BE BTech Cut-offs CAP III.pdf"
    out  = sys.argv[2] if len(sys.argv) > 2 else "cutoff_BTech_cap3_output.xlsx"
    result = process(pdf, out)
    print(result)