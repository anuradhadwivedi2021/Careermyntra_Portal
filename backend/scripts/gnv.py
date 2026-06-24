import pdfplumber
import pandas as pd
import re
import os


def process(pdf_path, output_path, progress_callback=None):
    """
    GNM CAP Round PDF processor
    Extracts: Sr, SML, Form No, Applicant, Gender, Category, Code, Course, College, Location, Quota
    """
    try:
        if progress_callback:
            progress_callback(10, "Reading GNM PDF...")

        records = []

        with pdfplumber.open(pdf_path) as pdf:
            total_pages = len(pdf.pages)

            for page_num, page in enumerate(pdf.pages):
                text = page.extract_text()
                if not text:
                    continue

                lines = text.split('\n')

                for line in lines:
                    line = line.strip()
                    if not line:
                        continue

                    # Skip header lines
                    if any(skip in line for skip in [
                        'GOVERNMENT OF MAHARASHTRA',
                        'State Common Entrance',
                        'Admissions to GNM',
                        'PROVISIONAL SELECTION',
                        'Note:', 'Printed On',
                        '---', '===',
                        'Sr.', 'SML', 'Form No',
                        'Last Date', 'Admitting',
                        'Candidate should',
                        'which this', 'Inter-se'
                    ]):
                        continue

                    # Match data lines — start with a number
                    # Pattern: Sr SML FormNo Applicant Gender Category [Code Course College Location] Quota
                    match = re.match(
                        r'^\s*(\d+)\s+(\d+)\s+(\d+)\s+(.+?)\s+(F|M)\s+([A-Z]+|)\s*(G\d+\s*:\s*GNM\s+.+?)\s+(ANM\s+\w+|ANM\s+\w+\s+\w+)\s*$',
                        line
                    )

                    if match:
                        sr = match.group(1).strip()
                        sml = match.group(2).strip()
                        form_no = match.group(3).strip()
                        applicant = match.group(4).strip()
                        gender = match.group(5).strip()
                        category = match.group(6).strip()
                        code_college = match.group(7).strip()
                        quota = match.group(8).strip()

                        # Parse code, course, college, location from code_college
                        # Example: G9013 : GNM CENT CIVIL HOSP HINGOLI
                        code_match = re.match(r'(G\d+)\s*:\s*GNM\s+(.+)', code_college)
                        if code_match:
                            code = code_match.group(1).strip()
                            college_location = code_match.group(2).strip()

                            # Last word is location
                            parts = college_location.rsplit(' ', 1)
                            if len(parts) == 2:
                                college = 'GNM ' + parts[0].strip()
                                location = parts[1].strip()
                            else:
                                college = 'GNM ' + college_location
                                location = ''
                        else:
                            code = ''
                            college = code_college
                            location = ''

                        records.append({
                            'Sr': sr,
                            'SML': sml,
                            'Form No': form_no,
                            'Applicant Name': applicant,
                            'Gender': gender,
                            'Category': category,
                            'College Code': code,
                            'Course': 'GNM',
                            'College': college,
                            'Location': location,
                            'Quota': quota
                        })
                    else:
                        # Try "Choice Not Available" pattern
                        match2 = re.match(
                            r'^\s*(\d+)\s+(\d+)\s+(\d+)\s+(.+?)\s+(F|M)\s+([A-Z]*)\s+Choice Not Available\.',
                            line
                        )
                        if match2:
                            records.append({
                                'Sr': match2.group(1).strip(),
                                'SML': match2.group(2).strip(),
                                'Form No': match2.group(3).strip(),
                                'Applicant Name': match2.group(4).strip(),
                                'Gender': match2.group(5).strip(),
                                'Category': match2.group(6).strip(),
                                'College Code': '',
                                'Course': 'GNM',
                                'College': 'Choice Not Available',
                                'Location': '',
                                'Quota': ''
                            })

                if progress_callback:
                    pct = 15 + int((page_num + 1) / total_pages * 70)
                    progress_callback(pct, f"Processing page {page_num + 1}/{total_pages} — {len(records)} records")

        if progress_callback:
            progress_callback(85, "Generating Excel...")

        if not records:
            # Fallback — try simple line parsing
            records = _fallback_parse(pdf_path, progress_callback)

        df = pd.DataFrame(records, columns=[
            'Sr', 'SML', 'Form No', 'Applicant Name', 'Gender', 'Category',
            'College Code', 'Course', 'College', 'Location', 'Quota'
        ])

        # Write Excel
        with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='GNM Data')

            ws = writer.sheets['GNM Data']
            # Auto width
            for col in ws.columns:
                max_len = max((len(str(cell.value)) for cell in col if cell.value), default=10)
                ws.column_dimensions[col[0].column_letter].width = min(max_len + 2, 40)

        if progress_callback:
            progress_callback(100, f"Done! {len(records)} records extracted.")

        return {"success": True, "records": len(records)}

    except Exception as e:
        return {"success": False, "error": str(e)}


def _fallback_parse(pdf_path, progress_callback=None):
    """Fallback: simpler regex for GNM lines"""
    records = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if not text:
                continue
            for line in text.split('\n'):
                line = line.strip()
                # Match: number number number NAME F/M CATEGORY ...
                m = re.match(r'^(\d+)\s+(\d+)\s+(\d+)\s+(.+?)\s+(F|M)\s+([A-Z]*)\s+(.*)', line)
                if m:
                    sr = m.group(1)
                    sml = m.group(2)
                    form_no = m.group(3)
                    name = m.group(4).strip()
                    gender = m.group(5)
                    category = m.group(6).strip()
                    rest = m.group(7).strip()

                    if 'Choice Not Available' in rest:
                        college = 'Choice Not Available'
                        code = ''
                        location = ''
                        quota = ''
                    else:
                        # Try to extract G-code
                        cm = re.match(r'(G\d+)\s*:\s*GNM\s+(.+?)\s+(ANM\s+\S+(?:\s+\S+)?)\s*$', rest)
                        if cm:
                            code = cm.group(1)
                            college = 'GNM ' + cm.group(2).strip()
                            quota = cm.group(3).strip()
                            # last word of college = location
                            cp = college.rsplit(' ', 1)
                            location = cp[1] if len(cp) == 2 else ''
                            college = cp[0] if len(cp) == 2 else college
                        else:
                            code = ''
                            college = rest
                            location = ''
                            quota = ''

                    records.append({
                        'Sr': sr, 'SML': sml, 'Form No': form_no,
                        'Applicant Name': name, 'Gender': gender,
                        'Category': category, 'College Code': code,
                        'Course': 'GNM', 'College': college,
                        'Location': location, 'Quota': quota
                    })

    if progress_callback:
        progress_callback(82, f"Fallback parse done — {len(records)} records")
    return records