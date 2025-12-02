import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import time
import requests

# --- CONFIG & SETUP ---
st.set_page_config(page_title="NavGo System V8 (Manage)", layout="wide", initial_sidebar_state="expanded")

def get_thai_time():
    return datetime.utcnow() + timedelta(hours=7)

def get_client():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    return client

# --- NOTIFY FUNCTION ---
def send_telegram_notify(msg):
    try:
        # Check secrets location (support both root and nested)
        if "telegram_token" in st.secrets:
            token = st.secrets["telegram_token"]
            chat_id = st.secrets["telegram_chat_id"]
        elif "telegram" in st.secrets:
            token = st.secrets["telegram"]["telegram_token"]
            chat_id = st.secrets["telegram"]["telegram_chat_id"]
        else:
            return None

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {'chat_id': chat_id, 'text': msg, 'parse_mode': 'HTML'}
        requests.post(url, data=payload)
    except Exception:
        pass

# --- LOAD DATA ---
def load_data():
    client = get_client()
    try:
        sh = client.open("CarBookingDB")
    except:
        st.error("❌ หาไฟล์ Google Sheets ไม่เจอ")
        st.stop()
    
    existing_sheets = [ws.title for ws in sh.worksheets()]

    # 1. Bookings
    try:
        ws_book = sh.get_worksheet(0)
        data_book = ws_book.get_all_records()
        df_book = pd.DataFrame(data_book)
        if df_book.empty and len(data_book) == 0:
            df_book = pd.DataFrame(columns=["User", "Task", "Car", "People", "Equipment", "Location", "Start_Time", "End_Time"])
        
        if not df_book.empty:
            df_book['Start_Time'] = pd.to_datetime(df_book['Start_Time'].astype(str), errors='coerce')
            df_book['End_Time'] = pd.to_datetime(df_book['End_Time'].astype(str), errors='coerce')
            df_book = df_book.dropna(subset=['Start_Time', 'End_Time'])
            if 'Car' in df_book.columns: df_book['Car'] = df_book['Car'].astype(str).str.strip()
            if 'Equipment' in df_book.columns: df_book['Equipment'] = df_book['Equipment'].astype(str)
            # Create Display Column for Dropdown
            df_book['Display'] = df_book.apply(lambda x: f"{x['User']} | {x['Car']} | {x['Start_Time'].strftime('%d/%m %H:%M')}", axis=1)
    except:
        df_book = pd.DataFrame(columns=["User", "Task", "Car", "People", "Equipment", "Location", "Start_Time", "End_Time"])

    # 2. Stock & Users (Standard Load)
    if "StockMaster" in existing_sheets:
        ws_stock = sh.worksheet("StockMaster")
        df_stock = pd.DataFrame(ws_stock.get_all_records())
    else:
        ws_stock = sh.add_worksheet("StockMaster", 100, 5)
        ws_stock.append_row(["ItemName", "TotalQty", "VolumeScore", "Description"])
        df_stock = pd.DataFrame(columns=["ItemName", "TotalQty", "VolumeScore", "Description"])

    if "Users" in existing_sheets:
        ws_users = sh.worksheet("Users")
        df_users = pd.DataFrame(ws_users.get_all_records())
    else:
        ws_users = sh.add_worksheet("Users", 100, 2)
        ws_users.append_row(["Name", "Department"])
        ws_users.append_row(["Admin", "IT"])
        df_users = pd.DataFrame([{"Name": "Admin", "Department": "IT"}])

    return df_book, df_stock, df_users, sh

# --- SAVE FUNCTIONS ---
def save_booking(sh, df):
    ws = sh.sheet1
    # Remove Display column before saving
    export_df = df.copy()
    if 'Display' in export_df.columns: export_df = export_df.drop(columns=['Display'])
    
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
    if not equip_str or equip_str in ["-", "nan", ""]: return {}
    items = {}
    for part in equip_str.split(','):
        if ' x' in part:
            try:
                name, qty = part.strip().rsplit(' x', 1) 
                items[name.strip()] = int(qty)
            except: continue
    return items

def get_stock_status(df_book, df_stock, query_time=None):
    if query_time is None: query_time = get_thai_time()
    stock = {row['ItemName']: {"Total": int(row['TotalQty']), "Used": 0} for _, row in df_stock.iterrows()}
    
    if not df_book.empty:
        active = df_book[(df_book['Start_Time'] <= query_time) & (df_book['End_Time'] >= query_time)]
        for _, row in active.iterrows():
            for k, v in parse_equip_str(row['Equipment']).items():
                if k in stock: stock[k]['Used'] += v
    
    for k in stock: stock[k]['Available'] = stock[k]['Total'] - stock[k]['Used']
    return pd.DataFrame(stock).T

# --- PAGE: CAR BOOKING ---
def page_car_booking(df_book, df_stock, df_users, sh):
    st.title("🚗 NavGo: จองรถและอุปกรณ์")
    st.caption(f"Time: {get_thai_time().strftime('%d/%m/%Y %H:%M')}")
    
    # Init Time Session
    if 'booking_s_time' not in st.session_state:
        now = get_thai_time()
        next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0)
        st.session_state.booking_s_time = next_hour.time()
        st.session_state.booking_e_time = (next_hour + timedelta(hours=4)).time()
        st.session_state.booking_s_date = now.date()
        st.session_state.booking_e_date = now.date()

    CAR_SPECS = {
        "Honda Jazz 2019": {"max_seats": 5, "cargo_score": 1500, "type": "company"},
        "Isuzu Mu-X": {"max_seats": 7, "cargo_score": 2000, "type": "company"},
        "Isuzu D-max 4 Doors": {"max_seats": 5, "cargo_score": 2500, "type": "company"},
        "🚙 รถส่วนตัว": {"max_seats": 99, "cargo_score": 9999, "type": "private"},
        "📦 ไม่ใช้รถ (ยืมเฉพาะของ)": {"max_seats": 99, "cargo_score": 9999, "type": "no_car"}
    }

    tab1, tab2, tab3 = st.tabs(["📦 จองใหม่", "📋 ตารางการใช้งาน", "✏️ แก้ไข/ยกเลิก"])

    # --- TAB 1: NEW BOOKING ---
    with tab1:
        curr_s_date = st.session_state.booking_s_date
        curr_s_time = st.session_state.booking_s_time
        curr_e_date = st.session_state.booking_e_date
        curr_e_time = st.session_state.booking_e_time
        check_start_dt = datetime.combine(curr_s_date, curr_s_time)
        check_end_dt = datetime.combine(curr_e_date, curr_e_time)

        # Busy Check
        overlap_now = df_book[(df_book['Start_Time'] < check_end_dt) & (df_book['End_Time'] > check_start_dt)]
        busy_cars_set = set(overlap_now['Car'].str.strip().unique())

        c1, c2 = st.columns([1, 1])
        with c1:
            st.subheader("1. รายละเอียด")
            user_list = df_users['Name'].tolist() if not df_users.empty else ["Admin"]
            user = st.selectbox("ชื่อผู้จอง", user_list, key="new_user")
            task = st.text_input("ภารกิจ", key="new_task")
            loc = st.text_input("สถานที่", key="new_loc")
            ppl = st.number_input("จำนวนคน", 1, 10, 2, key="new_ppl")
            
            st.divider()
            st.subheader("เลือกอุปกรณ์")
            st.caption(f"ยอดคงเหลือช่วง: {curr_s_time.strftime('%H:%M')} - {curr_e_time.strftime('%H:%M')}")
            
            selected_equip = {}
            if not df_stock.empty:
                for _, row in df_stock.iterrows():
                    item_name = row['ItemName']
                    total = int(row['TotalQty'])
                    used = sum([parse_equip_str(r['Equipment']).get(item_name, 0) for _, r in overlap_now.iterrows()])
                    avail = max(0, total - used)

                    cc1, cc2 = st.columns([3, 1])
                    if avail == 0:
                        cc1.markdown(f"🔴 **{item_name}** (หมด)")
                        max_v = 0
                    else:
                        color = "🟢" if avail == total else "🟠"
                        cc1.markdown(f"{color} {item_name} ({avail})")
                        max_v = avail

                    qty = cc2.number_input("จำนวน", key=f"q_{item_name}", min_value=0, max_value=max_v, value=0, label_visibility="collapsed", disabled=(max_v==0))
                    if qty > 0: selected_equip[item_name] = qty

        with c2:
            st.subheader("2. วันเวลา")
            d1, t1 = st.columns(2)
            s_date = d1.date_input("เริ่ม", key='booking_s_date')
            s_time = t1.time_input("เวลาเริ่ม", key='booking_s_time')
            d2, t2 = st.columns(2)
            e_date = d2.date_input("คืน", key='booking_e_date')
            e_time = t2.time_input("เวลาคืน", key='booking_e_time')

            # Recommend
            total_load = sum([(df_stock[df_stock['ItemName']==k]['VolumeScore'].values[0] * v) for k, v in selected_equip.items() if k in df_stock['ItemName'].values])
            equip_final_str = ", ".join([f"{k} x{v}" for k, v in selected_equip.items()]) if selected_equip else "-"

            st.divider()
            st.subheader("3. เลือกพาหนะ")
            valid_cars = []
            for c_name, specs in CAR_SPECS.items():
                if specs['max_seats'] >= ppl:
                    limit = specs['cargo_score'] if "D-max" in c_name or specs['type'] != 'company' else (specs['cargo_score'] - (ppl*20))
                    if total_load <= limit:
                        if specs['type'] == 'company':
                            if c_name not in busy_cars_set: valid_cars.append(c_name)
                        else:
                            valid_cars.append(c_name)

            sel_car = st.selectbox("เลือก:", valid_cars if valid_cars else ["ไม่มีตัวเลือก"], key="new_car")
            
            if st.button("🚀 ยืนยันจอง", type="primary", disabled=(not valid_cars or sel_car == "ไม่มีตัวเลือก")):
                # Check Overlap again
                specs = CAR_SPECS.get(sel_car, {})
                final_overlap = pd.DataFrame()
                if specs.get('type') == 'company':
                    final_overlap = df_book[(df_book['Start_Time'] < check_end_dt) & (df_book['End_Time'] > check_start_dt) & (df_book['Car'] == sel_car)]

                if not final_overlap.empty:
                    st.error("❌ ช้าไปนิด! มีคนตัดหน้าจองแล้ว")
                elif check_start_dt >= check_end_dt:
                    st.error("❌ เวลาผิดพลาด")
                elif not task:
                    st.error("❌ กรุณาระบุภารกิจ")
                else:
                    new_row = {"User": user, "Task": task, "Car": sel_car, "People": ppl, "Equipment": equip_final_str, "Location": loc, "Start_Time": check_start_dt, "End_Time": check_end_dt}
                    df_book = pd.concat([df_book, pd.DataFrame([new_row])], ignore_index=True)
                    save_booking(sh, df_book)
                    
                    msg = f"📣 <b>จองใหม่ (NavGo)</b>\n👤 {user}\n📝 {task}\n🚗 {sel_car}\n📦 {equip_final_str}\n🕒 {check_start_dt.strftime('%d/%m %H:%M')} - {check_end_dt.strftime('%H:%M')}"
                    send_telegram_notify(msg)
                    
                    st.success("บันทึกสำเร็จ!")
                    for k in ['booking_s_time', 'booking_e_time', 'booking_s_date', 'booking_e_date']: del st.session_state[k]
                    time.sleep(1)
                    st.rerun()

    # --- TAB 2: TABLE ---
    with tab2:
        st.subheader("ตารางการจองทั้งหมด")
        if not df_book.empty:
            show_df = df_book.sort_values("Start_Time", ascending=False).copy()
            show_df['Start_Time'] = show_df['Start_Time'].dt.strftime('%d/%m %H:%M')
            show_df['End_Time'] = show_df['End_Time'].dt.strftime('%d/%m %H:%M')
            st.dataframe(show_df[['User', 'Task', 'Location', 'Car', 'Equipment', 'Start_Time', 'End_Time']], use_container_width=True)

    # --- TAB 3: EDIT / DELETE (NEW!) ---
    with tab3:
        st.header("✏️ แก้ไข หรือ ยกเลิกรายการ")
        
        # Filter only future or active bookings for easier selection
        now = get_thai_time()
        if not df_book.empty:
            # Sort by time desc
            manage_df = df_book.sort_values("Start_Time", ascending=False)
            
            # Select Booking
            booking_options = manage_df['Display'].tolist()
            selected_booking_str = st.selectbox("เลือกรายการที่ต้องการจัดการ:", booking_options)
            
            # Get Row Data
            if selected_booking_str:
                # Find index in original DF
                # Logic: We match the 'Display' string. Be careful with duplicates, but display has time so highly unlikely to dupe.
                row_idx = df_book[df_book['Display'] == selected_booking_str].index[0]
                row_data = df_book.loc[row_idx]

                st.info(f"คุณกำลังเลือก: **{row_data['Task']}** โดย **{row_data['User']}**")
                
                action = st.radio("เลือกสิ่งที่ต้องการทำ:", ["❌ ยกเลิกการจอง (Delete)", "📝 แก้ไขข้อมูล (Edit)"], horizontal=True)

                # --- DELETE ---
                if action == "❌ ยกเลิกการจอง (Delete)":
                    st.warning("ยืนยันที่จะลบรายการนี้ใช่ไหม? (ลบแล้วกู้คืนไม่ได้)")
                    if st.button("ยืนยันการลบ", type="primary"):
                        # Delete from DF
                        df_book = df_book.drop(row_idx)
                        save_booking(sh, df_book)
                        
                        # Notify
                        msg = f"❌ <b>ยกเลิกการจอง (NavGo)</b>\n👤 {row_data['User']}\n📝 {row_data['Task']}\n🚗 {row_data['Car']}\n🕒 เดิม: {row_data['Start_Time'].strftime('%d/%m %H:%M')}"
                        send_telegram_notify(msg)
                        
                        st.success("ลบรายการเรียบร้อย!")
                        time.sleep(1)
                        st.rerun()

                # --- EDIT ---
                elif action == "📝 แก้ไขข้อมูล (Edit)":
                    st.write("--- แก้ไขรายละเอียด ---")
                    
                    with st.form("edit_form"):
                        c_ed1, c_ed2 = st.columns(2)
                        with c_ed1:
                            # Pre-fill data
                            ed_task = st.text_input("ภารกิจ", value=row_data['Task'])
                            ed_loc = st.text_input("สถานที่", value=row_data['Location'])
                            ed_car = st.selectbox("รถที่ใช้", list(CAR_SPECS.keys()), index=list(CAR_SPECS.keys()).index(row_data['Car']) if row_data['Car'] in CAR_SPECS else 0)
                        
                        with c_ed2:
                            ed_s_date = st.date_input("วันที่เริ่ม", value=row_data['Start_Time'].date())
                            ed_s_time = st.time_input("เวลาเริ่ม", value=row_data['Start_Time'].time())
                            ed_e_date = st.date_input("วันที่คืน", value=row_data['End_Time'].date())
                            ed_e_time = st.time_input("เวลาคืน", value=row_data['End_Time'].time())

                        st.caption("*หมายเหตุ: การแก้ไข จะไม่เช็ค Stock อุปกรณ์ซ้ำ (ถือว่าใช้อันเดิม) แต่จะเช็คคิวรถให้ใหม่")
                        
                        if st.form_submit_button("บันทึกการแก้ไข"):
                            new_start = datetime.combine(ed_s_date, ed_s_time)
                            new_end = datetime.combine(ed_e_date, ed_e_time)
                            
                            if new_start >= new_end:
                                st.error("เวลาคืนต้องหลังเวลาเริ่ม")
                            else:
                                # Check Car Collision (Exclude current booking)
                                specs = CAR_SPECS.get(ed_car, {})
                                is_conflict = False
                                if specs.get('type') == 'company':
                                    # Filter bookings that overlap AND are not this row
                                    conflict = df_book[
                                        (df_book.index != row_idx) & # Exclude self
                                        (df_book['Car'] == ed_car) &
                                        (df_book['Start_Time'] < new_end) & 
                                        (df_book['End_Time'] > new_start)
                                    ]
                                    if not conflict.empty: is_conflict = True
                                
                                if is_conflict:
                                    st.error(f"❌ แก้ไขไม่ได้! รถ {ed_car} ติดจองในช่วงเวลาใหม่ที่เลือก")
                                else:
                                    # Update Data
                                    df_book.at[row_idx, 'Task'] = ed_task
                                    df_book.at[row_idx, 'Location'] = ed_loc
                                    df_book.at[row_idx, 'Car'] = ed_car
                                    df_book.at[row_idx, 'Start_Time'] = new_start
                                    df_book.at[row_idx, 'End_Time'] = new_end
                                    
                                    save_booking(sh, df_book)
                                    
                                    msg = f"✏️ <b>แก้ไขรายการ (NavGo)</b>\n👤 {row_data['User']}\n📝 {ed_task}\n🚗 {ed_car}\n🕒 ใหม่: {new_start.strftime('%d/%m %H:%M')} - {new_end.strftime('%H:%M')}"
                                    send_telegram_notify(msg)
                                    
                                    st.success("แก้ไขเรียบร้อย!")
                                    time.sleep(1)
                                    st.rerun()
        else:
            st.info("ยังไม่มีรายการจองให้จัดการ")

# --- PAGE: ADMIN & INVENTORY ---
def page_admin(df_book, df_stock, df_users, sh):
    st.title("🛠️ Admin Dashboard")
    now = get_thai_time()
    
    st.write("### 🕵️‍♂️ Monitor")
    active = pd.DataFrame()
    if not df_book.empty:
        active = df_book[(df_book['Start_Time'] <= now) & (df_book['End_Time'] >= now)]

    found = False
    if not active.empty:
        for _, row in active.iterrows():
            if str(row['Equipment']) not in ["-", "", "nan", "{}"]:
                found = True
                st.info(f"👤 **{row['User']}** ({row['Car']})\n📦 {row['Equipment']}\n🕒 คืน: {row['End_Time'].strftime('%H:%M')}")
    if not found: st.success("✅ ไม่มีใครเบิกของ")

    st.divider()
    st.write("### 👥 พนักงาน & Stock")
    with st.expander("รายชื่อพนักงาน"):
        ed_users = st.data_editor(df_users, num_rows="dynamic", use_container_width=True)
        if st.button("บันทึกชื่อ"):
            save_users(sh, ed_users)
            st.rerun()

    status_df = get_stock_status(df_book, df_stock, now)
    if not status_df.empty:
        st.dataframe(status_df[['Total', 'Used', 'Available']].sort_values('Available'), use_container_width=True)
            
    with st.expander("แก้ไข Stock"):
        ed_stock = st.data_editor(df_stock, num_rows="dynamic", use_container_width=True)
        if st.button("บันทึก Stock"):
            save_stock(sh, ed_stock)
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
