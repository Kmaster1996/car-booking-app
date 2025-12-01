# --- PAGE: CAR BOOKING ---
def page_car_booking(df_book, df_stock, sh):
    st.title("🚗 NavGo: จองรถและอุปกรณ์")
    
    # 1. Initialize Session State (เหมือนเดิม: เพื่อให้เวลาไม่ดิ้น)
    if 'booking_s_time' not in st.session_state:
        now = datetime.now()
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
        # --- ดึงค่าเวลาปัจจุบันจาก Session State มาคำนวณ Stock ก่อนวาดหน้าจอ ---
        # ต้องทำตรงนี้ก่อน เพราะเราจะเอาเวลาไปเช็คของใน Stock ทันที
        curr_s_date = st.session_state.booking_s_date
        curr_s_time = st.session_state.booking_s_time
        curr_e_date = st.session_state.booking_e_date
        curr_e_time = st.session_state.booking_e_time
        
        # สร้างตัวแปร datetime สำหรับเช็ค Stock
        check_start_dt = datetime.combine(curr_s_date, curr_s_time)
        check_end_dt = datetime.combine(curr_e_date, curr_e_time)

        # หา Booking ที่ทับซ้อนกับช่วงเวลาที่เลือกอยู่ ณ ตอนนี้
        overlap_bookings_now = df_book[
            (df_book['Start_Time'] < check_end_dt) & 
            (df_book['End_Time'] > check_start_dt)
        ]

        # --- เริ่มวาดหน้าจอ ---
        c1, c2 = st.columns([1, 1])
        
        with c1:
            st.subheader("1. รายละเอียด")
            user = st.text_input("ชื่อผู้จอง")
            task = st.text_input("ภารกิจ")
            loc = st.text_input("สถานที่")
            ppl = st.number_input("จำนวนคน", 1, 10, 2)
            
            st.divider()
            st.subheader(f"เลือกอุปกรณ์ (เช็คยอด ณ {curr_s_time.strftime('%H:%M')})")
            
            selected_equip = {}
            if not df_stock.empty:
                for _, row in df_stock.iterrows():
                    item_name = row['ItemName']
                    total = int(row['TotalQty'])
                    
                    # --- คำนวณยอดคงเหลือ Real-time ---
                    used_count = 0
                    for _, b_row in overlap_bookings_now.iterrows():
                        b_items = parse_equip_str(b_row['Equipment'])
                        used_count += b_items.get(item_name, 0)
                    
                    available = total - used_count
                    if available < 0: available = 0 # กันติดลบ (กรณีข้อมูลเก่าผิดพลาด)

                    # แสดงผล
                    cc1, cc2 = st.columns([3, 1])
                    
                    # เปลี่ยนสีข้อความตามจำนวนของที่เหลือ
                    if available == 0:
                        cc1.markdown(f"🔴 **{item_name}** (หมดเกลี้ยง!)")
                        max_val = 0
                    elif available < total:
                        cc1.markdown(f"🟠 {item_name} (เหลือ **{available}**/{total})")
                        max_val = available
                    else:
                        cc1.markdown(f"🟢 {item_name} (เหลือ **{available}**/{total})")
                        max_val = available

                    # ช่องกรอกจำนวน (Limit ให้ไม่เกินของที่มี)
                    qty = cc2.number_input(
                        "จำนวน", 
                        key=f"q_{item_name}", 
                        min_value=0, 
                        max_value=max_val, # บังคับไม่ให้กรอกเกินที่มี
                        value=0, 
                        label_visibility="collapsed",
                        disabled=(max_val==0) # ถ้าของหมด ปิดช่องกรอกไปเลย
                    )
                    
                    if qty > 0: selected_equip[item_name] = qty
            else:
                st.warning("กรุณาเพิ่มข้อมูลในเมนู Inventory")

        with c2:
            st.subheader("2. เลือกวันเวลา")
            # หมายเหตุ: การเปลี่ยนเวลาตรงนี้ จะทำให้หน้าจอรันใหม่ และยอดคงเหลือฝั่งซ้ายจะเปลี่ยนตามทันที
            d1, t1 = st.columns(2)
            s_date = d1.date_input("เริ่ม", key='booking_s_date')
            s_time = t1.time_input("เวลาเริ่ม", key='booking_s_time')
            
            d2, t2 = st.columns(2)
            e_date = d2.date_input("คืน", key='booking_e_date')
            e_time = t2.time_input("เวลาคืน", key='booking_e_time')
            
            # (validation logic เดิม)
            start_dt = datetime.combine(s_date, s_time)
            end_dt = datetime.combine(e_date, e_time)
            
            # ... (Logic แนะนำรถ เหมือนเดิม) ...
            valid_cars = []
            total_load = 0
            equip_str_list = []
            for k, v in selected_equip.items():
                vol = df_stock[df_stock['ItemName'] == k]['VolumeScore'].values[0]
                total_load += (vol * v)
                equip_str_list.append(f"{k} x{v}")
            equip_final_str = ", ".join(equip_str_list) if equip_str_list else "-"

            for c_name, specs in CAR_SPECS.items():
                if specs['max_seats'] >= ppl:
                    cargo_limit = specs['cargo_score'] if "D-max" in c_name else (specs['cargo_score'] - (ppl*20))
                    if total_load <= cargo_limit:
                        valid_cars.append(c_name)

            st.divider()
            st.subheader("3. เลือกรถ")
            if not valid_cars:
                st.warning("⚠️ ของเยอะหรือคนเยอะเกิน รถรับไม่ไหว")
            else:
                st.success(f"✅ รถที่แนะนำ: {len(valid_cars)} คัน")
            
            sel_car = st.selectbox("เลือกรถ", valid_cars if valid_cars else list(CAR_SPECS.keys()))

            # ปุ่มยืนยัน
            if st.button("🚀 ยืนยันการจอง", type="primary"):
                if start_dt >= end_dt:
                    st.error("เวลาคืนต้องหลังเวลาเริ่ม")
                elif not user:
                    st.error("กรุณาใส่ชื่อผู้จอง")
                else:
                    new_row = {
                        "User": user, "Task": task, "Car": sel_car,
                        "People": ppl, "Equipment": equip_final_str,
                        "Location": loc, "Start_Time": start_dt, "End_Time": end_dt
                    }
                    df_book = pd.concat([df_book, pd.DataFrame([new_row])], ignore_index=True)
                    save_booking(sh, df_book)
                    st.success("จองสำเร็จ!")
                    for key in ['booking_s_time', 'booking_e_time', 'booking_s_date', 'booking_e_date']:
                        del st.session_state[key]
                    st.rerun()

    with tab2:
        # (ส่วนตารางแสดงผล เหมือนเดิม ไม่ต้องแก้)
        st.subheader("ตารางการจอง")
        if not df_book.empty:
            show_df = df_book.sort_values("Start_Time", ascending=False).copy()
            show_df['Start_Time'] = show_df['Start_Time'].dt.strftime('%d/%m %H:%M')
            show_df['End_Time'] = show_df['End_Time'].dt.strftime('%d/%m %H:%M')
            st.dataframe(show_df[['User','Car','Equipment','Start_Time','End_Time']], use_container_width=True)
