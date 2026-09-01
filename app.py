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
        "Geele-1": {"max_seats": 7, "cargo_score": 1800, "type": "company"},
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








import os
import time
import uuid
import socket
import threading
from datetime import datetime, date

import pymysql
import pymysql.cursors
from flask import Flask, request, jsonify, g, render_template

app = Flask(__name__)

MYSQL_HOST = os.environ.get("MYSQL_HOST", "mysql")
MYSQL_PORT = int(os.environ.get("MYSQL_PORT", "3306"))
MYSQL_USER = os.environ.get("MYSQL_USER", "root")
MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.environ.get("MYSQL_DATABASE", "car_service_tracker")

# --- ตั้งค่าแจ้งเตือนไป LAN receiver (แยกต่างหากจาก popup ในหน้าเว็บ) ---
# ใส่ IP ของเครื่องที่เปิด receiver.py รออยู่ คั่นด้วย comma ถ้ามีหลายเครื่อง
# เช่น NOTIFY_RECEIVER_IPS=192.168.1.50,192.168.1.51
NOTIFY_RECEIVER_IPS = [ip.strip() for ip in os.environ.get("NOTIFY_RECEIVER_IPS", "192.168.1.126").split(",") if ip.strip()]
NOTIFY_RECEIVER_PORT = int(os.environ.get("NOTIFY_RECEIVER_PORT", "5001"))  # ต้องตรงกับ PORT ใน receiver.py
NOTIFY_CHECK_HOUR = int(os.environ.get("NOTIFY_CHECK_HOUR", "11"))  # เช็ควันละครั้ง ตอนกี่โมง (24hr)
NOTIFY_THRESHOLD = 0.75  # เกณฑ์ "ใกล้ถึงกำหนด" เดียวกับที่ใช้ในหน้าเว็บ (คือ warn threshold)


def get_db():
    if "db" not in g:
        g.db = pymysql.connect(
            host=MYSQL_HOST,
            port=MYSQL_PORT,
            user=MYSQL_USER,
            password=MYSQL_PASSWORD,
            database=MYSQL_DATABASE,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
        )
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def query_all(sql, params=()):
    db = get_db()
    with db.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def query_one(sql, params=()):
    db = get_db()
    with db.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def execute(sql, params=()):
    db = get_db()
    with db.cursor() as cur:
        cur.execute(sql, params)
    db.commit()


def ensure_database():
    """Create the target database on the shared MySQL server if it doesn't exist yet.
    If the user doesn't have CREATE privileges (e.g. a restricted account on a shared
    server), this just logs a warning and assumes the database already exists."""
    try:
        conn = pymysql.connect(
            host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER,
            password=MYSQL_PASSWORD, charset="utf8mb4",
        )
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{MYSQL_DATABASE}` "
                f"CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[warn] ไม่สามารถสร้างฐานข้อมูล '{MYSQL_DATABASE}' อัตโนมัติ "
              f"(อาจไม่มีสิทธิ์ หรือมีฐานข้อมูลอยู่แล้ว): {e}")


def wait_for_mysql(max_attempts=15, delay_seconds=3):
    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            conn = pymysql.connect(
                host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER,
                password=MYSQL_PASSWORD, charset="utf8mb4",
            )
            conn.close()
            return
        except Exception as e:
            last_err = e
            print(f"[info] รอ MySQL ({MYSQL_HOST}:{MYSQL_PORT}) ... ความพยายามที่ {attempt}/{max_attempts}")
            time.sleep(delay_seconds)
    raise RuntimeError(f"เชื่อมต่อ MySQL ไม่สำเร็จหลังจากลองหลายครั้ง: {last_err}")


def _add_column_if_missing(cur, table, column, ddl):
    """Add a column to an existing table if it doesn't already exist yet.
    Uses information_schema so this works across MySQL versions that don't
    support 'ADD COLUMN IF NOT EXISTS', and keeps existing deployments safe
    to upgrade in place."""
    cur.execute(
        """
        SELECT COUNT(*) AS c FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s AND column_name = %s
        """,
        (MYSQL_DATABASE, table, column),
    )
    row = cur.fetchone()
    exists = row[0] if not isinstance(row, dict) else row["c"]
    if not exists:
        cur.execute(f"ALTER TABLE `{table}` ADD COLUMN {ddl}")


def init_db():
    wait_for_mysql()
    ensure_database()
    conn = pymysql.connect(
        host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER,
        password=MYSQL_PASSWORD, database=MYSQL_DATABASE, charset="utf8mb4",
    )
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS vehicles (
                id VARCHAR(36) PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                plate VARCHAR(64),
                current_mileage DOUBLE DEFAULT 0
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS items (
                id VARCHAR(36) PRIMARY KEY,
                vehicle_id VARCHAR(36) NOT NULL,
                name VARCHAR(255) NOT NULL,
                last_mileage DOUBLE DEFAULT 0,
                interval_km DOUBLE DEFAULT 0,
                last_date DATE NULL,
                interval_months DOUBLE DEFAULT 0,
                FOREIGN KEY (vehicle_id) REFERENCES vehicles(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS mileage_logs (
                id VARCHAR(36) PRIMARY KEY,
                vehicle_id VARCHAR(36) NOT NULL,
                old_mileage DOUBLE,
                new_mileage DOUBLE,
                updated_by VARCHAR(255),
                created_at DATETIME,
                FOREIGN KEY (vehicle_id) REFERENCES vehicles(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
        # ระบบแจ้งเตือนการชาร์จแบต — สำหรับอุปกรณ์ใช้แบตทั่วไป (รีโมท ไฟฉาย เมาส์ ฯลฯ)
        # เป็นระบบที่แยกต่างหากจากรถโดยสิ้นเชิง ไม่เกี่ยวข้องกับตาราง vehicles/items ด้านบน
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS devices (
                id VARCHAR(36) PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                interval_days DOUBLE DEFAULT 0,
                last_charged_date DATE NULL,
                notes VARCHAR(255),
                serial_number VARCHAR(128)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
        # เผื่อฐานข้อมูลเดิมที่ยังไม่มีคอลัมน์นี้ (deploy ทับเวอร์ชันก่อนหน้า)
        _add_column_if_missing(cur, "devices", "serial_number", "serial_number VARCHAR(128) NULL")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS device_charge_logs (
                id VARCHAR(36) PRIMARY KEY,
                device_id VARCHAR(36) NOT NULL,
                old_date DATE NULL,
                new_date DATE,
                updated_by VARCHAR(255),
                created_at DATETIME,
                FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
    conn.commit()
    conn.close()


# ======================================================================
# ระบบแจ้งเตือนไป LAN receiver — เช็ควันละครั้ง ถ้าพบรายการ "ใกล้ถึงกำหนด"
# ขึ้นไป (percent >= 0.75 เหมือนเกณฑ์ popup ในหน้าเว็บ) จะส่งข้อความ
# ไปเด้ง popup ที่เครื่อง receiver ทุกเครื่องที่ตั้งค่าไว้ใน NOTIFY_RECEIVER_IPS
# แยกต่างหากจาก popup ในหน้าเว็บโดยสิ้นเชิง ไม่กระทบกัน
# ======================================================================

def send_lan_notification(message):
    """ส่งข้อความไปเด้ง popup ที่เครื่อง receiver ทุกเครื่องที่ตั้งค่าไว้"""
    if not NOTIFY_RECEIVER_IPS:
        return
    for ip in NOTIFY_RECEIVER_IPS:
        try:
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client.settimeout(5)
            client.connect((ip, NOTIFY_RECEIVER_PORT))
            client.send(message.encode("utf-8"))
            client.close()
        except Exception as e:
            print(f"[warn] ส่งแจ้งเตือนไป {ip}:{NOTIFY_RECEIVER_PORT} ไม่สำเร็จ: {e}")


def _item_percent(item, current_mileage):
    """คำนวณ percent เหมือนฟังก์ชัน computeStatus() ฝั่ง JS ใน index.html"""
    percent_km = None
    percent_date = None
    if item.get("interval_km") and item["interval_km"] > 0:
        used = current_mileage - (item.get("last_mileage") or 0)
        percent_km = used / item["interval_km"]
    if item.get("interval_months") and item["interval_months"] > 0 and item.get("last_date"):
        months_used = (date.today() - item["last_date"]).days / 30.4375
        percent_date = months_used / item["interval_months"]
    if percent_km is not None and percent_date is not None:
        return max(percent_km, percent_date)
    if percent_km is not None:
        return percent_km
    if percent_date is not None:
        return percent_date
    return 0


def _device_percent(device):
    """คำนวณ percent เหมือนฟังก์ชัน computeDeviceStatus() ฝั่ง JS ใน index.html"""
    if not device.get("last_charged_date") or not device.get("interval_days") or device["interval_days"] <= 0:
        return 1 if not device.get("last_charged_date") else 0
    days_since = (date.today() - device["last_charged_date"]).days
    return days_since / device["interval_days"]


def check_and_notify():
    """เช็คว่ามีรายการรถ/อุปกรณ์ไหนใกล้/ถึงกำหนดบ้าง แล้วรวมเป็นข้อความเดียวส่งไป LAN"""
    lines = []

    vehicles = query_all("SELECT * FROM vehicles")
    for v in vehicles:
        items = query_all("SELECT * FROM items WHERE vehicle_id = %s", (v["id"],))
        for item in items:
            percent = _item_percent(item, v["current_mileage"])
            if percent >= NOTIFY_THRESHOLD:
                status = "ถึงกำหนดแล้ว" if percent >= 1 else "ใกล้ถึงกำหนด"
                lines.append(f"รถ {v['name']} - {item['name']}: {status}")

    devices = query_all("SELECT * FROM devices")
    for d in devices:
        percent = _device_percent(d)
        if percent >= NOTIFY_THRESHOLD:
            status = "ถึงกำหนดแล้ว" if percent >= 1 else "ใกล้ถึงกำหนด"
            lines.append(f"อุปกรณ์ {d['name']}: {status}")

    if lines:
        message = "รายการที่ต้องดำเนินการ:\n" + "\n".join(lines)
        send_lan_notification(message)
        print(f"[info] ส่งแจ้งเตือน LAN แล้ว ({len(lines)} รายการ)")
    else:
        print("[info] เช็คแล้ว ไม่มีรายการใกล้ถึงกำหนด")


def _daily_notify_loop():
    """เช็คทันที 1 ครั้งตอน server เริ่มทำงาน (กันปัญหา start หลังเวลาที่ตั้งไว้แล้วต้องรอข้ามวัน)
    จากนั้นวนเช็คว่าถึงเวลา NOTIFY_CHECK_HOUR ของวันใหม่หรือยัง (เช็ควันละครั้งเท่านั้น
    ไม่ใช่ query ฐานข้อมูลถี่ๆ) ทำงานอยู่เบื้องหลังตลอดเวลาที่ server รันอยู่"""
    try:
        with app.app_context():
            print("[info] เช็คแจ้งเตือนครั้งแรกตอน server เริ่มทำงาน...")
            check_and_notify()
    except Exception as e:
        print(f"[warn] เช็คแจ้งเตือนล้มเหลว: {e}")
    last_run_date = datetime.now().date()  # กันไม่ให้เช็คซ้ำอีกรอบถ้าวันนี้ตรงกับ NOTIFY_CHECK_HOUR พอดี

    while True:
        now = datetime.now()
        if now.hour == NOTIFY_CHECK_HOUR and now.date() != last_run_date:
            try:
                with app.app_context():
                    check_and_notify()
            except Exception as e:
                print(f"[warn] เช็คแจ้งเตือนล้มเหลว: {e}")
            last_run_date = now.date()
        time.sleep(600)  # เช็คทุก 10 นาทีว่าเข้าเวลาที่ตั้งไว้หรือยัง


def vehicle_to_dict(row, items):
    return {
        "id": row["id"],
        "name": row["name"],
        "plate": row["plate"] or "",
        "currentMileage": row["current_mileage"],
        "items": items,
    }


def item_to_dict(row):
    return {
        "id": row["id"],
        "vehicleId": row["vehicle_id"],
        "name": row["name"],
        "lastMileage": row["last_mileage"],
        "intervalKm": row["interval_km"],
        "lastDate": str(row["last_date"]) if row["last_date"] else None,
        "intervalMonths": row["interval_months"],
    }


def device_to_dict(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "intervalDays": row["interval_days"],
        "lastChargedDate": str(row["last_charged_date"]) if row["last_charged_date"] else None,
        "notes": row["notes"] or "",
        "serialNumber": row.get("serial_number") or "",
    }


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/vehicles", methods=["GET"])
def list_vehicles():
    vehicles = query_all("SELECT * FROM vehicles")
    result = []
    for v in vehicles:
        items = query_all("SELECT * FROM items WHERE vehicle_id = %s", (v["id"],))
        result.append(vehicle_to_dict(v, [item_to_dict(i) for i in items]))
    return jsonify(result)


@app.route("/api/vehicles", methods=["POST"])
def create_vehicle():
    data = request.get_json(force=True)
    vid = str(uuid.uuid4())
    execute(
        "INSERT INTO vehicles (id, name, plate, current_mileage) VALUES (%s, %s, %s, %s)",
        (vid, data.get("name", "รถของฉัน"), data.get("plate", ""), data.get("currentMileage", 0)),
    )
    return jsonify({"id": vid}), 201


@app.route("/api/vehicles/<vid>", methods=["PUT"])
def update_vehicle(vid):
    data = request.get_json(force=True)
    execute(
        "UPDATE vehicles SET name = %s, plate = %s, current_mileage = %s WHERE id = %s",
        (data.get("name"), data.get("plate", ""), data.get("currentMileage", 0), vid),
    )
    return jsonify({"ok": True})


@app.route("/api/vehicles/<vid>", methods=["DELETE"])
def delete_vehicle(vid):
    execute("DELETE FROM items WHERE vehicle_id = %s", (vid,))
    execute("DELETE FROM mileage_logs WHERE vehicle_id = %s", (vid,))
    execute("DELETE FROM vehicles WHERE id = %s", (vid,))
    return jsonify({"ok": True})


@app.route("/api/vehicles/<vid>/mileage", methods=["POST"])
def update_mileage(vid):
    data = request.get_json(force=True)
    new_mileage = data.get("mileage")
    updated_by = (data.get("updatedBy") or "").strip()
    if not updated_by:
        return jsonify({"error": "updatedBy is required"}), 400
    row = query_one("SELECT current_mileage FROM vehicles WHERE id = %s", (vid,))
    if row is None:
        return jsonify({"error": "vehicle not found"}), 404
    old_mileage = row["current_mileage"]
    execute("UPDATE vehicles SET current_mileage = %s WHERE id = %s", (new_mileage, vid))
    log_id = str(uuid.uuid4())
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    execute(
        """INSERT INTO mileage_logs (id, vehicle_id, old_mileage, new_mileage, updated_by, created_at)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (log_id, vid, old_mileage, new_mileage, updated_by, created_at),
    )
    return jsonify({"ok": True})


@app.route("/api/vehicles/<vid>/mileage-log", methods=["GET"])
def get_mileage_log(vid):
    rows = query_all(
        "SELECT * FROM mileage_logs WHERE vehicle_id = %s ORDER BY created_at DESC, id DESC",
        (vid,),
    )
    return jsonify(
        [
            {
                "id": r["id"],
                "oldMileage": r["old_mileage"],
                "newMileage": r["new_mileage"],
                "updatedBy": r["updated_by"],
                "createdAt": str(r["created_at"]),
            }
            for r in rows
        ]
    )


@app.route("/api/vehicles/<vid>/items", methods=["POST"])
def create_item(vid):
    data = request.get_json(force=True)
    iid = str(uuid.uuid4())
    execute(
        """INSERT INTO items (id, vehicle_id, name, last_mileage, interval_km, last_date, interval_months)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (
            iid,
            vid,
            data.get("name"),
            data.get("lastMileage", 0),
            data.get("intervalKm", 0),
            data.get("lastDate") or None,
            data.get("intervalMonths", 0),
        ),
    )
    return jsonify({"id": iid}), 201


@app.route("/api/items/<iid>", methods=["PUT"])
def update_item(iid):
    data = request.get_json(force=True)
    execute(
        """UPDATE items SET name = %s, last_mileage = %s, interval_km = %s, last_date = %s, interval_months = %s
           WHERE id = %s""",
        (
            data.get("name"),
            data.get("lastMileage", 0),
            data.get("intervalKm", 0),
            data.get("lastDate") or None,
            data.get("intervalMonths", 0),
            iid,
        ),
    )
    return jsonify({"ok": True})


@app.route("/api/items/<iid>", methods=["DELETE"])
def delete_item(iid):
    execute("DELETE FROM items WHERE id = %s", (iid,))
    return jsonify({"ok": True})


init_db()

if NOTIFY_RECEIVER_IPS:
    threading.Thread(target=_daily_notify_loop, daemon=True).start()
    print(f"[info] เปิดใช้งานแจ้งเตือน LAN ไปยัง {NOTIFY_RECEIVER_IPS} "
          f"(port {NOTIFY_RECEIVER_PORT}) เวลา {NOTIFY_CHECK_HOUR}:00 ทุกวัน")
else:
    print("[info] ไม่ได้ตั้งค่า NOTIFY_RECEIVER_IPS - ปิดใช้งานแจ้งเตือน LAN "
          "(popup ในหน้าเว็บยังทำงานตามปกติ)")

# ======================================================================
# ระบบแจ้งเตือนการชาร์จแบต — สำหรับอุปกรณ์ใช้แบตทั่วไป (รีโมท ไฟฉาย เมาส์ ฯลฯ)
# แยกเป็นคนละระบบจากการเช็คระยะรถด้านบนโดยสิ้นเชิง ไม่มีความเกี่ยวข้องกับ vehicles/items
# ======================================================================

@app.route("/api/devices", methods=["GET"])
def list_devices():
    rows = query_all("SELECT * FROM devices ORDER BY name")
    return jsonify([device_to_dict(r) for r in rows])


@app.route("/api/devices", methods=["POST"])
def create_device():
    data = request.get_json(force=True)
    did = str(uuid.uuid4())
    execute(
        """INSERT INTO devices (id, name, interval_days, last_charged_date, notes, serial_number)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (
            did,
            data.get("name", "อุปกรณ์ใหม่"),
            data.get("intervalDays", 0),
            data.get("lastChargedDate") or None,
            data.get("notes", ""),
            data.get("serialNumber", ""),
        ),
    )
    return jsonify({"id": did}), 201


@app.route("/api/devices/<did>", methods=["PUT"])
def update_device(did):
    data = request.get_json(force=True)
    execute(
        """UPDATE devices SET name = %s, interval_days = %s, last_charged_date = %s, notes = %s, serial_number = %s
           WHERE id = %s""",
        (
            data.get("name"),
            data.get("intervalDays", 0),
            data.get("lastChargedDate") or None,
            data.get("notes", ""),
            data.get("serialNumber", ""),
            did,
        ),
    )
    return jsonify({"ok": True})


@app.route("/api/devices/<did>", methods=["DELETE"])
def delete_device(did):
    execute("DELETE FROM device_charge_logs WHERE device_id = %s", (did,))
    execute("DELETE FROM devices WHERE id = %s", (did,))
    return jsonify({"ok": True})


@app.route("/api/devices/<did>/charge", methods=["POST"])
def charge_device(did):
    data = request.get_json(force=True)
    updated_by = (data.get("updatedBy") or "").strip()
    if not updated_by:
        return jsonify({"error": "updatedBy is required"}), 400
    charged_date = data.get("chargedDate") or datetime.now().strftime("%Y-%m-%d")
    row = query_one("SELECT last_charged_date FROM devices WHERE id = %s", (did,))
    if row is None:
        return jsonify({"error": "device not found"}), 404
    old_date = row["last_charged_date"]
    execute("UPDATE devices SET last_charged_date = %s WHERE id = %s", (charged_date, did))
    log_id = str(uuid.uuid4())
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    execute(
        """INSERT INTO device_charge_logs (id, device_id, old_date, new_date, updated_by, created_at)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (log_id, did, old_date, charged_date, updated_by, created_at),
    )
    return jsonify({"ok": True})


@app.route("/api/devices/<did>/charge-log", methods=["GET"])
def get_device_charge_log(did):
    rows = query_all(
        "SELECT * FROM device_charge_logs WHERE device_id = %s ORDER BY created_at DESC, id DESC",
        (did,),
    )
    return jsonify(
        [
            {
                "id": r["id"],
                "oldDate": str(r["old_date"]) if r["old_date"] else None,
                "newDate": str(r["new_date"]) if r["new_date"] else None,
                "updatedBy": r["updated_by"],
                "createdAt": str(r["created_at"]),
            }
            for r in rows
        ]
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)