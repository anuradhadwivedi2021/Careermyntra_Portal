#!/usr/bin/env python3
"""
fix_templates.py — Fix URL-encoded templates in the database
Run this once after deploying the backend fix
"""

import urllib.parse
from db import get_connection, get_cursor

def fix_templates():
    try:
        conn = get_connection()
        cur = conn.cursor()
        
        # Get all templates
        cur.execute("SELECT id, channel, subject, body FROM reminder_templates")
        templates = cur.fetchall()
        
        fixed_count = 0
        for tpl in templates:
            tpl_id = tpl[0]
            channel = tpl[1]
            subject = tpl[2] or ""
            body = tpl[3] or ""
            
            needs_fix = False
            
            # Check if URL-encoded
            if "%0A" in body or "%0D" in body or "%0A" in subject:
                needs_fix = True
                # Decode
                body = urllib.parse.unquote(body)
                subject = urllib.parse.unquote(subject)
            
            # Normalize line endings
            body = body.replace("\r\n", "\n").replace("\r", "\n")
            subject = subject.replace("\r\n", "\n").replace("\r", "\n")
            
            if needs_fix:
                cur.execute("""
                    UPDATE reminder_templates
                    SET subject = %s, body = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (subject, body, tpl_id))
                fixed_count += 1
                print(f"✅ Fixed {channel} template (ID: {tpl_id})")
        
        conn.commit()
        cur.close()
        conn.close()
        
        print(f"\n✅ Fixed {fixed_count} template(s) total")
        return True
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

if __name__ == "__main__":
    print("🔧 Starting template fix...")
    fix_templates()