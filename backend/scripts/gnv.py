import pdfplumber
import pandas as pd
import re
import os

def process(pdf_path, output_path, progress_callback=None):
    try:
        if progress_callback: progress_callback(10, "Reading GNM PDF...")
        records = []

        with pdfplumber.open(pdf_path) as pdf:
            total_pages = len(pdf.pages)
            for page_num, page in enumerate(pdf.pages):
                text = page.extract_text()
                if not text: continue
                lines = text.split('\n')

                for line in lines:
                    line = line.strip()
                    if not line or any(skip in line for skip in ['GOVERNMENT', 'Entrance', 'Admissions', 'PROVISIONAL', 'Note:', 'Printed On', 'Sr.', 'SML']):
                        continue

                    # Robust loose match configuration to protect against layout shift leaks
                    match = re.match(r'^\s*(\d+)\s+(\d+)\s+(\d+)\s+(.+?)\s+(F|M)\s+([A-Z]*)\s*(G\d+.*?|Choice.*)', line, re.IGNORECASE)
                    if match:
                        sr, sml, form_no, applicant, gender, category, rest = [match.group(i).strip() for i in range(1, 8)]
                        
                        if "Choice Not Available" in rest:
                            code, college, location, quota = '', 'Choice Not Available', '', ''
                        else:
                            code_match = re.match(r'(G\d+)\s*:\s*GNM\s+(.+?)\s+(ANM.*|GOPN.*|GSCH.*|$)', rest, re.IGNORECASE)
                            if code_match:
                                code = code_match.group(1).strip()
                                college_location = code_match.group(2).strip()
                                quota = code_match.group(3).strip()
                                parts = college_location.rsplit(' ', 1)
                                college, location = ('GNM ' + parts[0].strip(), parts[1].strip()) if len(parts) == 2 else ('GNM ' + college_location, '')
                            else:
                                code, college, location, quota = '', rest, '', ''

                        records.append({
                            'Sr': sr, 'SML': sml, 'Form No': form_no, 'Applicant Name': applicant,
                            'Gender': gender, 'Category': category, 'College Code': code,
                            'Course': 'GNM', 'College': college, 'Location': location, 'Quota': quota
                        })
                
                page.flush_cache()
                if progress_callback:
                    progress_callback(15 + int((page_num + 1) / total_pages * 70), f"Processing page {page_num + 1}/{total_pages}")

        if not records: records = _fallback_parse(pdf_path, progress_callback)
        df = pd.DataFrame(records, columns=['Sr', 'SML', 'Form No', 'Applicant Name', 'Gender', 'Category', 'College Code', 'Course', 'College', 'Location', 'Quota'])

        with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='GNM Data')
            ws = writer.sheets['GNM Data']
            for col in ws.columns:
                max_len = max((len(str(cell.value)) for cell in col if cell.value), default=10)
                ws.column_dimensions[col[0].column_letter].width = min(max_len + 2, 40)

        if progress_callback: progress_callback(100, f"Done! {len(records)} records extracted.")
        return {"success": True, "records": len(records)}
    except Exception as e:
        return {"success": False, "error": str(e)}

def _fallback_parse(pdf_path, progress_callback=None):
    records = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if not text: continue
            for line in text.split('\n'):
                line = line.strip()
                m = re.match(r'^(\d+)\s+(\d+)\s+(\d+)\s+(.+?)\s+(F|M)\s+([A-Z]*)\s+(.*)', line)
                if m:
                    sr, sml, form_no, name, gender, category, rest = m.group(1), m.group(2), m.group(3), m.group(4).strip(), m.group(5), m.group(6).strip(), m.group(7).strip()
                    if 'Choice Not Available' in rest:
                        college, code, location, quota = 'Choice Not Available', '', '', ''
                    else:
                        cm = re.match(r'(G\d+)\s*:\s*GNM\s+(.+?)\s+(ANM.*|GOPN.*|$)', rest, re.IGNORECASE)
                        if cm:
                            code = cm.group(1)
                            college = 'GNM ' + cm.group(2).strip()
                            quota = cm.group(3).strip()
                            cp = college.rsplit(' ', 1)
                            college, location = (cp[0], cp[1]) if len(cp) == 2 else (college, '')
                        else:
                            code, college, location, quota = '', rest, '', ''
                    records.append({'Sr': sr, 'SML': sml, 'Form No': form_no, 'Applicant Name': name, 'Gender': gender, 'Category': category, 'College Code': code, 'Course': 'GNM', 'College': college, 'Location': location, 'Quota': quota})
            page.flush_cache()
    return records