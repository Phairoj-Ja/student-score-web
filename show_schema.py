import sqlite3

conn = sqlite3.connect("grades.db")
c = conn.cursor()

c.execute("PRAGMA table_info(courses)")
for row in c.fetchall():
    print(row)

conn.close()
