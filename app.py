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
            
# --- PAGE: ADMIN & INVENTORY ---
def page_admin(df_book, df_stock, df_users, sh):
    st.title("🛠️ Admin Dashboard")
    now = get_thai_time()
    
    # ------------------------------------------------
    # 1. DAILY REMINDER (เพิ่มใหม่!)
    # ------------------------------------------------
    st.write("### 🔔 แจ้งเตือนการคืนของ (Manual Trigger)")
    with st.expander("กดเพื่อตรวจสอบและแจ้งเตือนเข้ากลุ่ม Telegram"):
        st.write("ระบบจะค้นหารายการที่ **ครบกำหนดคืนวันนี้** หรือ **เกินกำหนด** แล้วส่งสรุปเข้ากลุ่ม")
        if st.button("📢 ส่งแจ้งเตือนรายการคืนวันนี้", type="primary"):
            if df_book.empty:
                st.warning("ไม่มีข้อมูลการจอง")
            else:
                # หาคนที่ต้องคืนวันนี้ (หรือเกินกำหนดแล้วยังไม่คืน - ในที่นี้ดูแค่เวลาจบ)
                # Logic: End_Time คือ "วันนี้" (เทียบแค่วันที่)
                today_str = now.strftime('%Y-%m-%d')
                
                # กรองรายการที่ 'วันคืน' ตรงกับ 'วันนี้'
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
                            f"🚗 {row['Car']}\n"
                            f"📦 {row['Equipment']}\n"
                            f"🔴 คืนเวลา: {row['End_Time'].strftime('%H:%M')}\n\n"
                        )
                    
                    full_msg = msg_header + msg_body + "<i>รบกวนตรวจสอบและคืนของให้ตรงเวลาครับ</i>"
                    send_telegram_notify(full_msg)
                    st.success(f"ส่งแจ้งเตือน {count} รายการเรียบร้อย!")

    st.divider()

    # ------------------------------------------------
    # 2. MONITOR (เหมือนเดิม)
    # ------------------------------------------------
    st.write("### 🕵️‍♂️ Monitor (Real-time)")
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
    # 3. STOCK & USER (เหมือนเดิม)
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
    
    with st.expander("📝 แก้ไข Stock"):
        ed_stock = st.data_editor(df_stock, num_rows="dynamic", use_container_width=True, key="admin_stock")
        if st.button("💾 บันทึก Stock"):
            save_stock(sh, ed_stock)
            st.rerun()

    st.divider()
    st.write("### 👥 รายชื่อพนักงาน")
    with st.expander("แก้ไขรายชื่อ"):
        ed_users = st.data_editor(df_users, num_rows="dynamic", use_container_width=True, key="admin_users")
        if st.button("บันทึกรายชื่อ"):
            save_users(sh, ed_users)
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
