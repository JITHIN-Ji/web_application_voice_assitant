"""
Azure SQL Database Connection Testing Script
Tests ONLY the SQL database connection
"""
import os
import pyodbc
from dotenv import load_dotenv
import sys

# Load environment variables
load_dotenv()

print("=" * 70)
print("🔍 AZURE SQL DATABASE CONNECTION TEST")
print("=" * 70)

# ============================================
# TEST 1: Check SQL Environment Variables
# ============================================
print("\n📋 TEST 1: Checking SQL Environment Variables...")
print("-" * 70)

sql_vars = {
    'AZURE_SQL_SERVER': os.getenv('AZURE_SQL_SERVER'),
    'AZURE_SQL_DATABASE': os.getenv('AZURE_SQL_DATABASE'),
    'AZURE_SQL_USERNAME': os.getenv('AZURE_SQL_USERNAME'),
    'AZURE_SQL_PASSWORD': os.getenv('AZURE_SQL_PASSWORD'),
    'AZURE_SQL_DRIVER': os.getenv('AZURE_SQL_DRIVER')
}

all_vars_present = True
for var_name, var_value in sql_vars.items():
    if var_value:
        # Mask password
        if 'PASSWORD' in var_name:
            display_value = var_value[:3] + "***" + var_value[-3:] if len(var_value) > 6 else "***"
        else:
            display_value = var_value
        print(f"  ✅ {var_name}: {display_value}")
    else:
        print(f"  ❌ {var_name}: NOT SET")
        all_vars_present = False

if not all_vars_present:
    print("\n❌ ERROR: Some SQL environment variables are missing!")
    print("Please add them to your .env file:")
    print("\nAZURE_SQL_SERVER=acucognsqlserver.database.windows.net")
    print("AZURE_SQL_DATABASE=ambientscribe_sql")
    print("AZURE_SQL_USERNAME=adminuser")
    print("AZURE_SQL_PASSWORD=Keyisthepassword@7")
    print("AZURE_SQL_DRIVER={ODBC Driver 17 for SQL Server}")
    sys.exit(1)

print("\n✅ All SQL environment variables are set!")

# ============================================
# TEST 2: Check Available ODBC Drivers
# ============================================
print("\n📋 TEST 2: Checking Available ODBC Drivers...")
print("-" * 70)

try:
    available_drivers = pyodbc.drivers()
    
    if not available_drivers:
        print("  ❌ No ODBC drivers found!")
        print("\n  🔧 SOLUTION:")
        print("  Install ODBC Driver 17 for SQL Server")
        print("  Download: https://go.microsoft.com/fwlink/?linkid=2249004")
        sys.exit(1)
    
    print(f"  Found {len(available_drivers)} ODBC driver(s):")
    sql_drivers = []
    for driver in available_drivers:
        if 'SQL Server' in driver:
            print(f"    ✅ {driver}")
            sql_drivers.append(driver)
        else:
            print(f"    • {driver}")
    
    if not sql_drivers:
        print("\n  ❌ No SQL Server drivers found!")
        print("  Install ODBC Driver 17 for SQL Server")
        sys.exit(1)
    
    configured_driver = os.getenv('AZURE_SQL_DRIVER', '').strip('{}')
    if configured_driver in [d for d in available_drivers]:
        print(f"\n  ✅ Your configured driver is available: {configured_driver}")
    else:
        print(f"\n  ⚠️  Your configured driver '{configured_driver}' not found")
        print(f"  Available SQL Server drivers:")
        for sd in sql_drivers:
            print(f"    • {sd}")
        print(f"\n  Consider updating AZURE_SQL_DRIVER in .env to one of the above")
        
except Exception as e:
    print(f"  ❌ Error checking drivers: {e}")
    sys.exit(1)

# ============================================
# TEST 3: Test SQL Database Connection
# ============================================
print("\n📋 TEST 3: Testing Azure SQL Database Connection...")
print("-" * 70)

server = os.getenv('AZURE_SQL_SERVER')
database = os.getenv('AZURE_SQL_DATABASE')
username = os.getenv('AZURE_SQL_USERNAME')
password = os.getenv('AZURE_SQL_PASSWORD')
driver = os.getenv('AZURE_SQL_DRIVER')

print(f"  Server: {server}")
print(f"  Database: {database}")
print(f"  Username: {username}")
print(f"  Driver: {driver}")

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

print("\n  Attempting to connect...")

sql_connection_success = False
try:
    conn = pyodbc.connect(conn_str)
    print("  ✅ CONNECTION SUCCESSFUL!")
    
    cursor = conn.cursor()
    
    # Get SQL Server version
    print("\n  📊 Server Information:")
    cursor.execute("SELECT @@VERSION")
    version = cursor.fetchone()[0]
    version_line = version.split('\n')[0] if '\n' in version else version[:100]
    print(f"    {version_line}")
    
    # Get current database
    cursor.execute("SELECT DB_NAME()")
    current_db = cursor.fetchone()[0]
    print(f"    Connected to database: {current_db}")
    
    # List existing tables
    print("\n  📊 Existing Tables:")
    cursor.execute("""
        SELECT TABLE_NAME 
        FROM INFORMATION_SCHEMA.TABLES 
        WHERE TABLE_TYPE = 'BASE TABLE'
        ORDER BY TABLE_NAME
    """)
    tables = cursor.fetchall()
    
    if tables:
        for table in tables:
            # Get row count for each table
            cursor.execute(f"SELECT COUNT(*) FROM [{table[0]}]")
            count = cursor.fetchone()[0]
            print(f"    • {table[0]} ({count} rows)")
    else:
        print("    ⚠️  No tables found (database is empty)")
        print("    You need to create tables!")
    
    # Check for required tables
    print("\n  📊 Required Tables Check:")
    required_tables = ['logged_users', 'patients', 'soap_records', 'voice_recordings']
    existing_table_names = [t[0].lower() for t in tables]
    
    all_tables_exist = True
    missing_tables = []
    for required_table in required_tables:
        if required_table in existing_table_names:
            print(f"    ✅ {required_table}")
        else:
            print(f"    ❌ {required_table} - MISSING")
            all_tables_exist = False
            missing_tables.append(required_table)
    
    if not all_tables_exist:
        print(f"\n  ⚠️  Missing {len(missing_tables)} table(s): {', '.join(missing_tables)}")
    else:
        print("\n  ✅ All required tables exist!")
    
    conn.close()
    sql_connection_success = True
    
except pyodbc.Error as e:
    print(f"  ❌ CONNECTION FAILED!")
    print(f"\n  Error Details:")
    error_str = str(e)
    print(f"  {error_str}")
    
    print("\n  🔧 TROUBLESHOOTING:")
    
    if "TCP Provider" in error_str or "timeout" in error_str.lower():
        print("\n  ❌ FIREWALL ISSUE - Your IP is not allowed to connect")
        print("\n  SOLUTION (Choose one):")
        print("\n  Option A: Add Your IP to Firewall (Recommended for development)")
        print("    1. Go to Azure Portal (portal.azure.com)")
        print("    2. Navigate to: SQL databases → ambientscribe_sql")
        print("    3. Click 'Networking' or 'Firewalls and virtual networks'")
        print("    4. Click '+ Add your client IPv4 address'")
        print("    5. Check 'Allow Azure services and resources to access this server'")
        print("    6. Click 'Save' at the top")
        print("    7. Wait 1-2 minutes and run this test again")
        print("\n  Option B: Use Azure Portal Query Editor (Works immediately)")
        print("    1. Go to Azure Portal (portal.azure.com)")
        print("    2. Navigate to: SQL databases → ambientscribe_sql")
        print("    3. Click 'Query editor (preview)' in left sidebar")
        print("    4. Login with your credentials")
        print("    5. Run your SQL scripts there")
        
        # Try to get user's public IP
        try:
            import urllib.request
            ip = urllib.request.urlopen('https://api.ipify.org').read().decode('utf8')
            print(f"\n  Your current public IP: {ip}")
            print(f"  Add this IP to your Azure SQL firewall")
        except:
            pass
    
    elif "Login failed" in error_str:
        print("\n  ❌ AUTHENTICATION FAILED")
        print("\n  SOLUTION:")
        print("    Check your username and password in .env file")
        print("    Current username: {username}")
        print("    Make sure password is correct")
    
    elif "Data source name not found" in error_str:
        print("\n  ❌ ODBC DRIVER ISSUE")
        print("\n  SOLUTION:")
        print("    Install ODBC Driver 17 for SQL Server")
        print("    Download: https://go.microsoft.com/fwlink/?linkid=2249004")
        print("    After installing, restart your terminal and run this test again")
    
    else:
        print("\n  ❌ UNKNOWN ERROR")
        print("    Check your connection details in .env file")
    
    sys.exit(1)

# ============================================
# FINAL SUMMARY
# ============================================
print("\n" + "=" * 70)
print("📊 FINAL SUMMARY")
print("=" * 70)

if sql_connection_success:
    print("✅ Environment Variables: OK")
    print("✅ ODBC Drivers: OK")
    print("✅ Azure SQL Connection: SUCCESS")
    
    if all_tables_exist:
        print("✅ All Required Tables: EXIST")
        print("\n🎉 PERFECT! Your Azure SQL Database is fully set up!")
        print("\nNext steps:")
        print("  1. Test your backend: python app.py")
        print("  2. Or start with uvicorn: uvicorn app:app --reload")
    else:
        print(f"⚠️  Missing Tables: {', '.join(missing_tables)}")
        print("\n📝 Next step: Create missing tables")
        print("  Use Azure Portal Query Editor to create tables")
        print("  Or run: python create_tables.py (after fixing firewall)")
else:
    print("❌ Connection: FAILED")
    print("\nPlease fix the issues above and run this test again.")

print("=" * 70)

# ============================================
# TEST 4: View Stored Data
# ============================================
if sql_connection_success and all_tables_exist:
    print("\n\n📊 VIEWING STORED DATA")
    print("=" * 70)
    
    try:
        conn = pyodbc.connect(conn_str)
        cursor = conn.cursor()
        
        # Function to display table data
        def display_table_data(table_name):
            print(f"\n\n📋 TABLE: {table_name.upper()}")
            print("-" * 70)
            
            try:
                # Get column names
                cursor.execute(f"""
                    SELECT COLUMN_NAME, DATA_TYPE
                    FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_NAME = '{table_name}'
                    ORDER BY ORDINAL_POSITION
                """)
                columns = cursor.fetchall()
                
                if not columns:
                    print(f"  No columns found")
                    return
                
                # Get data
                cursor.execute(f"SELECT * FROM [{table_name}]")
                rows = cursor.fetchall()
                
                # Display column info
                print("\nColumns:")
                for col_name, data_type in columns:
                    print(f"  • {col_name} ({data_type})")
                
                # Display row count
                row_count = len(rows)
                print(f"\nTotal Records: {row_count}")
                
                if row_count > 0:
                    print("\nData:")
                    print("-" * 70)
                    
                    # Display each row
                    for i, row in enumerate(rows, 1):
                        print(f"\n  Record {i}:")
                        for j, col_info in enumerate(columns):
                            col_name = col_info[0]
                            value = row[j]
                            
                            # Format the value
                            if value is None:
                                display_value = "NULL"
                            elif isinstance(value, bytes):
                                display_value = f"[BINARY DATA: {len(value)} bytes]"
                            elif isinstance(value, str) and len(value) > 100:
                                display_value = value[:100] + "..."
                            elif hasattr(value, 'isoformat'):
                                display_value = value.isoformat()
                            else:
                                display_value = str(value)
                            
                            print(f"    {col_name}: {display_value}")
                else:
                    print("\n  [No records in this table]")
                
            except Exception as e:
                print(f"  ❌ Error reading {table_name}: {e}")
        
        # Display data for each table
        tables_to_display = ['logged_users', 'patients', 'soap_records', 'voice_recordings']
        
        for table in tables_to_display:
            if table.lower() in existing_table_names:
                display_table_data(table)
        
        conn.close()
        
        print("\n" + "=" * 70)
        print("✅ Data retrieval completed!")
        print("=" * 70)
        
    except Exception as e:
        print(f"\n❌ Error viewing data: {e}")
        import traceback
        traceback.print_exc()