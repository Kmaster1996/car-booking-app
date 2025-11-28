import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- 1. CONFIGURATION ---
CAR_SPECS = {
    "Honda Jazz 2019": {"max_seats": 5, "cargo_score": 800, "desc": "รถเก๋ง 5 ประตู คล่องตัว"},
    "Isuzu Mu-X": {"max_seats": 7, "cargo_score": 1800, "desc": "SUV 7 ที่นั่ง/พับเบาะขนของ"},
    "Isuzu D-max 4 Doors": {"max_seats": 5, "cargo_score": 2500, "desc": "กระบะ 4 ประตู ขนของหนัก"}
}

EQUIPMENT_DB = {
    "GNSS": {"volume": 150},
    "Tripod": {"volume": 120},
    "Pole": {"volume": 50},
    "Bag": {"volume": 80},
    "M350 set": {"volume": 450},
    "Apache3": {"volume": 400},
    "Apache4": {"volume": 500},
    "LiDAR": {"volume": 100},
    "RS10": {"volume": 150},
    "D270": {"volume": 200}
}

# --- 2. Google Sheets ---
def get_google_sheet():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    return client.open("CarBookingDB").sheet1 

# --- 3. LOGIC ---
def recommend_cars(n_people, selected_equipment):
    total_equipment_load = 0
    equip_summary = []
    for item, qty in selected_equipment.items():
        if qty > 0:
            vol = EQUIPMENT_DB[item]["volume"] * qty
            total_equipment_load += vol
            equip_summary.append(f"{item} x{qty}")
    equip_str = ", ".join(equip_summary) if equip_summary else "-"
    
    recommendations = []
    for car_name, specs in CAR_SPECS.items():
        if specs["max_seats"] >= n_people:
            is_fit = False
            note = ""
            if "D-max" in car_name:
                if total_equipment_load <= specs["cargo_score"]:
                    is_fit = True
                    note = "✅ เหมาะสม (ใส่กระบะหลัง)"
            else:
                available_cargo = specs["cargo_score"] - (n_people * 20) 
                if total_equipment_load <= available_cargo:
                    is_fit = True
                    note = "✅ เหมาะสม"
                else:
                    note = "❌ ที่นั่งพอ แต่ที่เก็บของไม่พอ"
            
            if is_fit:
                recommendations.append((car_name, note))
            
    return recommendations, equip_str

# ฟังก์ชันเช็คว่าว่างไหม (Excl_index เอาไว้ข้ามรายการตัวเองตอนแก้ไข)
def is_car_available(df, car, start, end, excl_index=None):
    if df.empty: return True, "ว่าง"
    
    # กรองรายการของรถคันนั้น
    car_bookings = df[df['Car'] == car]
    
    for index, row in car_bookings.iterrows():
        # ถ้าเป็นรายการเดียวกับที่เรากำลังแก้ ให้ข้ามไปเลย (ไม่นับว่าทับซ้อน)
        if excl_index is not None and index == excl_index:
            continue
            
        if start < row['End_Time'] and end > row['Start_Time']:
            return False, f"ชนกับ {row['User']} ({row['Start_Time'].strftime('%H:%M')}-{row['End_Time'].strftime('%H:%M')})"
    return True, "ว่าง"

# --- UI Setup ---
st.set_page_config(page_title="ระบบจองรถบริษัท (Smart V3)", layout="wide")
st.title("🚛 Navtech4Book")

# Initialize Default Time (แก้ปัญหาเวลาเด้ง)
if 'default_time_start' not in st.session_state:
    now = datetime.now()
    # ปัดเศษเวลาให้สวยงาม (เช่น 14:00) และบวกไปข้างหน้าเล็กน้อย
    next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0)
    st.session_state.default_time_start = next_hour.time()
    st.session_state.default_time_end = (next_hour + timedelta(hours=4)).time()

# Load Data
try:
    sheet = get_google_sheet()
    data = sheet.get_all_records()
    df = pd.DataFrame(data)
    
    if df.empty:
        df = pd.DataFrame(columns=["User", "Task", "Car", "People", "Equipment", "Location", "Start_Time", "End_Time"])
    else:
        # เทคนิคแก้บั๊ก: แปลงคอลัมน์วันที่เป็น String ก่อน -> แล้วค่อยแปลงเป็น Datetime
        # (ช่วยแก้ปัญหาเวลา Google Sheets ส่งค่ามาแปลกๆ)
        df['Start_Time'] = pd.to_datetime(df['Start_Time'].astype(str), errors='coerce')
        df['End_Time'] = pd.to_datetime(df['End_Time'].astype(str), errors='coerce')
        
        # ลบแถวที่วันที่ Error หรือเป็นค่าว่างทิ้งไป (กันระบบรวน)
        df = df.dropna(subset=['Start_Time', 'End_Time'])
        
        # ตัดช่องว่างข้างหน้า-ข้างหลังชื่อรถทิ้ง (Trim) เพื่อให้เปรียบเทียบชื่อรถได้แม่นยำ 100%
        df['Car'] = df['Car'].astype(str).str.strip()

except Exception as e:
    st.error(f"เกิดข้อผิดพลาดในการดึงข้อมูล: {e}")
    st.stop()

def is_car_available(df, car, start, end, excl_index=None):
    if df.empty: return True, "ว่าง"
    
    # Clean ชื่อรถที่รับเข้ามา ให้แน่ใจว่าไม่มีช่องว่างเกิน
    target_car = car.strip()
    
    # กรองเฉพาะรายการของรถคันนั้น
    car_bookings = df[df['Car'] == target_car]
    
    for index, row in car_bookings.iterrows():
        # ข้ามรายการตัวเอง (กรณีแก้ไข)
        if excl_index is not None and index == excl_index:
            continue
            
        # LOGIC การเช็คชนกัน (Overlap Logic)
        # ถ้ารายการเดิม เริ่ม 13:00 จบ 15:00
        # เราจองใหม่ 14:00 - 16:00
        # 14:00 < 15:00 (จริง) AND 16:00 > 13:00 (จริง) => ชนกัน!
        if start < row['End_Time'] and end > row['Start_Time']:
            
            # Format เวลาให้ดูง่าย
            existing_start = row['Start_Time'].strftime('%d/%m %H:%M')
            existing_end = row['End_Time'].strftime('%H:%M')
            
            return False, f"❌ ไม่ว่าง! ติดจองโดย {row['User']} ({existing_start} - {existing_end})"
            
    return True, "✅ ว่าง"

def save_to_gsheet(dataframe):
    export_df = dataframe.copy()
    export_df['Start_Time'] = export_df['Start_Time'].dt.strftime('%Y-%m-%d %H:%M:%S')
    export_df['End_Time'] = export_df['End_Time'].dt.strftime('%Y-%m-%d %H:%M:%S')
    sheet.clear()
    sheet.update([export_df.columns.values.tolist()] + export_df.values.tolist())

# --- TABS ---
tab1, tab2, tab3 = st.tabs(["📦 จองรถใหม่", "📋 ตารางรถ", "✏️ แก้ไข/ยกเลิก"])

# --- TAB 1: จองใหม่ ---
with tab1:
    col1, col2 = st.columns([1, 1])
    with col1:
        st.header("1. ข้อมูลการเดินทาง")
        user_name = st.text_input("ชื่อผู้จอง")
        task_name = st.text_input("ภารกิจ")
        location = st.text_input("สถานที่")
        n_people = st.number_input("จำนวนคน", 1, 10, 2)
        
        st.caption("เลือกอุปกรณ์")
        selected_equip = {}
        for item in EQUIPMENT_DB.keys():
            c_eq1, c_eq2 = st.columns([3, 1])
            c_eq1.write(f"- {item}")
            qty = c_eq2.number_input("จำนวน", key=f"add_{item}", min_value=0, max_value=10, value=0, label_visibility="collapsed")
            if qty > 0: selected_equip[item] = qty

    with col2:
        st.header("2. เลือกเวลาและรถ")
        valid_cars_list, equip_str = recommend_cars(n_people, selected_equip)
        
        # แสดงผลแนะนำ
        if valid_cars_list:
            st.success(f"แนะนำ: {', '.join([c[0] for c in valid_cars_list])}")
            car_choices = [c[0] for c in valid_cars_list]
        else:
            st.warning("ไม่มีรถที่เหมาะสมตามเกณฑ์ (แต่คุณยังเลือกเองได้)")
            car_choices = list(CAR_SPECS.keys())
        
        selected_car = st.selectbox("เลือกรถ", car_choices if car_choices else list(CAR_SPECS.keys()))
        
        # วันเวลา (ใช้ session_state เพื่อไม่ให้เด้ง)
        today = datetime.now()
        c_date, c_time = st.columns(2)
        start_date = c_date.date_input("วันที่เริ่ม", today)
        start_time = c_time.time_input("เวลาเริ่ม", st.session_state.default_time_start)
        end_date = c_date.date_input("วันที่คืน", today)
        end_time = c_time.time_input("เวลาคืน", st.session_state.default_time_end)

        if st.button("🚀 ยืนยันการจอง", use_container_width=True):
            start_dt = datetime.combine(start_date, start_time)
            end_dt = datetime.combine(end_date, end_time)
            
            if start_dt >= end_dt:
                st.warning("เวลาคืนต้องหลังเวลาเริ่ม")
            elif not user_name:
                st.warning("กรุณาระบุชื่อ")
            else:
                available, msg = is_car_available(df, selected_car, start_dt, end_dt)
                if available:
                    new_row = {
                        "User": user_name, "Task": task_name, "Car": selected_car,
                        "People": n_people, "Equipment": equip_str,
                        "Location": location, "Start_Time": start_dt, "End_Time": end_dt
                    }
                    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
                    save_to_gsheet(df)
                    st.success("จองสำเร็จ!")
                    st.rerun()
                else:
                    st.error(f"จองไม่ได้: {msg}")

# --- TAB 2: ตาราง (เหมือนเดิม) ---
with tab2:
    st.write("### สถานะรถตอนนี้")
    now = datetime.now()
    cols = st.columns(3)
    for i, car in enumerate(CAR_SPECS.keys()):
        usage = df[df['Car'] == car] if not df.empty else pd.DataFrame()
        with cols[i]:
            is_busy = False
            if not usage.empty:
                current = usage[(usage['Start_Time'] <= now) & (usage['End_Time'] >= now)]
                if not current.empty:
                    is_busy = True
                    row = current.iloc[0]
                    st.error(f"⛔ {car}")
                    st.caption(f"โดย: {row['User']} (ถึง {row['End_Time'].strftime('%H:%M')})")
            if not is_busy:
                st.success(f"✅ {car}")

    st.divider()
    if not df.empty:
        show_df = df.sort_values("Start_Time", ascending=False).copy()
        # Format for display
        show_df['Start_Time'] = show_df['Start_Time'].dt.strftime('%d/%m %H:%M')
        show_df['End_Time'] = show_df['End_Time'].dt.strftime('%d/%m %H:%M')
        st.dataframe(show_df[['User','Car','People','Equipment','Start_Time','End_Time']], use_container_width=True)

# --- TAB 3: แก้ไข/ยกเลิก (เพิ่มใหม่) ---
with tab3:
    st.header("จัดการรายการจอง")
    if not df.empty:
        # สร้าง Dropdown เลือกรายการ
        df['Display'] = df.apply(lambda x: f"{x['User']} - {x['Car']} ({x['Start_Time'].strftime('%d/%m %H:%M')})", axis=1)
        # ใช้ session state เพื่อจำว่าเลือกอะไรอยู่
        selected_item_str = st.selectbox("เลือกรายการที่จะแก้ไข/ลบ", df['Display'].unique())
        
        # ดึงข้อมูล Row ที่เลือกออกมา
        selected_row_idx = df[df['Display'] == selected_item_str].index[0]
        row_data = df.loc[selected_row_idx]
        
        st.info(f"กำลังจัดการ: {selected_item_str}")
        
        mode = st.radio("เลือกสิ่งที่ต้องการทำ", ["❌ ลบรายการนี้", "✏️ แก้ไขเวลา"], horizontal=True)
        
        if mode == "❌ ลบรายการนี้":
            if st.button("ยืนยันการลบ", type="primary"):
                df = df.drop(selected_row_idx)
                # ลบคอลัมน์ Display ก่อนเซฟ
                if 'Display' in df.columns: df = df.drop(columns=['Display'])
                save_to_gsheet(df)
                st.success("ลบเรียบร้อย")
                st.rerun()
                
        elif mode == "✏️ แก้ไขเวลา":
            st.write("--- แก้ไขวัน/เวลา ---")
            # ดึงค่าเดิมมาเป็นค่าตั้งต้น
            curr_start = row_data['Start_Time']
            curr_end = row_data['End_Time']
            
            # Form แก้ไข
            c_edit1, c_edit2 = st.columns(2)
            new_s_date = c_edit1.date_input("วันที่เริ่ม (ใหม่)", curr_start.date())
            new_s_time = c_edit2.time_input("เวลาเริ่ม (ใหม่)", curr_start.time())
            new_e_date = c_edit1.date_input("วันที่คืน (ใหม่)", curr_end.date())
            new_e_time = c_edit2.time_input("เวลาคืน (ใหม่)", curr_end.time())
            
            if st.button("บันทึกการแก้ไข"):
                new_start_dt = datetime.combine(new_s_date, new_s_time)
                new_end_dt = datetime.combine(new_e_date, new_e_time)
                
                if new_start_dt >= new_end_dt:
                    st.error("เวลาคืนต้องหลังเวลาเริ่ม")
                else:
                    # เช็คว่าว่างไหม (ส่ง selected_row_idx ไปบอกว่าอย่าเช็คชนกับตัวเองนะ)
                    available, msg = is_car_available(df, row_data['Car'], new_start_dt, new_end_dt, excl_index=selected_row_idx)
                    
                    if available:
                        # อัปเดตข้อมูลใน DataFrame
                        df.at[selected_row_idx, 'Start_Time'] = new_start_dt
                        df.at[selected_row_idx, 'End_Time'] = new_end_dt
                        if 'Display' in df.columns: df = df.drop(columns=['Display'])
                        
                        save_to_gsheet(df)
                        st.success("แก้ไขเวลาเรียบร้อย!")
                        st.rerun()
                    else:
                        st.error(f"เปลี่ยนไม่ได้: {msg}")

    else:
        st.info("ไม่มีรายการจอง")
