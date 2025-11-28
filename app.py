import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- ตั้งค่า Google Sheets ---
# เราจะดึง Key จาก Secrets ของ Streamlit Cloud (จะตั้งค่าใน Step 4)
def get_google_sheet():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    # สร้าง dict จาก secrets
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    # ใส่ชื่อ Sheet ของคุณที่นี่ (ต้องตรงเป๊ะๆ)
    sheet = client.open("CarBookingDB").sheet1 
    return sheet

# รายชื่อรถ
CAR_LIST = ["รถคันที่ 1 (Isuzu Mu-X)", "รถคันที่ 2 (Honda Jazz)", "รถคันที่ 3 (Isuzu กระบะ)"]

st.set_page_config(page_title="ระบบจองรถบริษัท", layout="wide")
st.title("🚗 ระบบจองรถบริษัท (Cloud Version)")

# --- เชื่อมต่อ Data ---
try:
    sheet = get_google_sheet()
    data = sheet.get_all_records()
    df = pd.DataFrame(data)
    
    # ถ้า Sheet ว่าง ให้สร้างตารางเปล่า
    if df.empty:
        df = pd.DataFrame(columns=["User", "Task", "Car", "Location", "Start_Time", "End_Time"])
    else:
        # แปลง String เป็น Datetime
        df['Start_Time'] = pd.to_datetime(df['Start_Time'])
        df['End_Time'] = pd.to_datetime(df['End_Time'])
        
except Exception as e:
    st.error(f"เชื่อมต่อ Google Sheets ไม่ได้: {e}")
    st.stop()

# ฟังก์ชันบันทึก
def save_to_gsheet(dataframe):
    # แปลง datetime เป็น string ก่อนส่งไป sheet
    export_df = dataframe.copy()
    export_df['Start_Time'] = export_df['Start_Time'].dt.strftime('%Y-%m-%d %H:%M:%S')
    export_df['End_Time'] = export_df['End_Time'].dt.strftime('%Y-%m-%d %H:%M:%S')
    # อัปเดตข้อมูลทั้งหมด
    sheet.update([export_df.columns.values.tolist()] + export_df.values.tolist())

# ฟังก์ชันเช็คว่าง
def is_car_available(df, car, start, end):
    if df.empty: return True, "ว่าง"
    car_bookings = df[df['Car'] == car]
    for index, row in car_bookings.iterrows():
        if start < row['End_Time'] and end > row['Start_Time']:
            return False, f"ไม่ว่าง! ติดจองโดย {row['User']}"
    return True, "ว่าง"

# --- UI (เหมือนเดิม แต่ตัด Tab ทิ้งเพื่อความกระชับ) ---
tab1, tab2, tab3 = st.tabs(["📅 จองรถ", "📋 ตารางการใช้รถ", "❌ ยกเลิกการจอง"])

with tab1:
    col1, col2 = st.columns(2)
    with col1:
        user_name = st.text_input("ชื่อผู้จอง")
        task_name = st.text_input("ภารกิจ")
        location = st.text_input("สถานที่")
    with col2:
        selected_car = st.selectbox("เลือกรถ", CAR_LIST)
        today = datetime.now()
        start_date = st.date_input("วันที่เริ่ม", today)
        start_time = st.time_input("เวลาเริ่ม", today.time())
        end_date = st.date_input("วันที่คืน", today)
        end_time = st.time_input("เวลาคืน", (today + timedelta(hours=2)).time())

    if st.button("ยืนยันการจอง"):
        start_dt = datetime.combine(start_date, start_time)
        end_dt = datetime.combine(end_date, end_time)
        
        if start_dt >= end_dt:
            st.warning("เวลาคืนต้องหลังเวลาเริ่ม")
        elif not user_name:
            st.warning("ใส่ชื่อด้วยครับ")
        else:
            available, msg = is_car_available(df, selected_car, start_dt, end_dt)
            if available:
                new_row = {
                    "User": user_name, "Task": task_name, "Car": selected_car,
                    "Location": location, "Start_Time": start_dt, "End_Time": end_dt
                }
                df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
                save_to_gsheet(df) # บันทึกลง Sheets
                st.success("จองสำเร็จ!")
                st.rerun()
            else:
                st.error(msg)

with tab2:
    st.subheader("สถานะรถตอนนี้")
    now = datetime.now()
    cols = st.columns(3)
    for i, car in enumerate(CAR_LIST):
        usage = df[df['Car'] == car] if not df.empty else pd.DataFrame()
        if not usage.empty:
            current = usage[(usage['Start_Time'] <= now) & (usage['End_Time'] >= now)]
            with cols[i]:
                if not current.empty:
                    st.error(f"⛔ {car}")
                    st.write(f"ผู้ใช้: {current.iloc[0]['User']}")
                else:
                    st.success(f"✅ {car}")
                    st.caption("ว่าง")

    st.divider()
    if not df.empty:
        # แสดงตารางสวยๆ
        show_df = df.sort_values("Start_Time", ascending=False).copy()
        show_df['Start_Time'] = show_df['Start_Time'].dt.strftime('%d/%m %H:%M')
        show_df['End_Time'] = show_df['End_Time'].dt.strftime('%d/%m %H:%M')
        st.dataframe(show_df, use_container_width=True)

with tab3:
    if not df.empty:
        df['Display'] = df.apply(lambda x: f"{x['User']} - {x['Car']} ({x['Start_Time'].strftime('%d/%m')})", axis=1)
        del_item = st.selectbox("เลือกรายการลบ", df['Display'].unique())
        if st.button("ลบรายการ"):
            # ลบจาก DataFrame
            df = df[df['Display'] != del_item].drop(columns=['Display'])
            save_to_gsheet(df) # บันทึกลง Sheets
            st.success("ลบแล้ว")
            st.rerun()
