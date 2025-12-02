import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import time
import requests # ใช้ยิง API เหมือนเดิม

# --- CONFIG & SETUP ---
st.set_page_config(page_title="NavGo System (Telegram)", layout="wide", initial_sidebar_state="expanded")

def get_thai_time():
    return datetime.utcnow() + timedelta(hours=7)

def get_client():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    return client

# --- TELEGRAM NOTIFY FUNCTION (DEBUG MODE) ---
def send_telegram_notify(msg):
    try:
        # 1. เช็คว่าใส่ Secret ครบไหม
        if "telegram_token" not in st.secrets:
            st.error("❌ ไม่พบ 'telegram_token' ใน Secrets! กรุณาตรวจสอบการสะกด")
            return None
        if "telegram_chat_id" not in st.secrets:
            st.error("❌ ไม่พบ 'telegram_chat_id' ใน Secrets! กรุณาตรวจสอบการสะกด")
            return None

        token = st.secrets["telegram_token"]
        chat_id = st.secrets["telegram_chat_id"]
        
        # 2. ลองยิงข้อความ
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            'chat_id': chat_id,
            'text': msg,
            'parse_mode': 'HTML'
        }
        r = requests.post(url, data=payload)
        
        # 3. เช็คผลลัพธ์จาก Telegram
        if r.status_code == 200:
            # ถ้าสำเร็จ ไม่ต้องโชว์อะไร (หรืออยากโชว์ก็ได้)
            return r.status_code
        else:
            # ถ้าพัง ให้โชว์ Error Code จาก Telegram ออกมาดูเลย
            st.error(f"❌ แจ้งเตือนไม่ไป! Telegram ตอบกลับว่า: {r.text}")
            return r.status_code
            
    except Exception as e:
        st.error(f"❌ เกิดข้อผิดพลาดในระบบ: {e}")
        return None

# --- LOAD DATA ---
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

    # 3. Users
    try:
        ws_users = sh.worksheet("Users")
        df_users = pd.DataFrame(ws_users.get_all_records())
        if df_users.empty: df_users = pd.DataFrame(columns=["Name", "Department"])
    except:
        ws_users = sh.add_worksheet(title="Users", rows=100, cols=2)
        ws_users.append_row(["Name", "Department"])
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
    
    if 'booking_s_time' not in st.session_state:
        now = get_thai_time()
        next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0)
        st.session_state.booking_s_time = next_hour.time()
        st.session_state.booking_e_time = (next_hour + timedelta(hours=4)).time()
        st.session_state.booking_s_date = now.date()
        st.session_state.booking_e_date = now.date()

    CAR_SPECS = {
        "Honda Jazz 2019": {"max_seats": 5, "cargo_score": 400, "type": "company"},
        "Isuzu Mu-X": {"max_seats": 7, "cargo_score": 1000, "type": "company"},
        "Isuzu D-max 4 Doors": {"max_seats": 5, "cargo_score": 2500, "type": "company"},
        "🚙 รถส่วนตัว (เบิกค่าน้ำมัน)": {"max_seats": 99, "cargo_score": 9999, "type": "private"},
        "📦 ไม่ใช้รถ (ยืมเฉพาะของ)": {"max_seats": 99, "cargo_score": 9999, "type": "no_car"}
    }

    tab1, tab2 = st.tabs(["📦 จองใหม่", "📋 ตารางการใช้งาน"])

    with tab1:
        curr_s_date = st.session_state.booking_s_date
        curr_s_time = st.session_state.booking_s_time
        curr_e_date = st.session_state.booking_e_date
        curr_e_time = st.session_state.booking_e_time
        check_start_dt = datetime.combine(curr_s_date, curr_s_time)
        check_end_dt = datetime.combine(curr_e_date, curr_e_time)

        overlap_bookings_now = df_book[(df_book['Start_Time'] < check_end_dt) & (df_book['End_Time'] > check_start_dt)]
        busy_cars_set = set(overlap_bookings_now['Car'].str.strip().unique())

        c1, c2 = st.columns([1, 1])
        with c1:
            st.subheader("1. รายละเอียด")
            user_list = df_users['Name'].tolist() if not df_users.empty else ["Admin"]
            user = st.selectbox("ชื่อผู้จอง", user_list)
            task = st.text_input("ภารกิจ")
            loc = st.text_input("สถานที่")
            ppl = st.number_input("จำนวนคน", 1, 10, 2)
            
            st.divider()
            st.subheader("เลือกอุปกรณ์")
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

            total_load = sum([(df_stock[df_stock['ItemName']==k]['VolumeScore'].values[0] * v) for k, v in selected_equip.items() if k in df_stock['ItemName'].values])
            equip_str_list = [f"{k} x{v}" for k, v in selected_equip.items()]
            equip_final_str = ", ".join(equip_str_list) if equip_str_list else "-"

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

            if not valid_cars: st.warning("⚠️ ไม่มีรถว่าง หรือ ของเยอะเกิน")
            
            sel_car = st.selectbox("เลือก:", valid_cars if valid_cars else ["ไม่มีตัวเลือก"])

            btn_disabled = (not valid_cars) or (sel_car == "ไม่มีตัวเลือก")
            if st.button("🚀 ยืนยัน", type="primary", disabled=btn_disabled):
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
                    
                    # --- SEND TELEGRAM NOTIFICATION (UPDATED) ---
                    # ใช้ <b>...</b> แทน **...** เพราะ Telegram ใช้ HTML Mode
                    telegram_msg = (
                        f"📣 <b>มีรายการจองใหม่ (NavGo)</b>\n"
                        f"------------------------\n"
                        f"👤 ผู้จอง: {user}\n"
                        f"📝 ภารกิจ: {task}\n"
                        f"📍 สถานที่: {loc}\n"
                        f"🚗 รถ: {sel_car}\n"
                        f"📦 อุปกรณ์: {equip_final_str}\n"
                        f"🕒 เริ่ม: {check_start_dt.strftime('%d/%m %H:%M')}\n"
                        f"🕒 คืน: {check_end_dt.strftime('%d/%m %H:%M')}\n"
                        f"------------------------\n"
                        f"<i>รบกวน Admin เตรียมของด้วยครับ</i>"
                    )
                    send_telegram_notify(telegram_msg)
                    # -------------------------

                    st.success("บันทึกสำเร็จ! แจ้งเตือน Telegram แล้ว")
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
            cols_to_show = ['User', 'Task', 'Location', 'Car', 'Equipment', 'Start_Time', 'End_Time']
            st.dataframe(show_df[cols_to_show], use_container_width=True)

# --- PAGE: ADMIN & INVENTORY ---
def page_admin(df_book, df_stock, df_users, sh):
    st.title("🛠️ Admin Dashboard")
    now = get_thai_time()
    
    st.write("### 🕵️‍♂️ Monitor: ใครใช้อุปกรณ์อยู่บ้างขณะนี้?")
    st.caption(f"ข้อมูล ณ เวลา: {now.strftime('%d/%m/%Y %H:%M')}")

    active_bookings = pd.DataFrame()
    if not df_book.empty:
        active_bookings = df_book[(df_book['Start_Time'] <= now) & (df_book['End_Time'] >= now)]

    found_borrower = False
    if not active_bookings.empty:
        for _, row in active_bookings.iterrows():
            equip_list = str(row['Equipment'])
            if equip_list not in ["-", "", "nan", "{}"]:
                found_borrower = True
                with st.container():
                    st.info(
                        f"👤 **{row['User']}** (ภารกิจ: {row['Task']})\n\n"
                        f"🚗 **พาหนะ:** {row['Car']}\n\n"
                        f"📦 **กำลังใช้งาน:** {equip_list}\n\n"
                        f"🕒 **กำหนดคืน:** {row['End_Time'].strftime('%H:%M')} (เหลือ {(row['End_Time'] - now).seconds // 3600} ชม.)"
                    )
    
    if not found_borrower: st.success("✅ ขณะนี้ไม่มีใครเบิกอุปกรณ์ออกไป")

    st.divider()
    st.write("### 👥 จัดการรายชื่อพนักงาน")
    with st.expander("แก้ไขรายชื่อ"):
        edited_users = st.data_editor(df_users, num_rows="dynamic", use_container_width=True)
        if st.button("บันทึกรายชื่อ"):
            save_users(sh, edited_users)
            st.success("บันทึกเรียบร้อย")
            st.rerun()

    st.divider()
    st.write("### 📊 คลังเครื่องมือ")
    status_df = get_stock_status(df_book, df_stock, now)
    if not status_df.empty:
        status_df = status_df.sort_values(by="Available")
        cols = st.columns(4)
        idx = 0
        for item_name, row in status_df.iterrows():
            with cols[idx % 4]:
                st.metric(label=item_name, value=f"{int(row['Available'])} / {int(row['Total'])}", delta=f"-{int(row['Used'])} ใช้" if row['Used']>0 else "ว่าง")
            idx+=1
            
    with st.expander("แก้ไข Stock"):
        edited_stock = st.data_editor(df_stock, num_rows="dynamic", use_container_width=True)
        if st.button("บันทึก Stock"):
            save_stock(sh, edited_stock)
            st.success("บันทึกเรียบร้อย")
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
