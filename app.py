import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- 1. CONFIGURATION: ตั้งค่าสเปครถและอุปกรณ์ตรงนี้ ---

# รายชื่อรถ พร้อมสเปค (Max Seats = จำนวนคนนั่งสูงสุด, Cargo Score = คะแนนความจุของ)
CAR_SPECS = {
    "Honda Jazz 2019": {
        "max_seats": 5, 
        "cargo_score": 400, # Jazz ที่เก็บของท้ายรถประมาณหนึ่ง
        "desc": "รถเก๋ง 5 ประตู คล่องตัว"
    },
    "Isuzu Mu-X": {
        "max_seats": 7, 
        "cargo_score": 1000, # พับเบาะได้ พื้นที่เยอะ
        "desc": "SUV 7 ที่นั่ง หรือพับเบาะขนของสำคัญ"
    },
    "Isuzu D-max 4 Doors": {
        "max_seats": 5, 
        "cargo_score": 2500, # กระบะหลัง ขนของชิ้นใหญ่ได้สบาย
        "desc": "กระบะ 4 ประตู เน้นขนเครื่องมือหนัก/เปื้อนได้"
    }
}

# รายชื่ออุปกรณ์ (Volume = คะแนนความกินพื้นที่ต่อชิ้น)
EQUIPMENT_DB = {
    "GNSS": {"volume": 50},
    "Tripod": {"volume": 100},
    "Pole": {"volume": 20},
    "Bag": {"volume": 50},
    "M350 set": {"volume": 300},
    "Apache3": {"volume": 300},
    "Apache4": {"volume": 350},
    "LiDAR": {"volume": 50},
    "RS10": {"volume": 80},
    "D270": {"volume": 200},
}

# --- 2. Google Sheets Connection ---
def get_google_sheet():
    # *ใช้ Code เดิมจาก Version ก่อนหน้า*
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    return client.open("CarBookingDB").sheet1 

# --- 3. LOGIC: ระบบคำนวณความเหมาะสม ---
def recommend_cars(n_people, selected_equipment):
    # 1. คำนวณ Load รวมของอุปกรณ์
    total_equipment_load = 0
    equip_summary = []
    
    for item, qty in selected_equipment.items():
        if qty > 0:
            vol = EQUIPMENT_DB[item]["volume"] * qty
            total_equipment_load += vol
            equip_summary.append(f"{item} x{qty}")
    
    equip_str = ", ".join(equip_summary) if equip_summary else "-"
    
    # 2. หาว่ารถคันไหนไปได้บ้าง
    recommendations = []
    
    for car_name, specs in CAR_SPECS.items():
        # กฏที่ 1: ที่นั่งต้องพอ
        if specs["max_seats"] >= n_people:
            # กฏที่ 2: ที่เหลือจากการนั่ง ต้องพอกับของ
            # สมมติคน 1 คนกินที่ Load Factor เล็กน้อยในห้องโดยสาร แต่หลักๆ ดู Cargo Score
            # ถ้าเป็นกระบะ (D-max) คนนั่งเต็ม ก็ยังขนของข้างหลังได้เต็มที่
            # ถ้าเป็น Jazz คนนั่งเต็ม ที่เก็บของจะเหลือน้อย
            
            is_fit = False
            note = ""
            
            if "D-max" in car_name:
                # กระบะ แยกส่วนคนกับของ ชิลๆ
                if total_equipment_load <= specs["cargo_score"]:
                    is_fit = True
                    note = "✅ เหมาะสม (ใส่กระบะหลัง)"
            else:
                # รถเก๋ง/SUV พื้นที่แปรผกผันกับคน
                # สมมติคน 1 คน กินพื้นที่ Cargo ไปนิดหน่อย (กระเป๋าเป้)
                available_cargo = specs["cargo_score"] - (n_people * 20) 
                if total_equipment_load <= available_cargo:
                    is_fit = True
                    note = "✅ เหมาะสม"
                else:
                    note = "❌ ที่นั่งพอ แต่ที่เก็บของไม่พอ"
            
            if is_fit:
                recommendations.append((car_name, note))
        else:
            # ไม่แนะนำเพราะที่นั่งไม่พอ
            pass
            
    return recommendations, equip_str

# --- UI Application ---
st.set_page_config(page_title="ระบบจองรถบริษัท (Smart)", layout="wide")
st.title("🚛 ระบบจองรถบริษัท + คำนวณการขนของ")

# เชื่อมต่อ Database
try:
    sheet = get_google_sheet()
    data = sheet.get_all_records()
    df = pd.DataFrame(data)
    if df.empty:
        df = pd.DataFrame(columns=["User", "Task", "Car", "People", "Equipment", "Location", "Start_Time", "End_Time"])
    else:
        df['Start_Time'] = pd.to_datetime(df['Start_Time'])
        df['End_Time'] = pd.to_datetime(df['End_Time'])
except Exception as e:
    st.error(f"Connect Error: {e}")
    st.stop()

def save_to_gsheet(dataframe):
    export_df = dataframe.copy()
    export_df['Start_Time'] = export_df['Start_Time'].dt.strftime('%Y-%m-%d %H:%M:%S')
    export_df['End_Time'] = export_df['End_Time'].dt.strftime('%Y-%m-%d %H:%M:%S')
    sheet.clear()
    sheet.update([export_df.columns.values.tolist()] + export_df.values.tolist())

def is_car_available(df, car, start, end):
    if df.empty: return True, "ว่าง"
    car_bookings = df[df['Car'] == car]
    for _, row in car_bookings.iterrows():
        if start < row['End_Time'] and end > row['Start_Time']:
            return False, f"ติดจองโดย {row['User']}"
    return True, "ว่าง"

# --- TABS ---
tab1, tab2, tab3 = st.tabs(["📦 คำนวณและจองรถ", "📋 ตารางการใช้รถ", "❌ ยกเลิก"])

with tab1:
    col1, col2 = st.columns([1, 1])
    
    with col1:
        st.header("1. ระบุคนและสิ่งของ")
        user_name = st.text_input("ชื่อผู้จอง")
        task_name = st.text_input("ภารกิจ")
        location = st.text_input("สถานที่ไป")
        
        n_people = st.number_input("จำนวนคนเดินทาง (รวมคนขับ)", min_value=1, max_value=10, value=2)
        
        st.subheader("เลือกอุปกรณ์ที่ขนไป")
        selected_equip = {}
        # วนลูปสร้างตัวเลือกจำนวน
        for item in EQUIPMENT_DB.keys():
            c1, c2 = st.columns([3, 1])
            c1.write(f"• {item}")
            qty = c2.number_input(f"จำนวน", key=item, min_value=0, max_value=10, value=0, label_visibility="collapsed")
            if qty > 0:
                selected_equip[item] = qty

    with col2:
        st.header("2. ระบบแนะนำรถ")
        
        # คำนวณรถที่เหมาะสม
        valid_cars_list, equip_str = recommend_cars(n_people, selected_equip)
        
        if not valid_cars_list:
            st.error("⚠️ ไม่มีรถที่เหมาะสม! (คนเยอะเกิน หรือ ของเยอะเกินความจุรถ)")
            available_choices = [] # ไม่มีให้เลือก
        else:
            st.success(f"พบรถที่เหมาะสม {len(valid_cars_list)} คัน")
            # แปลง List เป็นตัวเลือกใน Selectbox
            car_choices = [c[0] for c in valid_cars_list]
            available_choices = car_choices
            
            # แสดงรายละเอียดการแนะนำ
            for car_name, note in valid_cars_list:
                st.info(f"**{car_name}**: {note}")

        st.divider()
        st.header("3. ยืนยันวันเวลา")
        
        # ให้เลือกเฉพาะรถที่ระบบแนะนำ (แต่ถ้าไม่มี ก็ยอมให้เลือกทั้งหมดเผื่อเขาจะฝืน)
        final_car_list = available_choices if available_choices else list(CAR_SPECS.keys())
        selected_car = st.selectbox("เลือกรถที่ต้องการจอง", final_car_list)
        
        today = datetime.now()
        c_date, c_time = st.columns(2)
        start_date = c_date.date_input("วันที่เริ่ม", today)
        start_time = c_time.time_input("เวลาเริ่ม", today.time())
        end_date = c_date.date_input("วันที่คืน", today)
        end_time = c_time.time_input("เวลาคืน", (today + timedelta(hours=4)).time())

        if st.button("🚀 ยืนยันการจอง", use_container_width=True):
            start_dt = datetime.combine(start_date, start_time)
            end_dt = datetime.combine(end_date, end_time)
            
            if start_dt >= end_dt:
                st.warning("เวลาคืนต้องหลังเวลาเริ่ม")
            elif not user_name:
                st.warning("กรุณาระบุชื่อผู้จอง")
            else:
                available, msg = is_car_available(df, selected_car, start_dt, end_dt)
                if available:
                    new_row = {
                        "User": user_name, "Task": task_name, "Car": selected_car,
                        "People": n_people, "Equipment": equip_str, # บันทึกรายการของ
                        "Location": location, "Start_Time": start_dt, "End_Time": end_dt
                    }
                    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
                    save_to_gsheet(df)
                    st.success(f"จอง {selected_car} สำเร็จ!")
                    st.balloons()
                    st.experimental_rerun()
                else:
                    st.error(f"จองไม่ได้: {msg}")

with tab2:
    st.subheader("สถานะการใช้งานรถ")
    now = datetime.now()
    
    # Grid Layout แสดงรถ
    cols = st.columns(3)
    cars_all = list(CAR_SPECS.keys())
    
    for i, car in enumerate(cars_all):
        usage = df[df['Car'] == car] if not df.empty else pd.DataFrame()
        with cols[i]:
            # Card Styling
            st.write(f"### 🚗 {car}")
            st.caption(CAR_SPECS[car]['desc'])
            
            is_busy = False
            if not usage.empty:
                current = usage[(usage['Start_Time'] <= now) & (usage['End_Time'] >= now)]
                if not current.empty:
                    is_busy = True
                    row = current.iloc[0]
                    st.error(f"⛔ กำลังใช้งาน")
                    st.write(f"**โดย:** {row['User']}")
                    st.write(f"**ไป:** {row['Location']}")
                    st.write(f"**ขน:** {row['Equipment']}")
            
            if not is_busy:
                st.success("✅ ว่าง")

    st.divider()
    st.write("### ประวัติการจองทั้งหมด")
    if not df.empty:
        show_df = df.sort_values("Start_Time", ascending=False).copy()
        show_df['Start_Time'] = show_df['Start_Time'].dt.strftime('%d/%m %H:%M')
        show_df['End_Time'] = show_df['End_Time'].dt.strftime('%d/%m %H:%M')
        # เลือกแสดงเฉพาะคอลัมน์ที่จำเป็น
        st.dataframe(show_df[['User', 'Car', 'People', 'Equipment', 'Location', 'Start_Time', 'End_Time']], use_container_width=True)

with tab3:
    st.write("### ยกเลิกรายการจอง")
    if not df.empty:
        df['Display'] = df.apply(lambda x: f"{x['User']} - {x['Car']} ({x['Start_Time'].strftime('%d/%m')})", axis=1)
        del_item = st.selectbox("เลือกรายการที่จะยกเลิก", df['Display'].unique())
        if st.button("ยืนยันการลบ"):
            df = df[df['Display'] != del_item].drop(columns=['Display'])
            save_to_gsheet(df)
            st.success("ลบรายการเรียบร้อย")
            st.experimental_rerun()
