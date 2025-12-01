import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import time

# --- CONFIG & SETUP ---
st.set_page_config(page_title="NavGo System", layout="wide", initial_sidebar_state="expanded")

# --- TIMEZONE FIX: ฟังก์ชันดึงเวลาไทย ---
def get_thai_time():
    # ดึงเวลา UTC แล้วบวก 7 ชั่วโมง
    return datetime.utcnow() + timedelta(hours=7)

# Connect Google Sheets
def get_client():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    return client

# Load Data Functions
def load_data():
    client = get_client()
    sh = client.open("CarBookingDB")
    
    # 1. Load Bookings
    try:
        ws_book = sh.sheet1
        data_book = ws_book.get_all_records()
        df_book = pd.DataFrame(data_book)
        if df_book.empty:
            df_book = pd.DataFrame(columns=["User", "Task", "Car", "People", "Equipment", "Location", "Start_Time", "End_Time"])
        else:
            # Clean Data
            df_book['Start_Time'] = pd.to_datetime(df_book['Start_Time'].astype(str), errors='coerce')
            df_book['End_Time'] = pd.to_datetime(df_book['End_Time'].astype(str), errors='coerce')
            df_book = df_book.dropna(subset=['Start_Time', 'End_Time'])
            df_book['Car'] = df_book['Car'].astype(str).str.strip()
            df_book['Equipment'] = df_book['Equipment'].astype(str)
    except:
        df_book = pd.DataFrame(columns=["User", "Task", "Car", "People", "Equipment", "Location", "Start_Time", "End_Time"])

    # 2. Load Stock Master
    try:
        ws_stock = sh.worksheet("StockMaster")
        data_stock = ws_stock.get_all_records()
        df_stock = pd.DataFrame(data_stock)
        if df_stock.empty:
            df_stock = pd.DataFrame(columns=["ItemName", "TotalQty", "VolumeScore", "Description"])
    except:
        ws_stock = sh.add_worksheet(title="StockMaster", rows=100, cols=5)
        ws_stock.append_row(["ItemName", "TotalQty", "VolumeScore", "Description"])
        df_stock = pd.DataFrame(columns=["ItemName", "TotalQty", "VolumeScore", "Description"])

    return df_book, df_stock, sh

# Save Functions
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

# --- LOGIC HELPERS ---
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
            except:
                continue
    return items

def get_stock_status(df_book, df_stock, query_time=None):
    if query_time is None: query_time = get_thai_time() # ใช้เวลาไทย
    
    stock_status = {}
    for _, row in df_stock.iterrows():
        stock_status[row['ItemName']] = {
            "Total": int(row['TotalQty']), "Used": 0, "Available": 0
        }
    
    if not df_book.empty:
        active_bookings = df_book[(df_book['Start_Time'] <= query_time) & (df_book['End_Time'] >= query_time)]
        for _, row in active_bookings.iterrows():
            used_items = parse_equip_str(row['Equipment'])
            for item, qty in used_items.items():
                if item in stock_status:
                    stock_status[item]['Used'] += qty
    
    for item in stock_status:
        stock_status[item]['Available'] = stock_status[item]['Total'] - stock_status[item]['Used']
        
    return pd.DataFrame(stock_status).T

# --- PAGE: CAR BOOKING ---
def page_car_booking(df_book, df_stock, sh):
    st.title("🚗 NavGo: จองรถและอุปกรณ์")
    st.caption(f"เวลาปัจจุบัน (Thai Time): {get_thai_time().strftime('%d/%m/%Y %H:%M')}")
    
    # Initialize Time (Using Thai Time)
    if 'booking_s_time' not in st.session_state:
        now = get_thai_time() # ใช้เวลาไทย
        next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0)
        st.session_state.booking_s_time = next_hour.time()
        st.session_state.booking_e_time = (next_hour + timedelta(hours=4)).time()
        st.session_state.booking_s_date = now.date()
        st.session_state.booking_e_date = now.date()

    CAR_SPECS = {
        "Honda Jazz 2019": {"max_seats": 5, "cargo_score": 400},
        "Isuzu Mu-X": {"max_seats": 7, "cargo_score": 1000},
        "Isuzu D-max 4 Doors": {"max_seats": 5, "cargo_score": 2500}
    }

    tab1, tab2 = st.tabs(["📦 จองใหม่", "📋 ตารางรถ"])

    with tab1:
        # Pre-Calculation
        curr_s_date = st.session_state.booking_s_date
        curr_s_time = st.session_state.booking_s_time
        curr_e_date = st.session_state.booking_e_date
        curr_e_time = st.session_state.booking_e_time
        
        check_start_dt = datetime.combine(curr_s_date, curr_s_time)
        check_end_dt = datetime.combine(curr_e_date, curr_e_time)

        overlap_bookings_now = df_book[
            (df_book['Start_Time'] < check_end_dt) & 
            (df_book['End_Time'] > check_start_dt)
        ]
        busy_cars_set = set(overlap_bookings_now['Car'].str.strip().unique())

        c1, c2 = st.columns([1, 1])
        
        with c1:
            st.subheader("1. รายละเอียด")
            user = st.text_input("ชื่อผู้จอง")
            task = st.text_input("ภารกิจ")
            loc = st.text_input("สถานที่")
            ppl = st.number_input("จำนวนคน", 1, 10, 2)
            
            st.divider()
            st.subheader(f"เลือกอุปกรณ์")
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
                    
                    available = total - used_count
                    if available < 0: available = 0

                    cc1, cc2 = st.columns([3, 1])
                    if available == 0:
                        cc1.markdown(f"🔴 **{item_name}** (หมด!)")
                        max_val = 0
                    elif available < total:
                        cc1.markdown(f"🟠 {item_name} (เหลือ {available})")
                        max_val = available
                    else:
                        cc1.markdown(f"🟢 {item_name} (เหลือ {available})")
                        max_val = available

                    qty = cc2.number_input("จำนวน", key=f"q_{item_name}", min_value=0, max_value=max_val, value=0, label_visibility="collapsed", disabled=(max_val==0))
                    if qty > 0: selected_equip[item_name] = qty
            else:
                st.warning("กรุณาเพิ่มข้อมูลในเมนู Inventory")

        with c2:
            st.subheader("2. เลือกวันเวลา")
            d1, t1 = st.columns(2)
            s_date = d1.date_input("เริ่ม", key='booking_s_date')
            s_time = t1.time_input("เวลาเริ่ม", key='booking_s_time')
            
            d2, t2 = st.columns(2)
            e_date = d2.date_input("คืน", key='booking_e_date')
            e_time = t2.time_input("เวลาคืน", key='booking_e_time')
            
            valid_cars = []
            total_load = 0
            equip_str_list = []
            for k, v in selected_equip.items():
                try:
                    vol = df_stock[df_stock['ItemName'] == k]['VolumeScore'].values[0]
                    total_load += (vol * v)
                    equip_str_list.append(f"{k} x{v}")
                except: pass
            equip_final_str = ", ".join(equip_str_list) if equip_str_list else "-"

            st.divider()
            st.subheader("3. เลือกรถ")
            
            for c_name, specs in CAR_SPECS.items():
                if specs['max_seats'] >= ppl:
                    cargo_limit = specs['cargo_score'] if "D-max" in c_name else (specs['cargo_score'] - (ppl*20))
                    if total_load <= cargo_limit:
                        if c_name not in busy_cars_set:
                            valid_cars.append(c_name)

            if not valid_cars:
                st.warning("⚠️ ไม่มีรถว่าง หรือ รถรับไม่ไหว")
            else:
                st.success(f"✅ รถพร้อมใช้งาน: {len(valid_cars)} คัน")
            
            sel_car = st.selectbox("เลือกรถ", valid_cars if valid_cars else ["ไม่มีรถว่าง"])

            btn_disabled = (not valid_cars) or (sel_car == "ไม่มีรถว่าง")
            if st.button("🚀 ยืนยันการจอง", type="primary", disabled=btn_disabled):
                final_overlap = df_book[
                    (df_book['Start_Time'] < check_end_dt) & 
                    (df_book['End_Time'] > check_start_dt) &
                    (df_book['Car'] == sel_car)
                ]
                
                if not final_overlap.empty:
                    st.error("❌ ช้าไปนิด! มีคนเพิ่งจองตัดหน้าไปเมื่อกี้")
                elif check_start_dt >= check_end_dt:
                    st.error("❌ เวลาคืนต้องหลังเวลาเริ่ม")
                elif not user:
                    st.error("❌ กรุณาใส่ชื่อผู้จอง")
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
            st.dataframe(show_df[['User','Car','Equipment','Start_Time','End_Time']], use_container_width=True)

# --- PAGE: INVENTORY ---
def page_inventory(df_book, df_stock, sh):
    st.title("🛠️ NavGo: คลังเครื่องมือ")
    st.caption(f"ข้อมูล ณ เวลา: {get_thai_time().strftime('%d/%m/%Y %H:%M')}")
    
    # Use Thai Time for query
    status_df = get_stock_status(df_book, df_stock, get_thai_time())
    
    if not status_df.empty:
        status_df = status_df.sort_values(by="Available")
        cols = st.columns(4)
        idx = 0
        for item_name, row in status_df.iterrows():
            with cols[idx % 4]:
                st.metric(
                    label=item_name,
                    value=f"{int(row['Available'])} / {int(row['Total'])}",
                    delta=f"-{int(row['Used'])} ใช้อยู่" if row['Used'] > 0 else "พร้อม"
                )
            idx += 1
            
    st.divider()
    st.write("### 🕵️ ใครใช้อุปกรณ์อยู่บ้าง?")
    now = get_thai_time() # Use Thai Time
    active = df_book[(df_book['Start_Time'] <= now) & (df_book['End_Time'] >= now)]
    
    if not active.empty:
        for _, row in active.iterrows():
            if row['Equipment'] != "-" and row['Equipment'] != "nan":
                st.info(f"**{row['User']}** ({row['Car']}) ใช้อยู่: {row['Equipment']}")
    else:
        st.caption("ไม่มีการเบิกของในขณะนี้")
        
    st.divider()
    with st.expander("📝 จัดการ Stock Master (Admin)"):
        edited_df = st.data_editor(df_stock, num_rows="dynamic", use_container_width=True)
        if st.button("บันทึกการเปลี่ยนแปลง Stock"):
            save_stock(sh, edited_df)
            st.success("อัปเดตเรียบร้อย")
            st.rerun()

# --- MAIN APP ---
try:
    df_book, df_stock, sh = load_data()
    with st.sidebar:
        st.header("NavGo Menu")
        page = st.radio("ไปที่หน้า:", ["🚗 จองรถ & อุปกรณ์", "🛠️ คลังเครื่องมือ"])
        st.write("---")
        st.write(f"🕒 {get_thai_time().strftime('%H:%M:%S')}") # นาฬิกาไทยมุมซ้ายล่าง

    if page == "🚗 จองรถ & อุปกรณ์":
        page_car_booking(df_book, df_stock, sh)
    else:
        page_inventory(df_book, df_stock, sh)

except Exception as e:
    st.error(f"System Error: {e}")
