import sqlite3
from werkzeug.security import generate_password_hash

DB_PATH = "grades.db"
NEW_PASSWORD = "admin123"   # เปลี่ยนเป็นรหัสใหม่ที่ต้องการ

conn = sqlite3.connect(DB_PATH)
hashed = generate_password_hash(NEW_PASSWORD)

conn.execute(
    "UPDATE scores SET password=? WHERE course='All' AND user_id='admin'",
    (hashed,)
)
conn.commit()
conn.close()

print("Admin password reset successfully to:", NEW_PASSWORD)
