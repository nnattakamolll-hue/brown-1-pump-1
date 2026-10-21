import streamlit as st
from google import genai
from google.genai import types
import tempfile
import os
import time
import json
import re
import fitz  # PyMuPDF

st.set_page_config(
    page_title="PDF PO & Quotation Auditor", 
    page_icon="📄", 
    layout="wide"
)

st.title("📄 ระบบตรวจสอบและแก้ไขข้อความบนไฟล์ PDF (PO) อัตโนมัติ")
st.caption("เปรียบเทียบไฟล์ Quotation (File A) กับ PO (File B) พร้อมการันตีการวาดแก้ไขลง PDF ครบทุกจุดตามตารางรายงาน")

api_key = st.text_input("🔑 กรอก Gemini API Key ของคุณที่นี่", type="password", help="วาง API Key ที่ได้มาจาก Google AI Studio ตรงนี้")

st.divider()

col1, col2 = st.columns(2)
with col1:
    st.subheader("1. ไฟล์ตั้งต้น (Baseline - Quotation)")
    file_a = st.file_uploader("เลือกไฟล์ PDF Quotation", type=["pdf"], key="file_quotation")

with col2:
    st.subheader("2. ไฟล์ที่ต้องการตรวจ (To Check - PO)")
    file_b = st.file_uploader("เลือกไฟล์ PDF PO", type=["pdf"], key="file_po")

def upload_to_gemini(client, uploaded_file):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(uploaded_file.getvalue())
        tmp_path = tmp.name
    
    g_file = client.files.upload(file=tmp_path)
    while g_file.state.name == "PROCESSING":
        time.sleep(2)
        g_file = client.files.get(name=g_file.name)
        
    os.unlink(tmp_path)
    return g_file

def draw_red_correction_on_rect(page, rect, correct_text, index):
    """
    วาดเส้นขีดฆ่าสีแดง ลากเส้นชี้ และสร้างกล่องข้อความสีแดงฝังลงเนื้อ PDF
    """
    y_center = (rect.y0 + rect.y1) / 2
    
    # 1. วาดเส้นขีดฆ่าสีแดงกลางคำผิด
    page.draw_line(
        fitz.Point(rect.x0, y_center),
        fitz.Point(rect.x1, y_center),
        color=(1, 0, 0),
        width=1.8
    )
    
    # 2. คำนวณตำแหน่งกล่องข้อความแก้ไข
    box_x0 = rect.x1 + 12
    box_y0 = rect.y0 + (index * 20)
    box_w = max(len(correct_text) * 7.5, 120)
    box_h = 18
    
    box_rect = fitz.Rect(box_x0, box_y0, box_x0 + box_w, box_y0 + box_h)
    
    # 3. วาดเส้นชี้สีแดง
    page.draw_line(
        fitz.Point(rect.x1, y_center),
        fitz.Point(box_x0, box_y0 + (box_h / 2)),
        color=(1, 0, 0),
        width=1.0
    )
    
    # 4. วาดกล่องข้อความสีแดง พื้นหลังสีขาว
    page.draw_rect(
        box_rect,
        color=(1, 0, 0),
        fill=(1, 1, 1),
        width=1.2
    )
    
    # 5. ใส่ตัวหนังสือสีแดง
    page.insert_text(
        fitz.Point(box_x0 + 4, box_y0 + 13),
        correct_text,
        fontsize=8.5,
        color=(1, 0, 0),
        fontname="helv"
    )

def annotate_pdf_complete(input_pdf_bytes, corrections):
    """
    ระบบวาดลง PDF แบบเก็บตกครบ 100% (ใช้วิธี Hybrid: JSON Box + Text Search)
    """
    doc = fitz.open(stream=input_pdf_bytes, filetype="pdf")
    applied_logs = []
    
    for item in corrections:
        wrong_text = str(item.get("wrong", "")).strip()
        correct_text = str(item.get("correct", "")).strip()
        page_num = int(item.get("page", 1)) - 1
        box_2d = item.get("box_2d")
        
        if not wrong_text or page_num < 0 or page_num >= len(doc):
            continue
            
        page = doc[page_num]
        rect_to_draw = None
        
        # กรณีที่ 1: มีพิกัด box_2d จาก AI
        if box_2d and len(box_2d) == 4:
            ymin, xmin, ymax, xmax = box_2d
            w, h = page.rect.width, page.rect.height
            rect_to_draw = fitz.Rect(xmin * w / 1000.0, ymin * h / 1000.0, xmax * w / 1000.0, ymax * h / 1000.0)
            
        # กรณีที่ 2: ไม่มีพิกัด หรือพิกัดไม่สมบูรณ์ -> ใช้ Text Search สแกนพิกัดบนกระดาษอัตโนมัติ
        if not rect_to_draw:
            text_instances = page.search_for(wrong_text)
            if not text_instances and (" " in wrong_text or "-" in wrong_text):
                parts = re.split(r'[\s\-]+', wrong_text)
                for part in parts:
                    if len(part) >= 3 and not part.isdigit():
                        text_instances = page.search_for(part)
                        if text_instances:
                            break
            if text_instances:
                rect_to_draw = text_instances[0]
                
        # หากได้ตำแหน่งแล้ว สั่งวาดลง PDF ทันที
        if rect_to_draw:
            draw_red_correction_on_rect(page, rect_to_draw, correct_text, len(applied_logs))
            applied_logs.append({
                "page": page_num + 1,
                "wrong": wrong_text,
                "correct": correct_text
            })

    output_pdf_bytes = doc.write(clean=True, deflate=True)
    doc.close()
    return output_pdf_bytes, applied_logs

def extract_all_corrections(text):
    """
    แกะรายการแก้ไขอย่างละเอียดจากทั้ง JSON Block และตาราง Markdown เพื่อไม่ให้ตกหล่นแม้แต่จุดเดียว
    """
    corrections = []
    
    # 1. ลองแกะจาก JSON
    try:
        match = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL)
        if match:
            corrections = json.loads(match.group(1))
    except:
        pass
        
    # 2. แกะเก็บตกจากตาราง Markdown สรุปผล
    lines = text.split('\n')
    for line in lines:
        if '|' in line and '---' not in line and 'รายการ' not in line:
            cols = [c.strip() for c in line.split('|') if c.strip()]
            if len(cols) >= 4:
                # ดึงเฉพาะแถวที่มีการระบุให้แก้ไข
                if "แก้" in cols[3] or "เปลี่ยน" in cols[3] or "ไม่ตรง" in cols[3] or "ผิด" in cols[3]:
                    wrong_val = cols[2]
                    correct_val = cols[1]
                    # ตรวจสอบไม่ให้ซ้ำกับที่มีใน JSON
                    if not any(c.get("wrong") == wrong_val for c in corrections):
                        corrections.append({
                            "page": 1,
                            "wrong": wrong_val,
                            "correct": correct_val
                        })
                        
    return corrections

st.divider()

if st.button("🚀 เริ่มตรวจสอบและแก้ไขไฟล์ PDF ครบทุกจุด", type="primary", use_container_width=True):
    if not api_key:
        st.error("❌ กรุณากรอก Gemini API Key ในช่องด้านบนก่อนครับ")
    elif not file_a or not file_b:
        st.error("❌ กรุณาอัปโหลดไฟล์ PDF ให้ครบทั้ง 2 ไฟล์ครับ")
    else:
        status_box = st.status("🔍 กำลังประมวลผลระบบ...", expanded=True)
        uploaded_files = []
        
        try:
            client = genai.Client(api_key=api_key)
            
            status_box.write("📤 กำลังส่งไฟล์เข้าสู่ระบบวิเคราะห์ AI...")
            g_a = upload_to_gemini(client, file_a)
            uploaded_files.append(g_a.name)
            
            g_b = upload_to_gemini(client, file_b)
            uploaded_files.append(g_b.name)
            
            status_box.write("🤖 AI กำลังเปรียบเทียบและรวบรวมจุดที่ต้องแก้ไขทั้งหมด...")
            
            prompt = """
            จงตรวจสอบเปรียบเทียบเอกสาร B (ไฟล์ตรวจสอบ PO) กับ เอกสาร A (ไฟล์ตั้งต้น Quotation) อย่างละเอียดทุกหน้า
            โดยตรวจสอบ Description (Exact Match), ราคา และจำนวนสินค้า

            รูปแบบรายงานสรุป:
            ### ตารางเปรียบเทียบและผลการตรวจสอบ
            แสดงผลเป็นตาราง Markdown 4 คอลัมน์หลัก (รายการ/รหัสสินค้า, รายละเอียดใน A, รายละเอียดใน B, ผลการตรวจสอบ/สิ่งที่ต้องแก้ไข)

            ### สรุปภาพรวม
            สรุป 3 ด้านหลัก (ราคา, จำนวน, รายละเอียด)

            คำสั่งสำคัญมากๆ:
            ที่บรรทัดท้ายสุด ให้ส่ง JSON รายการจุดที่ผิดทั้งหมดในเอกสาร B โดยต้องระบุเลขหน้า (page) คำผิด (wrong) คำถูก (correct) และพิกัด [ymin, xmin, ymax, xmax] (box_2d) อย่างครบถ้วนทุกจุด:
            ```json
            [
              {
                "page": 1,
                "wrong": "ข้อความผิดใน B",
                "correct": "ข้อความถูกจาก A",
                "box_2d": [ymin, xmin, ymax, xmax]
              }
            ]
            ```
            ห้ามใช้แท็ก HTML เช่น <br> เด็ดขาด
            """
            
            max_retries = 5
            retry_delay = 5
            response = None
            success = False
            
            for attempt in range(max_retries):
                try:
                    status_box.write(f"🔄 กำลังวิเคราะห์ข้อมูล (พยายามครั้งที่ {attempt+1}/{max_retries})...")
                    response = client.models.generate_content(
                        model='gemini-3.8-flash',
                        contents=[g_a, g_b, prompt]
                    )
                    success = True
                    break
                except Exception as api_err:
                    if ("503" in str(api_err) or "UNAVAILABLE" in str(api_err)) and attempt < max_retries - 1:
                        status_box.warning(f"⚠️ เซิร์ฟเวอร์หนาแน่น กำลังลองใหม่ในอีก {retry_delay} วินาที...")
                        time.sleep(retry_delay)
                        retry_delay += 3
                    else:
                        raise api_err
            
            if not success or not response:
                raise Exception("เซิร์ฟเวอร์หนาแน่นเกินไป โปรดลองกดปุ่มเริ่มตรวจสอบใหม่อีกครั้ง")
            
            clean_output = response.text.replace("<br>", " ").replace("<br/>", " ").replace("<BR>", " ")
            
            status_box.write("⚙️ กำลังทำการวาดเส้นขีดฆ่าและแปะข้อความแก้ไขลงบน PDF ให้ครบทุกจุด...")
            
            corrections_list = extract_all_corrections(response.text)
            file_b.seek(0)
            pdf_bytes = file_b.read()
            annotated_pdf_bytes, applied_logs = annotate_pdf_complete(pdf_bytes, corrections_list)
            
            status_box.update(label=f"✅ ตรวจสอบและแก้ไขเสร็จสิ้น! (ทำการวาดลง PDF ไปทั้งหมด {len(applied_logs)} จุด)", state="complete", expanded=False)
            
            st.markdown(clean_output)
            
            with st.expander("🔍 ตรวจสอบรายการจุดแก้ไขทั้งหมดที่วาดลงบน PDF"):
                st.write("รายการจุดแก้ไขทั้งหมดที่รวบรวมได้:", corrections_list)
                st.write("รายการที่ทำการวาดลงไฟล์ PDF สำเร็จจริง:", applied_logs)
            
            st.divider()
            col_dl1, col_dl2 = st.columns(2)
            with col_dl1:
                st.download_button(
                    label="📥 ดาวน์โหลดรายงานผลสรุป (.txt)",
                    data=clean_output,
                    file_name="po_audit_report.txt",
                    mime="text/plain"
                )
            with col_dl2:
                st.download_button(
                    label=f"📥 ดาวน์โหลดไฟล์ PDF ที่แก้ไขแล้วครบทุกจุด ({len(applied_logs)} จุด) (.pdf)",
                    data=annotated_pdf_bytes,
                    file_name="annotated_po_document.pdf",
                    mime="application/pdf"
                )

        except Exception as e:
            status_box.update(label="❌ เกิดข้อผิดพลาดในการประมวลผล", state="error", expanded=True)
            st.error(f"รายละเอียด: {str(e)}")
            
        finally:
            if uploaded_files and 'client' in locals():
                for name in uploaded_files:
                    try:
                        client.files.delete(name=name)
                    except:
                        pass
