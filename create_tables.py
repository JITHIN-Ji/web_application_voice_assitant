import os
import pyodbc
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

server = os.getenv("AZURE_SQL_SERVER")
database = os.getenv("AZURE_SQL_DATABASE")
username = os.getenv("AZURE_SQL_USERNAME")
password = os.getenv("AZURE_SQL_PASSWORD")
driver = os.getenv("AZURE_SQL_DRIVER")

# Build connection string
conn_str = (
    f"DRIVER={driver};"
    f"SERVER={server};"
    f"DATABASE={database};"
    f"UID={username};"
    f"PWD={password};"
    f"Encrypt=yes;"
    f"TrustServerCertificate=no;"
    f"Connection Timeout=30;"
)

def reset_table(cursor, table_name):
    """Delete all data and reset identity counter."""
    print(f"🗑️ Clearing table: {table_name}...")

    try:
        # 1. Delete all records (safe even when FK exists)
        cursor.execute(f"DELETE FROM [{table_name}];")
        print(f"   ✔ Data deleted")

        # 2. Reset identity counter (restart ID from 1)
        cursor.execute(f"DBCC CHECKIDENT ('{table_name}', RESEED, 0);")
        print(f"   ✔ Identity reset")

    except Exception as e:
        print(f"   ❌ Error clearing {table_name}: {e}")

def main():
    try:
        print("\n🔗 Connecting to Azure SQL...")
        conn = pyodbc.connect(conn_str)
        cursor = conn.cursor()
        print("✅ Connected successfully!\n")

        # Delete order (child → parent)
        tables = [
            "voice_recordings",   # depends on soap_records
            "soap_records",       # depends on patients
            "patients",           # depends on logged_users
            "logged_users"
        ]

        for table in tables:
            reset_table(cursor, table)

        conn.commit()
        conn.close()
        print("\n🎉 All tables cleared successfully!")

    except Exception as e:
        print("\n❌ CONNECTION FAILED:", e)

if __name__ == "__main__":
    main()
