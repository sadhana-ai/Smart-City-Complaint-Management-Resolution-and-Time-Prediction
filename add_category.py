import sqlite3

conn = sqlite3.connect("complaints.db")
cursor = conn.cursor()

cursor.execute("""
    INSERT INTO categories (category_name, priority_level)
    SELECT ?, ?
    WHERE NOT EXISTS (
        SELECT 1 FROM categories
        WHERE category_name = ?
    )
""", ("Electricity", 2, "Electricity"))

conn.commit()

print("Electricity category added successfully!")
print("\nAvailable categories:")

for row in cursor.execute(
    "SELECT category_name FROM categories ORDER BY category_name"
):
    print("-", row[0])

conn.close()