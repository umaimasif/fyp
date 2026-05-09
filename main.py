import os
import time
import json
import random
import csv
import io
import cv2
import base64
import numpy as np
import re
from datetime import datetime, UTC
from fastapi import FastAPI, UploadFile, File, Request, Form
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv
from groq import Groq
from xhtml2pdf import pisa
from pymongo import MongoClient
from twilio.rest import Client as TwilioClient
from ultralytics import YOLO
from pydantic import BaseModel

# 1. Configuration & Client Setup
load_dotenv()
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
MONGO_URL = os.getenv("MONGO_URL")
TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_NUM = os.getenv("TWILIO_PHONE_NUMBER")

client = Groq(api_key=GROQ_API_KEY)

# Robust MongoDB Connection
try:
    mongo_client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    mongo_client.admin.command("ping")
    print("✅ MongoDB connected successfully!")
except Exception as e:
    print(f"⚠️ WARNING: MongoDB connection failed: {e}")
    mongo_client = None

db = mongo_client["traffic_challan_system"] if mongo_client is not None else None
challan_col = db["challans"] if db is not None else None
owner_col = db["owners"] if db is not None else None
officer_col = db["officers"] if db is not None else None
audit_col = db["audit_log"] if db is not None else None

# Seed default officer account if none exists
if officer_col is not None and officer_col.count_documents({}) == 0:
    officer_col.insert_one({
        "username": "officer",
        "password": "admin123",
        "name": "Default Officer",
        "badge_id": "OFC-001",
        "created_at": datetime.now(UTC)
    })
    print("✅ Seeded default officer (officer / admin123)")

# Twilio Setup
twilio_client = None
if TWILIO_SID and TWILIO_AUTH:
    twilio_client = TwilioClient(TWILIO_SID, TWILIO_AUTH)
else:
    print("⚠️ WARNING: Twilio credentials not set. WhatsApp alerts disabled.")

otp_store = {}  # {nic: {"otp": "123456", "expires": unix_timestamp}}

# --- DATA MODELS FOR FRONTEND ---
class RegisterData(BaseModel):
    name: str
    phone: str
    nic: str
    password: str

class LoginData(BaseModel):
    nic: str
    password: str

class RemoveVehicleData(BaseModel):
    nic: str
    vehicle_id: str

class ForgotPasswordData(BaseModel):
    nic: str

class ResetPasswordData(BaseModel):
    nic: str
    otp: str
    new_password: str

class OfficerLoginData(BaseModel):
    username: str
    password: str

class ChatMessage(BaseModel):
    message: str
    history: list = []

# --- LOAD CUSTOM YOLO MODEL ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
yolo_model_path = os.path.join(BASE_DIR, "Numberplate.pt")

try:
    plate_model = YOLO(yolo_model_path)
    print(f"✅ Loaded Custom Plate Model: {yolo_model_path}")
except Exception as e:
    print(f"⚠️ Error loading YOLO model: {e}")
    plate_model = None

app = FastAPI()

# 2. Setup
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(TEMPLATES_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# 3. HELPER: Clean Text
def clean_plate_text(text):
    if not text or text == "UNKNOWN": return None
    return re.sub(r'[^A-Z0-9]', '', text.upper())

# 4. HELPER: Find Best Frame
def extract_best_frame(video_path):
    cap = cv2.VideoCapture(video_path)
    max_conf = 0
    best_frame_b64 = None
    best_crop_b64 = None
    frame_count = 0
    
    while cap.isOpened():
        success, frame = cap.read()
        if not success: break
        frame_count += 1
        if frame_count % 5 != 0: continue

        if plate_model:
            results = plate_model(frame, verbose=False)
            for result in results:
                for box in result.boxes:
                    if int(box.cls[0]) == 0:
                        conf = float(box.conf[0])
                        if conf > max_conf:
                            max_conf = conf
                            _, buf = cv2.imencode(".jpg", frame)
                            best_frame_b64 = base64.b64encode(buf).decode('utf-8')
                            x1, y1, x2, y2 = box.xyxy[0].cpu().numpy().astype(int)
                            h, w, _ = frame.shape
                            x1, y1, x2, y2 = max(0, x1-5), max(0, y1-5), min(w, x2+5), min(h, y2+5)
                            crop = frame[y1:y2, x1:x2]
                            if crop.size > 0:
                                _, buf_crop = cv2.imencode(".jpg", crop)
                                best_crop_b64 = base64.b64encode(buf_crop).decode('utf-8')
    cap.release()
    
    if not best_frame_b64:
        cap = cv2.VideoCapture(video_path)
        cap.set(cv2.CAP_PROP_POS_MSEC, 1000)
        success, frame = cap.read()
        cap.release()
        if success:
            _, buf = cv2.imencode(".jpg", frame)
            best_frame_b64 = base64.b64encode(buf).decode('utf-8')
    return best_frame_b64, best_crop_b64


# ==========================================
# 5. API ROUTES
# ==========================================

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.get("/officer", response_class=HTMLResponse)
async def officer_page(request: Request):
    return templates.TemplateResponse(request=request, name="officer.html")


@app.post("/api/register")
async def register_user(data: RegisterData):
    if owner_col is None:
        return JSONResponse(status_code=503, content={"message": "Database unavailable."})
    
    existing = owner_col.find_one({"nic": data.nic})
    
    if existing:
        owner_col.update_one(
            {"nic": data.nic},
            {"$set": {"name": data.name, "phone": data.phone, "password": data.password}}
        )
        vehicle_count = len(existing.get("vehicles", []))
        return {"message": f"Account activated! Successfully fetched {vehicle_count} vehicle(s) from official database."}
    else:
        new_user = {
            "name": data.name,
            "phone": data.phone,
            "nic": data.nic,
            "password": data.password, 
            "vehicles": [] 
        }
        owner_col.insert_one(new_user)
        return {"message": "Account created, but no vehicles were found in the official database."}


@app.post("/api/login")
async def login_user(data: LoginData):
    if owner_col is None:
        return JSONResponse(status_code=503, content={"message": "Database unavailable."})
    user = owner_col.find_one({"nic": data.nic, "password": data.password})
    if user:
        return {"message": "Login successful!", "name": user.get("name", ""), "vehicles": user.get("vehicles", [])}
    return JSONResponse(status_code=400, content={"message": "Invalid CNIC or Password."})


@app.get("/api/my-challans/{nic}")
async def get_my_challans(nic: str):
    if owner_col is None:
        return JSONResponse(status_code=503, content={"message": "Database unavailable."})
    user = owner_col.find_one({"nic": nic})
    if not user:
        return JSONResponse(status_code=404, content={"message": "User not found."})
    
    vehicles = user.get("vehicles", [])
    challans = []
    
    if vehicles and challan_col is not None:
        raw = list(challan_col.find({"vehicle_id": {"$in": vehicles}}))
        for c in raw:
            issued = c.get("issued_at", datetime.now(UTC))
            challans.append({
                "challan_number": c.get("challan_number", "N/A"),
                "vehicle_id": c.get("vehicle_id", ""),
                "violation_type": c.get("violation_type", ""),
                "fine": c.get("fine", 0),
                "status": c.get("status", "UNPAID"),
                "issued_at": issued.strftime("%Y-%m-%d") if hasattr(issued, "strftime") else str(issued)
            })
    return {"challans": challans, "vehicles": vehicles, "name": user.get("name", "")}


@app.get("/api/download-challan/{challan_number}")
async def download_challan_citizen(challan_number: str):
    """Generates a PDF for the citizen based on the challan number in the database."""
    if challan_col is None or owner_col is None:
        return HTMLResponse("Database unavailable.")

    challan = challan_col.find_one({"challan_number": challan_number})
    if not challan:
        return HTMLResponse("Challan not found.")

    owner = owner_col.find_one({"_id": challan["owner_id"]})
    owner_name = owner.get("name", "Unknown") if owner else "Unknown"

    issued_date = challan.get("issued_at")
    date_str = issued_date.strftime("%Y-%m-%d") if hasattr(issued_date, "strftime") else str(issued_date)

    challan_data = {
        "challan_number": challan_number,
        "owner_name": owner_name,
        "type": challan.get("violation_type", ""),
        "vehicle": challan.get("vehicle_id", ""),
        "fine": challan.get("fine", 0),
        "plate": challan.get("vehicle_id", ""),
        "date": date_str,
        "status": challan.get("status", "UNPAID")
    }
    
    pdf_path = f"temp_{challan_number}.pdf"
    html_content = templates.get_template("challan_template.html").render(challan_data)
    
    with open(pdf_path, "w+b") as f:
        pisa.CreatePDF(html_content, dest=f)
        
    return FileResponse(pdf_path, filename=f"{challan_number}.pdf")


@app.post("/process-video")
async def process_video(request: Request, video: UploadFile = File(...)):
    """AI Video Scanning Logic (Officer Portal)"""
    video_path = f"temp_{video.filename}"
    with open(video_path, "wb") as f:
        f.write(await video.read())

    try:
        print("🔄 Scanning video...")
        base64_full, base64_crop = extract_best_frame(video_path)
        if not base64_full: raise Exception("Could not process video frames.")

        violation_prompt = """
        Analyze this traffic scene carefully.
        1. Detect exactly ONE violation from this exact list and assign the correct fine:
           - Helmet Violation (Fine: 1000)
           - Phone Violation (Fine: 1000)
           - Triple Riding (Fine: 2000)
           - Seatbelt Violation (Fine: 3000)
           - No Violation (Fine: 0)

        2. Identify the Vehicle Type (e.g., Motorcycle, Car, SUV).
        3. Read the License Plate text if visible (Fallback).
        
        Return ONLY JSON matching this exact structure. DO NOT INCLUDE IMAGE DATA: 
        {
          "violation_type": "string (MUST be from the list above)", 
          "fine": number, 
          "vehicle_type": "string (e.g., Motorcycle, Car)", 
          "number_plate_fallback": "string" 
        }
        """
        
        completion_v = client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{"role": "user", "content": [
                {"type": "text", "text": violation_prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_full}"}}
            ]}],
            response_format={"type": "json_object"}
        )
        violation_data = json.loads(completion_v.choices[0].message.content)

        plate_text = "UNKNOWN"
        if base64_crop:
            print("✅ Precision Scan: Reading text from YOLO Crop.")
            ocr_prompt = """
            Read the alphanumeric LICENSE PLATE text from this cropped image.
            Return JSON: {"number_plate": "string"}
            Output ONLY the characters (e.g., "KHI1234"). DO NOT INCLUDE IMAGE DATA.
            """
            completion_ocr = client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": ocr_prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_crop}"}}
                ]}],
                response_format={"type": "json_object"}
            )
            ocr_data = json.loads(completion_ocr.choices[0].message.content)
            plate_text = ocr_data.get("number_plate", "UNKNOWN")

        final_plate = clean_plate_text(plate_text)
        if not final_plate:
            fallback = clean_plate_text(violation_data.get("number_plate_fallback"))
            if fallback:
                final_plate = fallback
            else:
                final_plate = "UNKNOWN"

        final_data = {
            "type": violation_data.get("violation_type", "Unknown Violation"),
            "fine": violation_data.get("fine", 0),
            "vehicle": violation_data.get("vehicle_type", "Unknown Vehicle"),
            "number_plate": final_plate,
            "violation_image": base64_full,
            "owner_name": "Unregistered Vehicle",
            "owner_nic": "N/A",
            "challan_number": None
        }

        owner = None
        if final_plate != "UNKNOWN" and owner_col is not None:
            owner = owner_col.find_one({"vehicles": {"$regex": f"^{final_plate}$", "$options": "i"}})

        if owner:
            final_data["owner_name"] = owner.get("name", "Unknown")
            final_data["owner_nic"] = owner.get("nic", "Unknown")

            challan_no = f"CH-{int(time.time())}"
            if challan_col is not None:
                challan_col.insert_one({
                    "challan_number": challan_no,
                    "vehicle_id": final_plate,
                    "owner_id": owner["_id"],
                    "violation_type": final_data["type"],
                    "fine": final_data["fine"],
                    "status": "UNPAID",
                    "issued_at": datetime.now(UTC)
                })
            final_data["challan_number"] = challan_no

            msg = (
                f"🚨 *TRAFFIC VIOLATION* 🚨\n\n"
                f"Dear *{owner['name'].upper()}*,\n"
                f"Vehicle: *{final_data['vehicle']}* ({final_plate})\n"
                f"Violation: *{final_data['type']}*\n"
                f"Fine: PKR {final_data['fine']}\n"
                f"Please pay via App."
            )
            if twilio_client and TWILIO_NUM:
                try:
                    twilio_client.messages.create(
                        from_=f"whatsapp:{TWILIO_NUM}",
                        body=msg,
                        to=f"whatsapp:+{owner['phone']}" # Added the '+' for Twilio formatting
                    )
                    final_data['notification_status'] = "WhatsApp Sent ✅"
                except Exception as e:
                    final_data['notification_status'] = f"WhatsApp Fail: {str(e)}"
            else:
                final_data['notification_status'] = "WhatsApp disabled (no Twilio credentials)"
        else:
            final_data['notification_status'] = f"Owner not found for {final_plate}"

    except Exception as e:
        return templates.TemplateResponse(request=request, name="officer.html", context={"error": str(e)})
    finally:
        if os.path.exists(video_path): os.remove(video_path)

    return templates.TemplateResponse(request=request, name="officer.html", context={"result": final_data})


# ==========================================
# 6. FEATURE ENDPOINTS
# ==========================================

@app.patch("/api/challan/{challan_number}/pay")
async def mark_challan_paid(challan_number: str, by: str = "system"):
    if challan_col is None:
        return JSONResponse(status_code=503, content={"message": "Database unavailable."})
    result = challan_col.update_one(
        {"challan_number": challan_number},
        {"$set": {"status": "PAID", "paid_at": datetime.now(UTC)}}
    )
    if result.matched_count == 0:
        return JSONResponse(status_code=404, content={"message": "Challan not found."})
    if audit_col is not None:
        audit_col.insert_one({
            "challan_number": challan_number,
            "action": "MARKED_PAID",
            "by": by,
            "timestamp": datetime.now(UTC)
        })
    return {"message": f"Challan {challan_number} marked as PAID."}


@app.get("/api/search-plate/{plate}")
async def search_plate(plate: str):
    if challan_col is None or owner_col is None:
        return JSONResponse(status_code=503, content={"message": "Database unavailable."})
    clean = re.sub(r'[^A-Z0-9]', '', plate.upper().strip())
    owner = owner_col.find_one({"vehicles": {"$regex": f"^{clean}$", "$options": "i"}})
    challans_raw = list(challan_col.find({"vehicle_id": {"$regex": f"^{clean}$", "$options": "i"}}))
    challans = []
    for c in challans_raw:
        issued = c.get("issued_at", datetime.now(UTC))
        challans.append({
            "challan_number": c.get("challan_number", "N/A"),
            "vehicle_id": c.get("vehicle_id", ""),
            "violation_type": c.get("violation_type", ""),
            "fine": c.get("fine", 0),
            "status": c.get("status", "UNPAID"),
            "issued_at": issued.strftime("%Y-%m-%d") if hasattr(issued, "strftime") else str(issued)
        })
    return {
        "owner_name": owner.get("name") if owner else "Not Registered",
        "owner_nic": owner.get("nic") if owner else "N/A",
        "challans": challans
    }


@app.post("/api/remove-vehicle")
async def remove_vehicle(data: RemoveVehicleData):
    if owner_col is None:
        return JSONResponse(status_code=503, content={"message": "Database unavailable."})
    result = owner_col.update_one({"nic": data.nic}, {"$pull": {"vehicles": data.vehicle_id}})
    if result.matched_count == 0:
        return JSONResponse(status_code=404, content={"message": "User not found."})
    return {"message": f"Vehicle {data.vehicle_id} removed."}


@app.get("/stats", response_class=HTMLResponse)
async def stats_page(request: Request):
    return templates.TemplateResponse(request=request, name="stats.html")


@app.get("/api/stats")
async def get_stats():
    if challan_col is None or owner_col is None:
        return JSONResponse(status_code=503, content={"message": "Database unavailable."})
    total = challan_col.count_documents({})
    paid = challan_col.count_documents({"status": "PAID"})
    unpaid = challan_col.count_documents({"status": "UNPAID"})
    collected_agg = list(challan_col.aggregate([{"$match": {"status": "PAID"}}, {"$group": {"_id": None, "t": {"$sum": "$fine"}}}]))
    pending_agg = list(challan_col.aggregate([{"$match": {"status": "UNPAID"}}, {"$group": {"_id": None, "t": {"$sum": "$fine"}}}]))
    violations_agg = list(challan_col.aggregate([{"$group": {"_id": "$violation_type", "count": {"$sum": 1}}}]))
    monthly_agg = list(challan_col.aggregate([
        {"$group": {"_id": {"year": {"$year": "$issued_at"}, "month": {"$month": "$issued_at"}}, "count": {"$sum": 1}, "fines": {"$sum": "$fine"}}},
        {"$sort": {"_id.year": 1, "_id.month": 1}},
        {"$limit": 6}
    ]))
    months_map = {1:"Jan",2:"Feb",3:"Mar",4:"Apr",5:"May",6:"Jun",7:"Jul",8:"Aug",9:"Sep",10:"Oct",11:"Nov",12:"Dec"}
    return {
        "total_challans": total,
        "paid_challans": paid,
        "unpaid_challans": unpaid,
        "total_collected": collected_agg[0]["t"] if collected_agg else 0,
        "total_pending": pending_agg[0]["t"] if pending_agg else 0,
        "violations_breakdown": {v["_id"]: v["count"] for v in violations_agg if v["_id"]},
        "monthly_labels": [f"{months_map.get(m['_id']['month'], '?')} {m['_id']['year']}" for m in monthly_agg],
        "monthly_counts": [m["count"] for m in monthly_agg],
        "total_citizens": owner_col.count_documents({})
    }


@app.post("/api/forgot-password")
async def forgot_password(data: ForgotPasswordData):
    if owner_col is None:
        return JSONResponse(status_code=503, content={"message": "Database unavailable."})
    user = owner_col.find_one({"nic": data.nic})
    if not user:
        return JSONResponse(status_code=404, content={"message": "No account found with this CNIC."})
    otp = str(random.randint(100000, 999999))
    otp_store[data.nic] = {"otp": otp, "expires": time.time() + 300}
    msg = f"🔐 Your TrafficGuard OTP is: *{otp}*\nValid for 5 minutes. Do not share with anyone."
    phone = user.get("phone", "")
    if twilio_client and TWILIO_NUM and phone:
        try:
            twilio_client.messages.create(from_=f"whatsapp:{TWILIO_NUM}", body=msg, to=f"whatsapp:+{phone}")
            masked = phone[-4:].rjust(len(phone), '*')
            return {"message": f"OTP sent to WhatsApp ending in {phone[-4:]}."}
        except Exception as e:
            return JSONResponse(status_code=500, content={"message": f"Failed to send OTP: {str(e)}"})
    return {"message": f"[DEV] OTP: {otp} (Twilio not configured)"}


@app.post("/api/reset-password")
async def reset_password(data: ResetPasswordData):
    if owner_col is None:
        return JSONResponse(status_code=503, content={"message": "Database unavailable."})
    record = otp_store.get(data.nic)
    if not record:
        return JSONResponse(status_code=400, content={"message": "No OTP found. Request a new one."})
    if time.time() > record["expires"]:
        del otp_store[data.nic]
        return JSONResponse(status_code=400, content={"message": "OTP expired. Request a new one."})
    if record["otp"] != data.otp:
        return JSONResponse(status_code=400, content={"message": "Invalid OTP. Please try again."})
    owner_col.update_one({"nic": data.nic}, {"$set": {"password": data.new_password}})
    del otp_store[data.nic]
    return {"message": "Password reset successfully! You can now log in."}


# ==========================================
# 7. BATCH-1 ENDPOINTS (Officer auth, leaderboard, export, chatbot, audit)
# ==========================================

@app.post("/api/officer-login")
async def officer_login(data: OfficerLoginData):
    if officer_col is None:
        return JSONResponse(status_code=503, content={"message": "Database unavailable."})
    officer = officer_col.find_one({"username": data.username, "password": data.password})
    if not officer:
        return JSONResponse(status_code=401, content={"message": "Invalid officer credentials."})
    return {
        "message": "Officer login successful.",
        "name": officer.get("name", ""),
        "badge_id": officer.get("badge_id", ""),
        "username": officer.get("username", "")
    }


@app.get("/api/leaderboard")
async def leaderboard():
    if challan_col is None or owner_col is None:
        return JSONResponse(status_code=503, content={"message": "Database unavailable."})
    pipeline = [
        {"$group": {
            "_id": "$owner_id",
            "violation_count": {"$sum": 1},
            "total_fine": {"$sum": "$fine"},
            "unpaid_count": {"$sum": {"$cond": [{"$eq": ["$status", "UNPAID"]}, 1, 0]}}
        }},
        {"$sort": {"violation_count": -1}},
        {"$limit": 10}
    ]
    top = list(challan_col.aggregate(pipeline))
    out = []
    for entry in top:
        owner = owner_col.find_one({"_id": entry["_id"]}) if entry["_id"] else None
        out.append({
            "name": owner.get("name", "Unknown") if owner else "Unknown",
            "nic_masked": (owner["nic"][:5] + "*****" + owner["nic"][-2:]) if owner and owner.get("nic") else "N/A",
            "violation_count": entry["violation_count"],
            "total_fine": entry["total_fine"],
            "unpaid_count": entry["unpaid_count"]
        })
    return {"leaderboard": out}


@app.get("/api/export-stats-csv")
async def export_stats_csv():
    if challan_col is None:
        return JSONResponse(status_code=503, content={"message": "Database unavailable."})
    rows = list(challan_col.find({}, {"_id": 0, "owner_id": 0}))
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Challan Number", "Vehicle ID", "Violation Type", "Fine (PKR)", "Status", "Issued At", "Paid At"])
    for r in rows:
        issued = r.get("issued_at", "")
        paid = r.get("paid_at", "")
        writer.writerow([
            r.get("challan_number", ""),
            r.get("vehicle_id", ""),
            r.get("violation_type", ""),
            r.get("fine", 0),
            r.get("status", ""),
            issued.strftime("%Y-%m-%d %H:%M") if hasattr(issued, "strftime") else str(issued),
            paid.strftime("%Y-%m-%d %H:%M") if hasattr(paid, "strftime") else str(paid)
        ])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=trafficguard_stats_{datetime.now().strftime('%Y%m%d')}.csv"}
    )


@app.post("/api/chatbot")
async def chatbot(data: ChatMessage):
    if not GROQ_API_KEY:
        return JSONResponse(status_code=503, content={"message": "Chatbot unavailable (no Groq key)."})
    system_prompt = (
        "You are TrafficGuard Assistant — a friendly, concise chatbot helping Pakistani citizens "
        "with traffic challans. You answer questions about: how to view challans, how to pay online "
        "(citizens use the Pay button on their dashboard, mock card flow), what violations cost "
        "(Helmet 1000, Phone 1000, Triple Riding 2000, Seatbelt 3000), how to register vehicles "
        "(automatically linked by CNIC), how to reset password (Forgot Password sends WhatsApp OTP), "
        "and the officer portal at /officer. Keep answers under 3 sentences. Use simple English."
    )
    messages = [{"role": "system", "content": system_prompt}]
    for msg in data.history[-6:]:
        if msg.get("role") in ("user", "assistant"):
            messages.append({"role": msg["role"], "content": msg.get("content", "")})
    messages.append({"role": "user", "content": data.message})
    try:
        completion = client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=messages,
            max_tokens=200,
            temperature=0.5
        )
        reply = completion.choices[0].message.content
        return {"reply": reply}
    except Exception as e:
        return JSONResponse(status_code=500, content={"message": f"Chatbot error: {str(e)}"})


@app.get("/api/audit-log")
async def get_audit_log(limit: int = 50):
    if audit_col is None:
        return JSONResponse(status_code=503, content={"message": "Database unavailable."})
    entries = list(audit_col.find({}, {"_id": 0}).sort("timestamp", -1).limit(limit))
    for e in entries:
        ts = e.get("timestamp")
        e["timestamp"] = ts.strftime("%Y-%m-%d %H:%M:%S") if hasattr(ts, "strftime") else str(ts)
    return {"entries": entries}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8001))
    host = "0.0.0.0" if os.getenv("PORT") else "127.0.0.1"
    uvicorn.run(app, host=host, port=port)