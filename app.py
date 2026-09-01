import streamlit as st
import pandas as pd
from datetime import datetime, timedelta, date
import calendar
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import time
import requests
import uuid

# --- CONFIG & SETUP ---
st.set_page_config(page_title="NavGo System V9 (All-in-One)", layout="wide", initial_sidebar_state="expanded")

def get_thai_time():
    return datetime.utcnow() + timedelta(hours=7)

@st.cache_resource
def get_client():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    return client

@st.cache_resource
def get_spreadsheet():
    """เปิดไฟล์ Google Sheets ครั้งเดียวแล้ว cache ไว้"""
    client = get_client()
    try:
        return client.open("CarBookingDB")
    except Exception:
        st.error("❌ หาไฟล์ Google Sheets ไม่เจอ")
        st.stop()

# --- NOTIFY FUNCTION ---
def send_telegram_notify(msg):
    try:
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
        requests.post(url, data=payload, timeout=5)
    except Exception:
        pass

# ============================================================
# LOAD DATA — NavGo (จองรถ) + Car Maintenance
# ============================================================
@st.cache_data(ttl=30, show_spinner=False)
def load_data():
    sh = get_spreadsheet()
    existing_sheets = [ws.title for ws in sh.worksheets()]

    def get_or_create(name, headers, rows=200, cols=10):
        if name in existing_sheets:
            ws = sh.worksheet(name)
            recs = ws.get_all_records()
            df = pd.DataFrame(recs) if recs else pd.DataFrame(columns=headers)
        else:
            ws = sh.add_worksheet(name, rows, cols)
            ws.append_row(headers)
            df = pd.DataFrame(columns=headers)
        return df

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
            df_book['Display'] = df_book.apply(lambda x: f"{x['User']} | {x['Car']} | {x['Start_Time'].strftime('%d/%m %H:%M')}", axis=1)
    except:
        df_book = pd.DataFrame(columns=["User", "Task", "Car", "People", "Equipment", "Location", "Start_Time", "End_Time"])

    # 2. Stock
    df_stock = get_or_create("StockMaster", ["ItemName", "TotalQty", "VolumeScore", "Description"])

    # 3. Users
    if "Users" in existing_sheets:
        df_users = get_or_create("Users", ["Name", "Department"])
    else:
        ws_users = sh.add_worksheet("Users", 100, 2)
        ws_users.append_row(["Name", "Department"])
        ws_users.append_row(["Admin", "IT"])
        df_users = pd.DataFrame([{"Name": "Admin", "Department": "IT"}])

    # 4. Vehicles
    df_vehicles = get_or_create("Vehicles", ["ID", "Name", "Plate", "CurrentMileage"])
    if not df_vehicles.empty:
        df_vehicles['CurrentMileage'] = pd.to_numeric(df_vehicles['CurrentMileage'], errors='coerce').fillna(0)

    # 5. Maintenance Items
    df_mitems = get_or_create("MaintItems", ["ID", "VehicleID", "Name", "LastMileage", "IntervalKm", "LastDate", "IntervalMonths"])
    if not df_mitems.empty:
        df_mitems['LastMileage'] = pd.to_numeric(df_mitems['LastMileage'], errors='coerce').fillna(0)
        df_mitems['IntervalKm'] = pd.to_numeric(df_mitems['IntervalKm'], errors='coerce').fillna(0)
        df_mitems['IntervalMonths'] = pd.to_numeric(df_mitems['IntervalMonths'], errors='coerce').fillna(0)
        df_mitems['LastDate'] = pd.to_datetime(df_mitems['LastDate'], errors='coerce')

    # 6. Mileage Logs
    df_mlogs = get_or_create("MileageLogs", ["ID", "VehicleID", "OldMileage", "NewMileage", "UpdatedBy", "CreatedAt"])

    # 7. Devices
    df_devices = get_or_create("Devices", ["ID", "Name", "IntervalDays", "LastChargedDate", "Notes", "SerialNumber"])
    if not df_devices.empty:
        df_devices['IntervalDays'] = pd.to_numeric(df_devices['IntervalDays'], errors='coerce').fillna(0)
        df_devices['LastChargedDate'] = pd.to_datetime(df_devices['LastChargedDate'], errors='coerce')

    # 8. Device Charge Logs
    df_dlogs = get_or_create("DeviceChargeLogs", ["ID", "DeviceID", "OldDate", "NewDate", "UpdatedBy", "CreatedAt"])

    return {
        "book": df_book, "stock": df_stock, "users": df_users,
        "vehicles": df_vehicles, "mitems": df_mitems, "mlogs": df_mlogs,
        "devices": df_devices, "dlogs": df_dlogs
    }

# --- SAVE FUNCTIONS ---
def save_booking(sh, df):
    ws = sh.sheet1
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

def _json_safe_cell(v):
    if isinstance(v, (pd.Timestamp, datetime, date)):
        return "" if pd.isna(v) else v.strftime('%Y-%m-%d')
    if isinstance(v, float) and pd.isna(v):
        return ""
    return v

def save_sheet(sh, sheet_name, df):
    ws = sh.worksheet(sheet_name)
    export_df = df.copy()
    for col in export_df.columns:
        export_df[col] = export_df[col].apply(_json_safe_cell)
    export_df = export_df.fillna("")
    ws.clear()
    ws.update([export_df.columns.values.tolist()] + export_df.values.tolist())

# --- HELPERS (Booking) ---
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

# --- HELPERS (Car Maintenance) ---
def compute_item_percent(row, current_mileage):
    percent_km = None
    percent_date = None
    if row.get('IntervalKm', 0) and row['IntervalKm'] > 0:
        used = current_mileage - (row.get('LastMileage') or 0)
        percent_km = used / row['IntervalKm']
    if row.get('IntervalMonths', 0) and row['IntervalMonths'] > 0 and pd.notna(row.get('LastDate')):
        months_used = (date.today() - row['LastDate'].date()).days / 30.4375
        percent_date = months_used / row['IntervalMonths']
    vals = [v for v in [percent_km, percent_date] if v is not None]
    return max(vals) if vals else 0

def compute_device_percent(row):
    if pd.isna(row.get('LastChargedDate')) or not row.get('IntervalDays') or row['IntervalDays'] <= 0:
        return 1 if pd.isna(row.get('LastChargedDate')) else 0
    days_since = (date.today() - row['LastChargedDate'].date()).days
    return days_since / row['IntervalDays']

def status_label(percent):
    if percent >= 1: return "🔴 ถึงกำหนดแล้ว"
    elif percent >= 0.75: return "🟠 ใกล้ถึงกำหนด"
    else: return "🟢 ปกติ"

# --- HELPERS (Icon Mapping) ---
def get_car_icon(car_name):
    """ส่งคืนไอคอนตามประเภทรถที่กำหนด"""
    car_str = str(car_name).strip()
    if "Mu-X" in car_str:
        return "🚙"  # SUV 7 ที่นั่ง
    elif "D-max" in car_str:
        return "🛻"  # รถกระบะ 4 ประตู
    elif "Geely" in car_str:
        return "⚡"  # รถไฟฟ้า EV
    elif "Jazz" in car_str:
        return "🚗"  # รถเก๋ง
    elif "ยืมเฉพาะของ" in car_str or "ไม่ใช้รถ" in car_str:
        return "📦"  # ยืมเฉพาะของ
    return "🚘"

@st.dialog("รายละเอียดการจอง")
def show_booking_detail(row):
    st.write(f"**ผู้จอง:** {row['User']}")
    st.write(f"**ภารกิจ:** {row['Task']}")
    st.write(f"**สถานที่:** {row['Location'] or '-'}")
    st.write(f"**รถ:** {row['Car']}")
    st.write(f"**อุปกรณ์:** {row['Equipment']}")
    st.write(f"**🟢 วันยืม:** {row['Start_Time'].strftime('%d/%m/%Y %H:%M')}")
    st.write(f"**🔴 วันคืน:** {row['End_Time'].strftime('%d/%m/%Y %H:%M')}")
    if st.button("ปิด"):
        st.session_state.pop('cal_selected_idx', None)
        st.rerun()

def render_booking_calendar(df_book, car_options=None):
    """ปฏิทินรายเดือนแบบ Google Calendar พร้อมตัวกรองไอคอนรถแบบใหม่"""
    if 'cal_year' not in st.session_state:
        now = get_thai_time()
        st.session_state['cal_year'] = now.year
        st.session_state['cal_month'] = now.month
    if 'cal_car_filter' not in st.session_state:
        st.session_state['cal_car_filter'] = "ทั้งหมด"

    nav1, nav2, nav3 = st.columns([1, 3, 1])
    with nav1:
        if st.button("← เดือนก่อน", use_container_width=True):
            m, y = st.session_state['cal_month'] - 1, st.session_state['cal_year']
            if m < 1: m, y = 12, y - 1
            st.session_state['cal_month'], st.session_state['cal_year'] = m, y
            st.rerun()
    with nav3:
        if st.button("เดือนถัดไป →", use_container_width=True):
            m, y = st.session_state['cal_month'] + 1, st.session_state['cal_year']
            if m > 12: m, y = 1, y + 1
            st.session_state['cal_month'], st.session_state['cal_year'] = m, y
            st.rerun()

    year, month = st.session_state['cal_year'], st.session_state['cal_month']
    thai_months = ["", "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
                   "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"]
    with nav2:
        st.markdown(f"<div style='text-align:center;font-weight:600;padding-top:6px;'>{thai_months[month]} {year + 543}</div>", unsafe_allow_html=True)

    show_only_equip = st.checkbox("📦 แสดงเฉพาะรายการยืมของ (ซ่อนการจองรถ)", value=False, key="cal_show_only_equip")

    if car_options:
        if show_only_equip:
            available_options = ["📦 ไม่ใช้รถ (ยืมเฉพาะของ)"]
        else:
            available_options = [c for c in car_options if c != "📦 ไม่ใช้รถ (ยืมเฉพาะของ)"]

        short_label = {
            "Honda Jazz 2019": "🚗 Honda Jazz",
            "Isuzu Mu-X": "🚙 Isuzu Mu-X",
            "Isuzu D-max 4 Doors": "🛻 Isuzu D-max",
            "Geely EX5": "⚡ Geely Ex5",
            "📦 ไม่ใช้รถ (ยืมเฉพาะของ)": "📦 ยืมเฉพาะของ",
        }

        filter_items = [("ทั้งหมด", "ทั้งหมด")] + [(short_label.get(c, c), c) for c in available_options]

        st.caption("เลือกกรองคันที่ต้องการดู:")
        f_cols = st.columns(len(filter_items))
        for col, (label, value) in zip(f_cols, filter_items):
            with col:
                is_checked = st.checkbox(
                    label,
                    value=(st.session_state['cal_car_filter'] == value),
                    key=f"cal_chk_{value}"
                )
                if is_checked and st.session_state['cal_car_filter'] != value:
                    st.session_state['cal_car_filter'] = value
                    st.rerun()

        if st.session_state['cal_car_filter'] not in ["ทั้งหมด"] + available_options:
            st.session_state['cal_car_filter'] = "ทั้งหมด"
            st.rerun()

        st.write("")

    days_in_month = calendar.monthrange(year, month)[1]
    first_weekday = (date(year, month, 1).weekday() + 1) % 7
    cells = [None] * first_weekday + list(range(1, days_in_month + 1))
    while len(cells) % 7 != 0:
        cells.append(None)
    weeks = [cells[i:i + 7] for i in range(0, len(cells), 7)]

    weekday_names = ["อา", "จ", "อ", "พ", "พฤ", "ศ", "ส"]
    hcols = st.columns(7)
    for c, name in zip(hcols, weekday_names):
        c.markdown(f"<div style='text-align:center;font-size:12px;color:gray;'>{name}</div>", unsafe_allow_html=True)

    df_valid = df_book.dropna(subset=['Start_Time', 'End_Time']) if not df_book.empty else df_book
    
    if not df_valid.empty:
        NO_CAR_VAL = "📦 ไม่ใช้รถ (ยืมเฉพาะของ)"
        if show_only_equip:
            df_valid = df_valid[df_valid['Car'] == NO_CAR_VAL]
        else:
            df_valid = df_valid[df_valid['Car'] != NO_CAR_VAL]

        if car_options and st.session_state.get('cal_car_filter', 'ทั้งหมด') != "ทั้งหมด":
            df_valid = df_valid[df_valid['Car'] == st.session_state['cal_car_filter']]

    for w_idx, week in enumerate(weeks):
        if all(d is None for d in week):
            continue

        dcols = st.columns(7)
        for c, d in zip(dcols, week):
            c.markdown(f"<div style='font-size:13px;padding:2px 4px;'>{d if d else ''}</div>", unsafe_allow_html=True)

        placed, lanes = [], []
        if not df_valid.empty:
            for idx, row in df_valid.iterrows():
                ev_start, ev_end = row['Start_Time'].date(), row['End_Time'].date()
                col_start = col_end = None
                for i, d in enumerate(week):
                    if d is None: continue
                    wd = date(year, month, d)
                    if ev_start <= wd <= ev_end:
                        if col_start is None: col_start = i
                        col_end = i
                if col_start is None: continue

                lane = 0
                def overlaps(seg, cs=col_start, ce=col_end):
                    return not (ce < seg[0] or cs > seg[1])
                while lane < len(lanes) and any(overlaps(seg) for seg in lanes[lane]):
                    lane += 1
                if lane == len(lanes): lanes.append([])
                lanes[lane].append((col_start, col_end))
                placed.append({'colStart': col_start, 'colEnd': col_end, 'lane': lane, 'row': row, 'idx': idx})

        max_lane = max([p['lane'] for p in placed], default=-1)
        for lane_i in range(max_lane + 1):
            lane_events = sorted([p for p in placed if p['lane'] == lane_i], key=lambda x: x['colStart'])
            widths, slots, cursor = [], [], 0
            for ev in lane_events:
                gap = ev['colStart'] - cursor
                if gap > 0:
                    widths.append(gap); slots.append(None)
                widths.append(ev['colEnd'] - ev['colStart'] + 1); slots.append(ev)
                cursor = ev['colEnd'] + 1
            if cursor < 7:
                widths.append(7 - cursor); slots.append(None)

            lane_cols = st.columns(widths)
            for col, slot in zip(lane_cols, slots):
                with col:
                    if slot is None:
                        st.write("")
                    else:
                        row = slot['row']
                        car_icon = get_car_icon(row['Car'])
                        label = f"{car_icon} {row['User']}: {row['Task']}"
                        if len(label) > 26: label = label[:24] + "…"
                        if st.button(label, key=f"cal_ev_{w_idx}_{lane_i}_{slot['idx']}", use_container_width=True):
                            st.session_state['cal_selected_idx'] = slot['idx']
                            st.rerun()
        st.markdown("<div style='border-bottom:1px solid rgba(128,128,128,0.2);margin:2px 0 8px;'></div>", unsafe_allow_html=True)

    if 'cal_selected_idx' in st.session_state:
        sel_idx = st.session_state['cal_selected_idx']
        if sel_idx in df_book.index:
            show_booking_detail(df_book.loc[sel_idx])
        else:
            st.session_state.pop('cal_selected_idx', None)

# --- PAGE: ADMIN & INVENTORY ---
def page_admin(df_book, df_stock, df_users, sh):
    st.title("🛠️ Admin Dashboard")
    now = get_thai_time()

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
            load_data.clear()
            st.rerun()

    st.divider()
    st.write("### 👥 รายชื่อพนักงาน")
    with st.expander("แก้ไขรายชื่อ"):
        ed_users = st.data_editor(df_users, num_rows="dynamic", use_container_width=True, key="admin_users")
        if st.button("บันทึกรายชื่อ"):
            save_users(sh, ed_users)
            load_data.clear()
            st.rerun()

# --- PAGE: CAR BOOKING ---
def page_car_booking(data, sh):
    df_book = data["book"]
    df_stock = data["stock"]
    df_users = data["users"]
    df_vehicles = data["vehicles"]
    df_mitems = data["mitems"]

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
        "Geely EX5": {"max_seats": 7, "cargo_score": 1800, "type": "company"},
        "📦 ไม่ใช้รถ (ยืมเฉพาะของ)": {"max_seats": 99, "cargo_score": 9999, "type": "no_car"}
    }

    tab1, tab2, tab3 = st.tabs(["📦 จองใหม่", "📋 ตารางการใช้งาน", "✏️ แก้ไข/ยกเลิก"])

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

                # --- [LOGIC เพิ่มเติม] เช็คการล็อกจองรถถ้าระยะทางเกินกำหนด +2,000 กม. ---
                is_mileage_blocked = False
                blocked_reason = ""
                
                if specs.get('type') == 'company':
                    v_match = df_vehicles[df_vehicles['Name'].str.strip() == sel_car.strip()]
                    if not v_match.empty:
                        v_row = v_match.iloc[0]
                        v_id = v_row['ID']
                        curr_mileage = float(v_row['CurrentMileage'])
                        
                        # ค้นหา MaintItems ของรถคันนี้
                        v_items = df_mitems[df_mitems['VehicleID'] == v_id]
                        for _, item in v_items.iterrows():
                            interval_km = float(item['IntervalKm'])
                            last_mileage = float(item['LastMileage'])
                            
                            if interval_km > 0:
                                max_allowed_km = last_mileage + interval_km + 2000  # ยอมให้เกินได้ไม่เกิน 2,000 กม.
                                if curr_mileage > max_allowed_km:
                                    is_mileage_blocked = True
                                    over_km = curr_mileage - (last_mileage + interval_km)
                                    blocked_reason = f"รถคันนี้เกินกำหนดเช็คระยะ ({item['Name']}) มาแล้ว {int(over_km):,} กม. (อนุญาตให้เกินได้ไม่เกิน 2,000 กม.) กรุณานำรถเข้าศูนย์บริการก่อนทำการจอง"
                                    break

                # --- ตรวจสอบเงื่อนไข ---
                if is_mileage_blocked:
                    st.error(f"❌ ไม่สามารถจองได้: {blocked_reason}")
                elif not final_overlap.empty:
                    st.error("❌ ช้าไปนิด! มีคนตัดหน้าจองแล้ว")
                elif check_start_dt >= check_end_dt:
                    st.error("❌ เวลาผิดพลาด")
                elif not task:
                    st.error("❌ กรุณาระบุภารกิจ")
                else:
                    new_row = {"User": user, "Task": task, "Car": sel_car, "People": ppl, "Equipment": equip_final_str, "Location": loc, "Start_Time": check_start_dt, "End_Time": check_end_dt}
                    df_book = pd.concat([df_book, pd.DataFrame([new_row])], ignore_index=True)
                    save_booking(sh, df_book)

                    msg = (
                        f"📣 <b>จองใหม่ (NavGo)</b>\n"
                        f"----------------------------\n"
                        f"👤 <b>{user}</b>\n"
                        f"📝 ภารกิจ: {task}\n"
                        f"📍 <b>สถานที่: {loc}</b>\n"
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
                    load_data.clear()
                    st.rerun()

    with tab2:
        st.subheader("📅 ปฏิทินการจองทั้งหมด")
        if df_book.empty:
            st.info("ไม่มีรายการจอง")
        else:
            render_booking_calendar(df_book, car_options=list(CAR_SPECS.keys()))

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
                        msg = f"❌ <b>ยกเลิกการจอง</b>\n👤 {row_data['User']}\n📝 {row_data['Task']}\n📍 {row_data['Location']}\n🚗 {row_data['Car']}"
                        send_telegram_notify(msg)
                        st.success("ลบเรียบร้อย!")
                        time.sleep(1)
                        load_data.clear()
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

                                msg = (
                                    f"✏️ <b>แก้ไขรายการ (NavGo)</b>\n"
                                    f"👤 <b>{row_data['User']}</b>\n"
                                    f"📝 {ed_task}\n"
                                    f"📍 {ed_loc}\n"
                                    f"📦 {new_equip_str}\n"
                                    f"----------------------------\n"
                                    f"🟢 <b>วันยืมใหม่:</b> {new_start_dt.strftime('%d/%m/%Y %H:%M')}\n"
                                    f"🔴 <b>วันคืนใหม่:</b> {new_end_dt.strftime('%d/%m/%Y %H:%M')}"
                                )
                                send_telegram_notify(msg)
                                st.success("แก้ไขเรียบร้อย!")
                                time.sleep(1)
                                load_data.clear()
                                st.rerun()
        else:
            st.info("ไม่มีรายการ")

# --- PAGE: CAR MAINTENANCE & DEVICE BATTERY ---
def page_car_maintenance(data, sh):
    df_vehicles = data["vehicles"]
    df_mitems = data["mitems"]
    df_mlogs = data["mlogs"]
    df_devices = data["devices"]
    df_dlogs = data["dlogs"]

    st.title("🔧 เช็คระยะรถ & แจ้งเตือนแบต")
    tab1, tab2, tab3, tab4 = st.tabs(["📊 ภาพรวม", "🚗 รถยนต์", "🔋 อุปกรณ์แบต", "📜 ประวัติ"])

    # --- TAB 1: DASHBOARD ---
    with tab1:
        st.subheader("สถานะรถยนต์")
        if df_vehicles.empty:
            st.info("ยังไม่มีข้อมูลรถ (ไปเพิ่มที่แท็บ 'รถยนต์')")
        else:
            for _, v in df_vehicles.iterrows():
                items = df_mitems[df_mitems['VehicleID'] == v['ID']]
                st.markdown(f"**🚗 {v['Name']}** ({v['Plate']}) — เลขไมล์ปัจจุบัน: {int(v['CurrentMileage']):,} กม.")
                if items.empty:
                    st.caption("ไม่มีรายการบำรุงรักษา")
                else:
                    cols = st.columns(3)
                    for i, (_, it) in enumerate(items.iterrows()):
                        pct = compute_item_percent(it, v['CurrentMileage'])
                        with cols[i % 3]:
                            st.metric(label=it['Name'], value=status_label(pct), delta=f"{pct*100:.0f}%")
                st.divider()

        st.subheader("สถานะอุปกรณ์แบต")
        if df_devices.empty:
            st.info("ยังไม่มีข้อมูลอุปกรณ์")
        else:
            cols = st.columns(4)
            for i, (_, d) in enumerate(df_devices.iterrows()):
                pct = compute_device_percent(d)
                with cols[i % 4]:
                    st.metric(label=d['Name'], value=status_label(pct))

    # --- TAB 2: VEHICLES ---
    with tab2:
        st.subheader("จัดการรถยนต์")
        with st.expander("➕ เพิ่มรถใหม่"):
            new_name = st.text_input("ชื่อรถ", key="new_v_name")
            new_plate = st.text_input("ทะเบียน", key="new_v_plate")
            new_mileage = st.number_input("เลขไมล์เริ่มต้น", 0, step=100, key="new_v_mileage")
            if st.button("บันทึกรถใหม่"):
                vid = str(uuid.uuid4())
                new_row = {"ID": vid, "Name": new_name, "Plate": new_plate, "CurrentMileage": new_mileage}
                df_vehicles = pd.concat([df_vehicles, pd.DataFrame([new_row])], ignore_index=True)
                save_sheet(sh, "Vehicles", df_vehicles)
                st.success("เพิ่มรถเรียบร้อย")
                load_data.clear()
                st.rerun()

        if df_vehicles.empty:
            st.info("ยังไม่มีรถในระบบ")
        else:
            vehicle_names = df_vehicles['Name'].tolist()
            sel_v_name = st.selectbox("เลือกรถ", vehicle_names)
            v_row = df_vehicles[df_vehicles['Name'] == sel_v_name].iloc[0]
            vid = v_row['ID']

            c1, c2 = st.columns(2)
            with c1:
                st.write(f"**ทะเบียน:** {v_row['Plate']}")
                st.write(f"**เลขไมล์ปัจจุบัน:** {int(v_row['CurrentMileage']):,} กม.")
                new_mileage_val = st.number_input("อัปเดตเลขไมล์", value=int(v_row['CurrentMileage']), step=100, key=f"upd_mileage_{vid}")
                updater = st.text_input("ผู้บันทึก (ชื่อ)", key=f"upd_by_{vid}")
                if st.button("💾 บันทึกเลขไมล์", key=f"btn_upd_mileage_{vid}"):
                    if not updater:
                        st.error("กรุณาระบุชื่อผู้บันทึก")
                    else:
                        old_mileage = v_row['CurrentMileage']
                        df_vehicles.loc[df_vehicles['ID'] == vid, 'CurrentMileage'] = new_mileage_val
                        save_sheet(sh, "Vehicles", df_vehicles)
                        log_row = {"ID": str(uuid.uuid4()), "VehicleID": vid, "OldMileage": old_mileage,
                                   "NewMileage": new_mileage_val, "UpdatedBy": updater,
                                   "CreatedAt": get_thai_time().strftime('%Y-%m-%d %H:%M:%S')}
                        df_mlogs = pd.concat([df_mlogs, pd.DataFrame([log_row])], ignore_index=True)
                        save_sheet(sh, "MileageLogs", df_mlogs)
                        st.success("อัปเดตเลขไมล์เรียบร้อย")
                        load_data.clear()
                        st.rerun()

            with c2:
                st.write("")
                st.write("")
                if st.button("🗑️ ลบรถคันนี้", key=f"del_v_{vid}"):
                    df_vehicles = df_vehicles[df_vehicles['ID'] != vid]
                    df_mitems = df_mitems[df_mitems['VehicleID'] != vid]
                    save_sheet(sh, "Vehicles", df_vehicles)
                    save_sheet(sh, "MaintItems", df_mitems)
                    st.success("ลบรถเรียบร้อย")
                    load_data.clear()
                    st.rerun()

            st.divider()
            st.write("### 🔩 รายการบำรุงรักษา")
            v_items = df_mitems[df_mitems['VehicleID'] == vid].copy()
            if not v_items.empty:
                v_items['สถานะ'] = v_items.apply(lambda r: status_label(compute_item_percent(r, v_row['CurrentMileage'])), axis=1)
                show_items = v_items.copy()
                show_items['LastDate'] = show_items['LastDate'].dt.strftime('%d/%m/%Y')
                st.dataframe(show_items[['Name', 'LastMileage', 'IntervalKm', 'LastDate', 'IntervalMonths', 'สถานะ']], use_container_width=True)

            with st.expander("➕ เพิ่ม / แก้ไขรายการบำรุงรักษา"):
                item_names = ["-- เพิ่มใหม่ --"] + v_items['Name'].tolist()
                sel_item = st.selectbox("เลือกรายการ", item_names, key=f"sel_item_{vid}")
                if sel_item == "-- เพิ่มใหม่ --":
                    it_name = st.text_input("ชื่อรายการ (เช่น เปลี่ยนน้ำมันเครื่อง)", key=f"it_name_{vid}")
                    it_last_km = st.number_input("เลขไมล์ล่าสุดที่ทำ", 0, step=100, key=f"it_km_{vid}")
                    it_interval_km = st.number_input("ระยะ (กม.)", 0, step=500, key=f"it_ikm_{vid}")
                    it_last_date = st.date_input("วันที่ทำล่าสุด", value=date.today(), key=f"it_date_{vid}")
                    it_interval_months = st.number_input("ระยะ (เดือน)", 0, step=1, key=f"it_imo_{vid}")
                    if st.button("บันทึกรายการใหม่", key=f"btn_new_item_{vid}"):
                        new_item = {"ID": str(uuid.uuid4()), "VehicleID": vid, "Name": it_name,
                                    "LastMileage": it_last_km, "IntervalKm": it_interval_km,
                                    "LastDate": it_last_date.strftime('%Y-%m-%d'), "IntervalMonths": it_interval_months}
                        df_mitems = pd.concat([df_mitems, pd.DataFrame([new_item])], ignore_index=True)
                        save_sheet(sh, "MaintItems", df_mitems)
                        st.success("เพิ่มรายการเรียบร้อย")
                        load_data.clear()
                        st.rerun()
                else:
                    it_row = v_items[v_items['Name'] == sel_item].iloc[0]
                    it_id = it_row['ID']
                    it_last_km = st.number_input("เลขไมล์ล่าสุดที่ทำ", value=int(it_row['LastMileage']), step=100, key=f"ed_km_{it_id}")
                    it_interval_km = st.number_input("ระยะ (กม.)", value=int(it_row['IntervalKm']), step=500, key=f"ed_ikm_{it_id}")
                    default_date = it_row['LastDate'].date() if pd.notna(it_row['LastDate']) else date.today()
                    it_last_date = st.date_input("วันที่ทำล่าสุด", value=default_date, key=f"ed_date_{it_id}")
                    it_interval_months = st.number_input("ระยะ (เดือน)", value=int(it_row['IntervalMonths']), step=1, key=f"ed_imo_{it_id}")
                    cbtn1, cbtn2 = st.columns(2)
                    if cbtn1.button("💾 บันทึกแก้ไข", key=f"btn_ed_item_{it_id}"):
                        df_mitems.loc[df_mitems['ID'] == it_id, 'LastMileage'] = it_last_km
                        df_mitems.loc[df_mitems['ID'] == it_id, 'IntervalKm'] = it_interval_km
                        df_mitems.loc[df_mitems['ID'] == it_id, 'LastDate'] = pd.to_datetime(it_last_date)
                        df_mitems.loc[df_mitems['ID'] == it_id, 'IntervalMonths'] = it_interval_months
                        save_sheet(sh, "MaintItems", df_mitems)
                        st.success("แก้ไขเรียบร้อย")
                        load_data.clear()
                        st.rerun()
                    if cbtn2.button("🗑️ ลบรายการนี้", key=f"btn_del_item_{it_id}"):
                        df_mitems = df_mitems[df_mitems['ID'] != it_id]
                        save_sheet(sh, "MaintItems", df_mitems)
                        st.success("ลบเรียบร้อย")
                        load_data.clear()
                        st.rerun()

    # --- TAB 3: DEVICES ---
    with tab3:
        st.subheader("จัดการอุปกรณ์แบต")
        with st.expander("➕ เพิ่มอุปกรณ์ใหม่"):
            d_name = st.text_input("ชื่ออุปกรณ์", key="new_d_name")
            d_interval = st.number_input("รอบชาร์จ (วัน)", 0, step=1, key="new_d_interval")
            d_notes = st.text_input("หมายเหตุ", key="new_d_notes")
            d_serial = st.text_input("Serial Number", key="new_d_serial")
            if st.button("บันทึกอุปกรณ์ใหม่"):
                new_dev = {"ID": str(uuid.uuid4()), "Name": d_name, "IntervalDays": d_interval,
                           "LastChargedDate": "", "Notes": d_notes, "SerialNumber": d_serial}
                df_devices = pd.concat([df_devices, pd.DataFrame([new_dev])], ignore_index=True)
                save_sheet(sh, "Devices", df_devices)
                st.success("เพิ่มอุปกรณ์เรียบร้อย")
                load_data.clear()
                st.rerun()

        if df_devices.empty:
            st.info("ยังไม่มีอุปกรณ์ในระบบ")
        else:
            for _, d in df_devices.iterrows():
                pct = compute_device_percent(d)
                did = d['ID']
                with st.container(border=True):
                    c1, c2, c3 = st.columns([2, 2, 1])
                    with c1:
                        st.write(f"**{d['Name']}**  {status_label(pct)}")
                        last_charged = d['LastChargedDate'].strftime('%d/%m/%Y') if pd.notna(d['LastChargedDate']) else "ไม่เคยชาร์จ"
                        st.caption(f"ชาร์จล่าสุด: {last_charged} | รอบ: {int(d['IntervalDays'])} วัน | SN: {d['SerialNumber'] or '-'}")
                    with c2:
                        charger_name = st.text_input("ผู้ชาร์จ", key=f"charger_{did}", label_visibility="collapsed", placeholder="ชื่อผู้ชาร์จ")
                    with c3:
                        if st.button("🔋 ชาร์จแล้ว", key=f"btn_charge_{did}"):
                            if not charger_name:
                                st.error("กรุณาระบุชื่อผู้ชาร์จ")
                            else:
                                old_date = d['LastChargedDate'].strftime('%Y-%m-%d') if pd.notna(d['LastChargedDate']) else ""
                                new_date_str = date.today().strftime('%Y-%m-%d')
                                df_devices.loc[df_devices['ID'] == did, 'LastChargedDate'] = pd.to_datetime(new_date_str)
                                save_sheet(sh, "Devices", df_devices)
                                log_row = {"ID": str(uuid.uuid4()), "DeviceID": did, "OldDate": old_date,
                                           "NewDate": new_date_str, "UpdatedBy": charger_name,
                                           "CreatedAt": get_thai_time().strftime('%Y-%m-%d %H:%M:%S')}
                                df_dlogs = pd.concat([df_dlogs, pd.DataFrame([log_row])], ignore_index=True)
                                save_sheet(sh, "DeviceChargeLogs", df_dlogs)
                                st.success("บันทึกการชาร์จเรียบร้อย")
                                load_data.clear()
                                st.rerun()
                        if st.button("🗑️ ลบ", key=f"btn_del_dev_{did}"):
                            df_devices = df_devices[df_devices['ID'] != did]
                            save_sheet(sh, "Devices", df_devices)
                            load_data.clear()
                            st.rerun()

    # --- TAB 4: HISTORY ---
    with tab4:
        st.subheader("ประวัติเลขไมล์")
        if not df_mlogs.empty and not df_vehicles.empty:
            show = df_mlogs.merge(df_vehicles[['ID', 'Name']], left_on='VehicleID', right_on='ID', suffixes=('', '_v'))
            show = show.sort_values('CreatedAt', ascending=False)
            st.dataframe(show[['Name', 'OldMileage', 'NewMileage', 'UpdatedBy', 'CreatedAt']], use_container_width=True)
        else:
            st.info("ยังไม่มีประวัติ")

        st.subheader("ประวัติการชาร์จ")
        if not df_dlogs.empty and not df_devices.empty:
            show2 = df_dlogs.merge(df_devices[['ID', 'Name']], left_on='DeviceID', right_on='ID', suffixes=('', '_d'))
            show2 = show2.sort_values('CreatedAt', ascending=False)
            st.dataframe(show2[['Name', 'OldDate', 'NewDate', 'UpdatedBy', 'CreatedAt']], use_container_width=True)
        else:
            st.info("ยังไม่มีประวัติ")

# --- MAIN ---
try:
    sh = get_spreadsheet()
    data = load_data()
    with st.sidebar:
        st.header("NavGo Menu")
        page = st.radio("ไปที่หน้า:", ["🚗 จองรถ & อุปกรณ์", "🛠️ Admin & Stock", "🔧 เช็คระยะรถ & แบต"])
        st.write("---")
        st.caption(f"Time: {get_thai_time().strftime('%H:%M')}")

    if page == "🚗 จองรถ & อุปกรณ์":
        page_car_booking(data, sh)
    elif page == "🛠️ Admin & Stock":
        page_admin(data["book"], data["stock"], data["users"], sh)
    else:
        page_car_maintenance(data, sh)

except Exception as e:
    st.error(f"Error: {e}")
