import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import time

# --- CONFIG & SETUP ---
st.set_page_config(page_title="NavGo System V5", layout="wide", initial_sidebar_state="expanded")

def get_thai_time():
    return datetime.utcnow() + timedelta(hours=7)

def get_client():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    return client

# --- LOAD DATA (เพิ่มส่วน Users) ---
def load_data():
    client = get_client()
    sh = client.open("CarBookingDB")
    
    # 1. Bookings
    try:
        ws_book = sh.sheet1
        data_book = ws_book.get_all_records()
        df_book = pd.DataFrame(data_book)
        if df_book.empty:
            df_book = pd.DataFrame(columns=["User", "Task", "Car", "People", "Equipment", "Location", "Start_Time", "End_Time"])
        else:
            df_book['Start_Time'] = pd.to_datetime(df_book['Start_Time'].astype(str), errors='coerce')
            df_book['End_Time'] = pd.to_datetime(df_book['End_Time'].astype(str), errors='coerce')
            df_book = df_book.dropna(subset=['Start_Time', 'End_Time'])
            df_book['Car'] = df_book['Car'].astype(str).str.strip()
            df_book['Equipment'] = df_book['Equipment'].astype(str)
    except:
        df_book = pd.DataFrame(columns=["User", "Task", "Car", "People", "Equipment", "Location", "Start_Time", "End_Time"])

    # 2. Stock Master
    try:
        ws_stock = sh.worksheet("StockMaster")
        df_stock = pd.DataFrame(ws_stock.get_all_records())
        if df_stock.empty: df_stock = pd.DataFrame(columns=["ItemName", "TotalQty", "VolumeScore", "Description"])
    except:
        ws_stock = sh.add_worksheet(title="StockMaster", rows=100, cols=5)
        ws_stock.append_row(["ItemName", "TotalQty", "VolumeScore", "Description"])
        df_stock = pd.DataFrame(columns=["ItemName", "TotalQty", "VolumeScore", "Description"])

    # 3. Users (NEW!)
    try:
        ws_users = sh.worksheet("Users")
        df_users = pd.DataFrame(ws_users.get_all_records())
        if df_users.empty: df_users = pd.DataFrame(columns=["Name", "Department"])
    except:
        ws_users = sh.add_worksheet(title="Users", rows=100, cols=2)
        ws_users.append_row(["Name", "Department"]) # Header
        # ใส่ชื่อตัวอย่าง
        ws_users.append_row(["Admin", "IT"])
        df_users = pd.DataFrame([{"Name": "Admin", "Department": "IT"}])

    return df_book, df_stock, df_users, sh

# --- SAVE FUNCTIONS ---
def save_booking(sh, df):
    ws = sh.sheet1
    export_df = df.copy()
    export_df['Start_Time'] = export_df['Start_Time'].dt.strftime('%Y-%m-%d %H:%M:%S')
    export_df['End_Time'] = export_df['End_Time'].dt.strftime('%Y-%m-%d %H:%M:%S')
    ws.clear()
    ws.update([export_df.columns.values.tolist()] + export_df.values.tolist())

def save_stock(sh, df):
    ws = sh.worksheet("StockMaster")
    ws.clear()
    ws.update([df.columns.values.tolist()] + df.values.tolist())

def save_users(sh, df):
    ws = sh.worksheet("Users")
    ws.clear()
    ws.update([df.columns.values.tolist()] + df.values.tolist())

# --- HELPERS ---
def parse_equip_str(equip_str):
    if not equip_str or equip_str == "-" or equip_str == "nan": return {}
    items = {}
    parts = equip_str.split(',')
    for part in parts:
        part = part.strip()
        if ' x' in part:
            try:
                name, qty = part.rsplit(' x', 1) 
                items[name.strip()] = int(qty)
            except: continue
    return items

def get_stock_status(df_book, df_stock, query_time=None):
    if query_time is None: query_time = get_thai_time()
    stock_status = {}
    for _, row in df_stock.iterrows():
        stock_status[row['ItemName']] = {"Total": int(row['TotalQty']), "Used": 0, "Available": 0}
    
    if not df_book.empty:
        active_bookings = df_book[(df_book['Start_Time'] <= query_time) & (df_book['End_Time'] >= query_time)]
        for _, row in active_bookings.iterrows():
            used_items = parse_equip_str(row['Equipment'])
            for item, qty in used_items.items():
                if item in stock_status: stock_status[item]['Used'] += qty
    
    for item in stock_status:
        stock_status[item]['Available'] = stock_status[item]['Total'] - stock_status[item]['Used']
    return pd.DataFrame(stock_status).T

# --- PAGE: CAR BOOKING ---
def page_car_booking(df_book, df_stock, df_users, sh):
    st.title("🚗 NavGo: จองรถและอุปกรณ์")
    st.caption(f"Time: {get_thai_time().strftime('%d/%m/%Y %H:%M')}")
    
    # Time State
    if 'booking_s_time' not in st.session_state:
        now = get_thai_time()
        next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0)
        st.session_state.booking_s_time = next_hour.time()
        st.session_state.booking_e_time = (next_hour + timedelta(hours=4)).time()
        st.session_state.booking_s_date = now.date()
        st.session_state.booking_e_date = now.date()

    # --- CAR SPECS (รวมรถพิเศษ) ---
    CAR_SPECS = {
        "Honda Jazz 2019": {"max_seats": 5, "cargo_score": 400, "type": "company"},
        "Isuzu Mu-X": {"max_seats": 7, "cargo_score": 1000, "type": "company"},
        "Isuzu D-max 4 Doors": {"max_seats": 5, "cargo_score": 2500, "type": "company"},
        # รถพิเศษ (Capacity สูง เพื่อให้ผ่านเงื่อนไขเสมอ)
        "🚙 รถส่วนตัว (เบิกค่าน้ำมัน)": {"max_seats": 99, "cargo_score": 9999, "type": "private"},
        "📦 ไม่ใช้รถ (ยืมเฉพาะของ)": {"max_seats": 99, "cargo_score": 9999, "type": "no_car"}
    }

    tab1, tab2 = st.tabs(["📦 จองใหม่", "📋 ตารางการใช้งาน"])

    with tab1:
        # Pre-calc
        curr_s_date = st.session_state.booking_s_date
        curr_s_time = st.session_state.booking_s_time
        curr_e_date = st.session_state.booking_e_date
        curr_e_time = st.session_state.booking_e_time
        check_start_dt = datetime.combine(curr_s_date, curr_s_time)
        check_end_dt = datetime.combine(curr_e_date, curr_e_time)

        overlap_bookings_now = df_book[(df_book['Start_Time'] < check_end_dt) & (df_book['End_Time'] > check_start_dt)]
        # Busy Car Logic: เฉพาะรถบริษัทเท่านั้นที่ต้องเช็คว่าว่างไหม
        busy_cars_set = set(overlap_bookings_now['Car'].str.strip().unique())

        c1, c2 = st.columns([1, 1])
        with c1:
            st.subheader("1. รายละเอียด")
            
            # --- USER DROPDOWN ---
            user_list = df_users['Name'].tolist() if not df_users.empty else ["Admin"]
            user = st.selectbox("ชื่อผู้จอง", user_list)
            
            task = st.text_input("ภารกิจ")
            loc = st.text_input("สถานที่")
            ppl = st.number_input("จำนวนคน", 1, 10, 2)
            
            st.divider()
            st.subheader("เลือกอุปกรณ์")
            st.caption(f"เช็คยอดช่วง: {curr_s_time.strftime('%H:%M')} - {curr_e_time.strftime('%H:%M')}")
            
            selected_equip = {}
            if not df_stock.empty:
                for _, row in df_stock.iterrows():
                    item_name = row['ItemName']
                    total = int(row['TotalQty'])
                    used_count = 0
                    for _, b_row in overlap_bookings_now.iterrows():
                        b_items = parse_equip_str(b_row['Equipment'])
                        used_count += b_items.get(item_name, 0)
                    available = max(0, total - used_count)

                    cc1, cc2 = st.columns([3, 1])
                    if available == 0:
                        cc1.markdown(f"🔴 **{item_name}** (หมด!)")
                        max_val = 0
                    else:
                        color = "🟢" if available == total else "🟠"
                        cc1.markdown(f"{color} {item_name} (เหลือ {available})")
                        max_val = available

                    qty = cc2.number_input("จำนวน", key=f"q_{item_name}", min_value=0, max_value=max_val, value=0, label_visibility="collapsed", disabled=(max_val==0))
                    if qty > 0: selected_equip[item_name] = qty
            else:
                st.warning("กรุณาเพิ่มข้อมูล Stock")

        with c2:
            st.subheader("2. วันเวลา")
            d1, t1 = st.columns(2)
            s_date = d1.date_input("เริ่ม", key='booking_s_date')
            s_time = t1.time_input("เวลาเริ่ม", key='booking_s_time')
            d2, t2 = st.columns(2)
            e_date = d2.date_input("คืน", key='booking_e_date')
            e_time = t2.time_input("เวลาคืน", key='booking_e_time')

            # --- RECOMMENDATION LOGIC ---
            total_load = sum([(df_stock[df_stock['ItemName']==k]['VolumeScore'].values[0] * v) for k, v in selected_equip.items() if k in df_stock['ItemName'].values])
            equip_str_list = [f"{k} x{v}" for k, v in selected_equip.items()]
            equip_final_str = ", ".join(equip_str_list) if equip_str_list else "-"

            st.divider()
            st.subheader("3. เลือกพาหนะ")
            
            valid_cars = []
            for c_name, specs in CAR_SPECS.items():
                # 1. เช็ค Capacity
                if specs['max_seats'] >= ppl:
                    limit = specs['cargo_score'] if "D-max" in c_name or specs['type'] != 'company' else (specs['cargo_score'] - (ppl*20))
                    if total_load <= limit:
                        # 2. เช็คการจองซ้อน (เฉพาะรถบริษัท)
                        if specs['type'] == 'company':
                            if c_name not in busy_cars_set:
                                valid_cars.append(c_name)
                        else:
                            # รถส่วนตัว/ไม่ใช้รถ เลือกได้ตลอด ไม่ต้องเช็ค busy
                            valid_cars.append(c_name)

            if not valid_cars:
                st.warning("⚠️ ไม่มีรถว่าง หรือ ของเยอะเกิน")
            else:
                st.success(f"✅ ตัวเลือกที่ใช้ได้: {len(valid_cars)}")

            sel_car = st.selectbox("เลือก:", valid_cars if valid_cars else ["ไม่มีตัวเลือก"])

            btn_disabled = (not valid_cars) or (sel_car == "ไม่มีตัวเลือก")
            if st.button("🚀 ยืนยัน", type="primary", disabled=btn_disabled):
                # Final Check
                specs = CAR_SPECS.get(sel_car, {})
                is_company_car = specs.get('type') == 'company'
                
                final_overlap = pd.DataFrame()
                if is_company_car:
                     final_overlap = df_book[
                        (df_book['Start_Time'] < check_end_dt) & 
                        (df_book['End_Time'] > check_start_dt) &
                        (df_book['Car'] == sel_car)
                    ]

                if not final_overlap.empty:
                    st.error("❌ ช้าไปนิด! มีคนตัดหน้าจองรถคันนี้แล้ว")
                elif check_start_dt >= check_end_dt:
                    st.error("❌ เวลาผิดพลาด")
                elif not task:
                    st.error("❌ กรุณาระบุภารกิจ")
                else:
                    new_row = {
                        "User": user, "Task": task, "Car": sel_car,
                        "People": ppl, "Equipment": equip_final_str,
                        "Location": loc, "Start_Time": check_start_dt, "End_Time": check_end_dt
                    }
                    df_book = pd.concat([df_book, pd.DataFrame([new_row])], ignore_index=True)
                    save_booking(sh, df_book)
                    st.success("บันทึกสำเร็จ!")
                    for key in ['booking_s_time', 'booking_e_time', 'booking_s_date', 'booking_e_date']:
                        del st.session_state[key]
                    time.sleep(1)
                    st.rerun()

    with tab2:
        st.subheader("ตารางการจองทั้งหมด")
        if not df_book.empty:
            show_df = df_book.sort_values("Start_Time", ascending=False).copy()
            show_df['Start_Time'] = show_df['Start_Time'].dt.strftime('%d/%m %H:%M')
            show_df['End_Time'] = show_df['End_Time'].dt.strftime('%d/%m %H:%M')
            # เพิ่มคอลัมน์ Task และ Location ตามที่ขอ
            cols_to_show = ['User', 'Task', 'Location', 'Car', 'Equipment', 'Start_Time', 'End_Time']
            st.dataframe(show_df[cols_to_show], use_container_width=True)

# --- PAGE: ADMIN & INVENTORY ---
def page_admin(df_book, df_stock, df_users, sh):
    st.title("🛠️ Admin Dashboard")
    
    st.write("### 👥 จัดการรายชื่อพนักงาน")
    with st.expander("แก้ไขรายชื่อ (Dropdown)"):
        edited_users = st.data_editor(df_users, num_rows="dynamic", use_container_width=True)
        if st.button("บันทึกรายชื่อ"):
            save_users(sh, edited_users)
            st.success("บันทึกรายชื่อเรียบร้อย")
            st.rerun()

    st.divider()
    st.write("### 📊 คลังเครื่องมือ (Stock)")
    status_df = get_stock_status(df_book, df_stock, get_thai_time())
    if not status_df.empty:
        status_df = status_df.sort_values(by="Available")
        cols = st.columns(4)
        idx = 0
        for item_name, row in status_df.iterrows():
            with cols[idx % 4]:
                st.metric(label=item_name, value=f"{int(row['Available'])} / {int(row['Total'])}", delta=f"-{int(row['Used'])} ใช้" if row['Used']>0 else "ว่าง")
            idx+=1
            
    with st.expander("แก้ไขจำนวน Stock"):
        edited_stock = st.data_editor(df_stock, num_rows="dynamic", use_container_width=True)
        if st.button("บันทึก Stock"):
            save_stock(sh, edited_stock)
            st.success("บันทึก Stock เรียบร้อย")
            st.rerun()

# --- MAIN ---
try:
    df_book, df_stock, df_users, sh = load_data()
    with st.sidebar:
        st.header("NavGo Menu")
        page = st.radio("ไปที่หน้า:", ["🚗 จองรถ & อุปกรณ์", "🛠️ Admin & Stock"])
        st.write("---")
        st.caption(f"Time: {get_thai_time().strftime('%H:%M')}")

    if page == "🚗 จองรถ & อุปกรณ์":
        page_car_booking(df_book, df_stock, df_users, sh)
    else:
        page_admin(df_book, df_stock, df_users, sh)

except Exception as e:
    st.error(f"Error: {e}")
