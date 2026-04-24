from pymongo import MongoClient, errors
from dotenv import load_dotenv
import os
from datetime import datetime, UTC

# Load environment variables
load_dotenv()

MONGO_URI = os.getenv("MONGO_URL")
client = MongoClient(MONGO_URI)
db = client["traffic_challan_system"]

# ===================== 1. OWNERS AND VEHICLES =====================
owners_collection = db["owners"]
owners_collection.drop() # Wipe old data for a clean slate
owners_collection.create_index("nic", unique=True) 

# Explicitly defined owners (7 Users Total)
owners_data = [
    {
        "nic": "35202-1234567-1",
        "name": "Ali Raza",
        "phone": "923001234567",
        "password": "password123",
        "created_at": datetime.now(UTC),
        "vehicles": ["LEB1234"]
    },
    {
        "nic": "42101-9876543-2",
        "name": "Muhammad Usman",
        "phone": "923337654321",
        "password": "password123",
        "created_at": datetime.now(UTC),
        "vehicles": ["KHI5678"]
    },
    {
        "nic": "61101-3344556-3",
        "name": "Ahsan Khan",
        "phone": "923015566778",
        "password": "password123",
        "created_at": datetime.now(UTC),
        "vehicles": ["ISB9087"]
    },
    {
        "nic": "37405-6677889-4",
        "name": "Bilal Ahmed",
        "phone": "923129988776",
        "password": "password123",
        "created_at": datetime.now(UTC),
        "vehicles": ["RWP3322"]
    },
    {
        "nic": "42301-7654321-9", 
        "name": "Umaima Asif",
        "phone": "923032823327", 
        "password": "password123",
        "created_at": datetime.now(UTC),
        "vehicles": ["GA14C6251"]
    },
    {
        "nic": "42201-5556667-8", 
        "name": "Sara Ahmed",
        "phone": "923223344556",
        "password": "password123",
        "created_at": datetime.now(UTC),
        "vehicles": ["LHR9999"]
    },
    {
        "nic": "35201-1122334-5", 
        "name": "Tariq Mahmood",
        "phone": "923009988776",
        "password": "password123",
        "created_at": datetime.now(UTC),
        "vehicles": ["PESH777"]
    }
]

try:
    owners_collection.insert_many(owners_data, ordered=False)
    print("✅ Dummy Owners inserted successfully (7 Users Total).")
except errors.BulkWriteError:
    print("⚠️ Some owners already exist.")

# Fetch owners back from DB
owners = list(owners_collection.find({}))
owners_map = {owner["nic"]: owner["_id"] for owner in owners}

# ===================== 2. CHALLANS AND PAYMENTS =====================
challans_collection = db["challans"]
challans_collection.drop() 
challans_collection.create_index("challan_number", unique=True)

# Explicitly defined challans 
challans_data = [
    {
        "challan_number": "CH-2026-1001",
        "owner_id": owners_map["35202-1234567-1"], 
        "vehicle_id": "LEB1234",
        "violation_type": "Signal Violation",
        "fine": 3000,
        "status": "UNPAID", 
        "issued_at": datetime.now(UTC)
    },
    {
        "challan_number": "CH-2026-1002",
        "owner_id": owners_map["42101-9876543-2"], 
        "vehicle_id": "KHI5678",
        "violation_type": "Speeding",
        "fine": 2000,
        "status": "PAID", 
        "issued_at": datetime.now(UTC)
    },
    {
        "challan_number": "CH-2026-1003",
        "owner_id": owners_map["61101-3344556-3"], 
        "vehicle_id": "ISB9087",
        "violation_type": "Seatbelt Violation",
        "fine": 3000,
        "status": "UNPAID", 
        "issued_at": datetime.now(UTC)
    },
    {
        "challan_number": "CH-2026-1004",
        "owner_id": owners_map["37405-6677889-4"], 
        "vehicle_id": "RWP3322",
        "violation_type": "Helmet Violation",
        "fine": 1000,
        "status": "PAID", 
        "issued_at": datetime.now(UTC)
    },
    {
        "challan_number": "CH-2026-1005",
        "owner_id": owners_map["42301-7654321-9"], 
        "vehicle_id": "GA14C6251",
        "violation_type": "Helmet Violation",
        "fine": 1000,
        "status": "UNPAID", 
        "issued_at": datetime.now(UTC)
    },
    {
        "challan_number": "CH-2026-1006",
        "owner_id": owners_map["42201-5556667-8"], 
        "vehicle_id": "LHR9999",
        "violation_type": "Phone Violation",
        "fine": 1000,
        "status": "UNPAID", 
        "issued_at": datetime.now(UTC)
    },
    {
        "challan_number": "CH-2026-1007",
        "owner_id": owners_map["35201-1122334-5"], 
        "vehicle_id": "PESH777",
        "violation_type": "Triple Riding",
        "fine": 2000,
        "status": "PAID", 
        "issued_at": datetime.now(UTC)
    }
]

try:
    challans_collection.insert_many(challans_data, ordered=False)
    print("✅ Static, predictable Challans have been inserted.")
except errors.BulkWriteError:
    print("⚠️ Some challans already existed.")

print("🎉 Database has been successfully set up with 7 predictable users!")