import pdfplumber
import csv
import re

# PDF File Path
pdf_path = r"C:\Users\anura\Downloads\ANURADHA-28-05-2026\BTECH-DATA\BE BTech Cut-offs CAP III.pdf"
output_csv = r"C:\Users\anura\Downloads\ANURADHA-28-05-2026\BTECH-DATA\cutoff_data_BTech_cap3.csv"
# List to store extracted data
extracted_data = []

# Regex Patterns
college_pattern = re.compile(r"^\s*(\d{5})\s*-\s*(.+)", re.MULTILINE)
course_pattern = re.compile(r"^\s*(\d{10}[A-Za-z]?)\s*-\s*(.+)", re.MULTILINE)
status_pattern = re.compile(r"Status:\s*(.+)")
home_university_pattern = re.compile(r"Home University\s*:\s*(.+)")

# List of category substrings
category_keywords = ["OPEN", "OBC", "SC", "ST", "NTA", "NTB", "NTC", "NTD", "SEBC", "MI", "EWS", "TFWS", "ORPHAN"]

# Open the PDF
with pdfplumber.open(pdf_path) as pdf:
    for page in pdf.pages:
        text = page.extract_text()
        if not text:
            continue
        
        # Extract Institute Code & University Name
        college_match = college_pattern.search(text)
        if college_match:
            institute_code = f"'{college_match.group(1).strip()}"
            university_name = college_match.group(2).strip()
        else:
            continue
        
        # Extract Status and University dynamically
        status_match = status_pattern.search(text)
        home_university_match = home_university_pattern.search(text)
        status = status_match.group(1).strip() if status_match else "Unknown"
        university = home_university_match.group(1).strip() if home_university_match else "Unknown"
        
        # Extract Branch Code & Name
        course_entries = list(course_pattern.finditer(text))
        course_index = 0
        
        # Extract tables
        tables = page.extract_tables()
        
        for table in tables:
            if len(table) < 2:
                continue
            
            # Ensure course entries exist before proceeding
            if course_index >= len(course_entries):
                continue
            
            # Assign correct branch code and name
            branch_code = f"'{course_entries[course_index].group(1).strip()}"
            course_name = course_entries[course_index].group(2).strip()
            course_index += 1  # Move to the next course
            
            categories = [cat.strip() for cat in table[0][1:]]
            data_rows = table[1:]
            
            for row in data_rows:
                for col_idx, category in enumerate(categories):
                    if col_idx + 1 < len(row):  # +1 because categories skip first column
                        cell_data = row[col_idx + 1]
                        if cell_data and "(" in cell_data:
                            rank, percentile = cell_data.split("(")
                            percentile = percentile.strip(")")
                            gender = category[0] if category else ""
                            quota = category[-1] if category else ""
                            
                            # Determine category(1)
                            category_1 = ""
                            if category.startswith("PWD"):
                                category_1 = "PWD"
                            else:
                                for keyword in category_keywords:
                                    if keyword in category:
                                        category_1 = keyword
                                        break
                            
                            if not category_1:
                                category_1 = category[1:-1] if len(category) > 2 else category
                            
                            extracted_data.append([
                                institute_code, university_name, branch_code, course_name, status,
                                university, category, rank.strip(), percentile.strip(),
                                gender, quota, category_1, "B.Tech", "2024", "3"
                            ])

# Save to CSV
with open(output_csv, mode="w", newline="", encoding="utf-8") as file:
    writer = csv.writer(file)
    writer.writerow(["institute code", "institute name", "branch code", "course name", "status", "university", "category", "rank", "percentile", "gender", "quota", "category(1)", "branch", "year", "cap round"])
    writer.writerows(extracted_data)

print(f"Data successfully extracted and saved to {output_csv}")