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

# --- PAGE: ADMIN & INVENTORY ---
def page_admin(df_book, df_stock, df_users, sh):
    st.title("🛠️ Admin Dashboard")
    now = get_thai_time()
    
    # ------------------------------------------------
    # 1. DAILY REMINDER
    # ------------------------------------------------
    st.write("### 🔔 แจ้งเตือนการคืนของ (Manual Trigger)")
    with st.expander("กดเพื่อตรวจสอบและแจ้งเตือนเข้ากลุ่ม Telegram"):
        st.write("ระบบจะค้นหารายการที่ **ครบกำหนดคืนวันนี้** หรือ **เกินกำหนด** แล้วส่งสรุปเข้ากลุ่ม")
        if st.button("📢 ส่งแจ้งเตือนรายการคืนวันนี้", type="primary"):
            if df_book.empty:
                st.warning("ไม่มีข้อมูลการจอง")
            else:
                today_str = now.strftime('%Y-%m-%d')
                due_today = df_book[df_book['End_Time'].dt.strftime('%Y-%m-%d') == today_str]
                
                if due_today.empty:
                    st.info("✅ วันนี้ไม่มีรายการครบกำหนดคืน")
                else:
                    count = len(due_today)
                    msg_header = f"📢 <b>แจ้งเตือนรายการคืนวันนี้ ({now.strftime('%d/%m')})</b>\nมีทั้งหมด {count} รายการ\n----------------------------\n"
                    msg_body = ""
                    for _, row in due_today.iterrows():
                        # --- เพิ่มสถานที่ในสรุปรายวัน ---
                        msg_body += (
                            f"👤 <b>{row['User']}</b>\n"
                            f"📍 {row['Location']}\n" 
                            f"🚗 {row['Car']}\n"
                            f"📦 {row['Equipment']}\n"
                            f"🔴 คืนเวลา: {row['End_Time'].strftime('%H:%M')}\n\n"
                        )
                    
                    full_msg = msg_header + msg_body + "<i>รบกวนตรวจสอบและคืนของให้ตรงเวลาครับ</i>"
                    send_telegram_notify(full_msg)
                    st.success(f"ส่งแจ้งเตือน {count} รายการเรียบร้อย!")

    st.divider()

    # ------------------------------------------------
    # 2. MONITOR
    # ------------------------------------------------
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
    
    # ------------------------------------------------
    # 3. STOCK & USER
    # ------------------------------------------------
    st.write("### 📊 สถานะคลังเครื่องมือ")
    status_df = get_stock_status(df_book, df_stock, now)
    if not status_df.empty:
        status_df = status_df.sort_values(by="Available")
        cols = st.columns(4)
        for i, (item_name, row) in enumerate(status_df.iterrows()):
            with cols[i % 4]:
                delta_msg = f"-{int(row['Used'])} ใช้อยู่" if row['Used'] > 0 else "ครบ"
                delta_color = "inverse" if row['Available'] == 0 else "normal"
                st.metric(label=item_name, value=f"{int(row['Available'])} / {int(row['Total'])}", delta=delta_msg, delta_color=delta_color)
    
    with st.expander("📝 แก้ไข / เพิ่ม / ลบ อุปกรณ์ (คลิกที่นี่)"):
        st.caption("💡 วิธีใช้: แก้ไขตัวเลขในตารางได้เลย / เพิ่มแถวใหม่ด้านล่าง / ลบแถวโดยคลิกหน้าเลขแถวแล้วกด Delete")
        ed_stock = st.data_editor(df_stock, num_rows="dynamic", use_container_width=True, key="admin_stock")
        if st.button("💾 บันทึก Stock", type="primary"):
            save_stock(sh, ed_stock)
            st.rerun()

    st.divider()
    st.write("### 👥 รายชื่อพนักงาน")
    with st.expander("แก้ไขรายชื่อ"):
        ed_users = st.data_editor(df_users, num_rows="dynamic", use_container_width=True, key="admin_users")
        if st.button("บันทึกรายชื่อ"):
            save_users(sh, ed_users)
            st.rerun()
            
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
        "Honda Jazz 2019": {"max_seats": 5, "cargo_score": 1500, "type": "company"},
        "Isuzu Mu-X": {"max_seats": 7, "cargo_score": 1800, "type": "company"},
        "Isuzu D-max 4 Doors": {"max_seats": 5, "cargo_score": 2200, "type": "company"},
        "🚙 รถส่วนตัว (เบิกค่าน้ำมัน)": {"max_seats": 99, "cargo_score": 9999, "type": "private"},
        "📦 ไม่ใช้รถ (ยืมเฉพาะของ)": {"max_seats": 99, "cargo_score": 9999, "type": "no_car"}
    }

    tab1, tab2, tab3 = st.tabs(["📦 จองใหม่", "📋 ตารางการใช้งาน", "✏️ แก้ไข/ยกเลิก"])

    # --- TAB 1: จองใหม่ ---
    with tab1:
        curr_s_date = st.session_state.booking_s_date
        curr_s_time = st.session_state.booking_s_time
        curr_e_date = st.session_state.booking_e_date
        curr_e_time = st.session_state.booking_e_time
        check_start_dt = datetime.combine(curr_s_date, curr_s_time)
        check_end_dt = datetime.combine(curr_e_date, curr_e_time)

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
            st.caption(f"ยอดช่วง: {curr_s_time.strftime('%H:%M')} - {curr_e_time.strftime('%H:%M')}")
            
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
                    
                    # --- แจ้งเตือนจองใหม่ (เพิ่มสถานที่) ---
                    msg = (
                        f"📣 <b>จองใหม่ (NavGo)</b>\n"
                        f"----------------------------\n"
                        f"👤 <b>{user}</b>\n"
                        f"📝 ภารกิจ: {task}\n"
                        f"📍 <b>สถานที่: {loc}</b>\n"  # <--- เพิ่มตรงนี้
                        f"🚗 {sel_car}\n"
                        f"📦 {equip_final_str}\n"
                        f"----------------------------\n"
                        f"🟢 <b>วันยืม:</b> {check_start_dt.strftime('%d/%m/%Y %H:%M')}\n"
                        f"🔴 <b>วันคืน:</b> {check_end_dt.strftime('%d/%m/%Y %H:%M')}"
                    )
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

    # --- TAB 3: EDIT / DELETE ---
    with tab3:
        st.header("✏️ แก้ไข หรือ ยกเลิก")
        if not df_book.empty:
            manage_df = df_book.sort_values("Start_Time", ascending=False)
            booking_options = manage_df['Display'].tolist()
            selected_booking_str = st.selectbox("เลือกรายการ:", booking_options)
            
            if selected_booking_str:
                row_idx = df_book[df_book['Display'] == selected_booking_str].index[0]
                row_data = df_book.loc[row_idx]

                st.info(f"รายการ: **{row_data['Task']}** ({row_data['User']})")
                action = st.radio("Action:", ["❌ ยกเลิก (Delete)", "📝 แก้ไข (Edit)"], horizontal=True)

                if action == "❌ ยกเลิก (Delete)":
                    st.warning("ยืนยันที่จะลบ?")
                    if st.button("ยืนยันลบ", type="primary"):
                        df_book = df_book.drop(row_idx)
                        save_booking(sh, df_book)
                        # --- แจ้งเตือนลบ (เพิ่มสถานที่) ---
                        msg = f"❌ <b>ยกเลิกการจอง</b>\n👤 {row_data['User']}\n📝 {row_data['Task']}\n📍 {row_data['Location']}\n🚗 {row_data['Car']}"
                        send_telegram_notify(msg)
                        st.success("ลบเรียบร้อย!")
                        time.sleep(1)
                        st.rerun()

                elif action == "📝 แก้ไข (Edit)":
                    st.write("--- แก้ไข ---")
                    c_ed_t1, c_ed_t2 = st.columns(2)
                    new_s_date = c_ed_t1.date_input("วันยืม (ใหม่)", value=row_data['Start_Time'].date())
                    new_s_time = c_ed_t1.time_input("เวลายืม (ใหม่)", value=row_data['Start_Time'].time())
                    new_e_date = c_ed_t2.date_input("วันคืน (ใหม่)", value=row_data['End_Time'].date())
                    new_e_time = c_ed_t2.time_input("เวลาคืน (ใหม่)", value=row_data['End_Time'].time())
                    
                    new_start_dt = datetime.combine(new_s_date, new_s_time)
                    new_end_dt = datetime.combine(new_e_date, new_e_time)

                    c_ed1, c_ed2 = st.columns(2)
                    with c_ed1:
                        ed_task = st.text_input("ภารกิจ", value=row_data['Task'])
                        ed_loc = st.text_input("สถานที่", value=row_data['Location'])
                    with c_ed2:
                        ed_car = st.selectbox("รถ", list(CAR_SPECS.keys()), index=list(CAR_SPECS.keys()).index(row_data['Car']) if row_data['Car'] in CAR_SPECS else 0)
                        ed_ppl = st.number_input("คน", 1, 10, int(row_data['People']))

                    st.write("--- อุปกรณ์ (คำนวณ Stock ใหม่) ---")
                    current_equip_dict = parse_equip_str(row_data['Equipment'])
                    other_overlaps = df_book[(df_book.index != row_idx) & (df_book['Start_Time'] < new_end_dt) & (df_book['End_Time'] > new_start_dt)]
                    
                    edited_equip_result = {}
                    if not df_stock.empty:
                        cols = st.columns(3)
                        for i, (idx_stock, stock_row) in enumerate(df_stock.iterrows()):
                            item_name = stock_row['ItemName']
                            total_qty = int(stock_row['TotalQty'])
                            used_by_others = sum([parse_equip_str(r['Equipment']).get(item_name, 0) for _, r in other_overlaps.iterrows()])
                            max_avail = max(0, total_qty - used_by_others)
                            default_val = min(current_equip_dict.get(item_name, 0), max_avail)
                            
                            with cols[i % 3]:
                                new_qty = st.number_input(f"{item_name} (ว่าง {max_avail})", 0, max_avail, default_val, key=f"ed_{row_idx}_{item_name}")
                                if new_qty > 0: edited_equip_result[item_name] = new_qty
                    
                    if st.button("💾 บันทึกแก้ไข", type="primary"):
                        if new_start_dt >= new_end_dt:
                            st.error("เวลาคืนต้องหลังเวลาเริ่ม")
                        else:
                            specs = CAR_SPECS.get(ed_car, {})
                            is_conflict = False
                            if specs.get('type') == 'company':
                                car_conflict = df_book[(df_book.index != row_idx) & (df_book['Car'] == ed_car) & (df_book['Start_Time'] < new_end_dt) & (df_book['End_Time'] > new_start_dt)]
                                if not car_conflict.empty: is_conflict = True
                            
                            if is_conflict:
                                st.error(f"❌ รถ {ed_car} ไม่ว่างช่วงใหม่นี้")
                            else:
                                new_equip_str = ", ".join([f"{k} x{v}" for k, v in edited_equip_result.items()]) if edited_equip_result else "-"
                                df_book.at[row_idx, 'Task'] = ed_task
                                df_book.at[row_idx, 'Location'] = ed_loc
                                df_book.at[row_idx, 'Car'] = ed_car
                                df_book.at[row_idx, 'People'] = ed_ppl
                                df_book.at[row_idx, 'Start_Time'] = new_start_dt
                                df_book.at[row_idx, 'End_Time'] = new_end_dt
                                df_book.at[row_idx, 'Equipment'] = new_equip_str
                                save_booking(sh, df_book)
                                
                                # --- แจ้งเตือนแก้ไข (เพิ่มสถานที่) ---
                                msg = (
                                    f"✏️ <b>แก้ไขรายการ (NavGo)</b>\n"
                                    f"👤 <b>{row_data['User']}</b>\n"
                                    f"📝 {ed_task}\n"
                                    f"📍 {ed_loc}\n" # <--- เพิ่มตรงนี้
                                    f"📦 {new_equip_str}\n"
                                    f"----------------------------\n"
                                    f"🟢 <b>วันยืมใหม่:</b> {new_start_dt.strftime('%d/%m/%Y %H:%M')}\n"
                                    f"🔴 <b>วันคืนใหม่:</b> {new_end_dt.strftime('%d/%m/%Y %H:%M')}"
                                )
                                send_telegram_notify(msg)
                                st.success("แก้ไขเรียบร้อย!")
                                time.sleep(1)
                                st.rerun()
        else:
            st.info("ไม่มีรายการ")

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
