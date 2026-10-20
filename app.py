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
st.caption("เปรียบเทียบไฟล์ Quotation (File A) กับ PO (File B) แบบละเอียดทุกหน้า พร้อมขีดฆ่าและแปะคำที่ถูกต้องลงไฟล์ PDF จริง")

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

def draw_red_corrections_permanently(page, inst, correct_text, index):
    y_center = (inst.y0 + inst.y1) / 2
    
    # 1. วาดเส้นขีดฆ่าสีแดงทับคำผิด
    page.draw_line(
        fitz.Point(inst.x0, y_center),
        fitz.Point(inst.x1, y_center),
        color=(1, 0, 0),
        width=1.8
    )
    
    # 2. คำนวณตำแหน่งกล่องข้อความ
    box_x0 = inst.x1 + 15
    box_y0 = inst.y1 + (index * 22)
    box_w = max(len(correct_text) * 7.5, 130)
    box_h = 18
    
    rect = fitz.Rect(box_x0, box_y0, box_x0 + box_w, box_y0 + box_h)
    
    # 3. วาดเส้นชี้สีแดง
    page.draw_line(
        fitz.Point(inst.x1, y_center),
        fitz.Point(box_x0, box_y0 + (box_h / 2)),
        color=(1, 0, 0),
        width=1.0
    )
    
    # 4. วาดกล่องข้อความสีแดง
    page.draw_rect(
        rect,
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

def annotate_pdf_corrections(input_pdf_bytes, corrections):
    doc = fitz.open(stream=input_pdf_bytes, filetype="pdf")
    applied_logs = []
    
    for page_num, page in enumerate(doc, start=1):
        box_idx = 0
        for item in corrections:
            wrong_text = str(item.get("wrong", "")).strip()
            correct_text = str(item.get("correct", "")).strip()
            
            if not wrong_text or len(wrong_text) < 2:
                continue
                
            # ค้นหาข้อความแบบยืดหยุ่น (Exact Match ก่อน)
            text_instances = page.search_for(wrong_text)
            
            # ถ้ายูสเซอร์หรือ AI ส่งข้อความยาว ให้แยก Token ค้นหาเฉพาะรหัส/ตัวเลขสำคัญ
            if not text_instances:
                tokens = re.findall(r'[A-Za-z0-9\-]+', wrong_text)
                for token in tokens:
                    if len(token) >= 4:  # เช่น รหัสสินค้าที่มีความยาวพอสมควร
                        text_instances = page.search_for(token)
                        if text_instances:
                            break
            
            for inst in text_instances:
                draw_red_corrections_permanently(page, inst, correct_text, box_idx)
                applied_logs.append({
                    "page": page_num,
                    "wrong": wrong_text,
                    "correct": correct_text
                })
                box_idx += 1

    output_pdf_bytes = doc.write(clean=True, deflate=True)
    doc.close()
    return output_pdf_bytes, applied_logs

def extract_corrections_robust(text):
    """
    ระบบดึงรายการแก้ไขแบบ 2 ชั้น (JSON + Markdown Table Fallback)
    """
    corrections = []
    
    # ชั้นที่ 1: ดึงจาก Block JSON
    try:
        match = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL)
        if match:
            corrections = json.loads(match.group(1))
            if corrections:
                return corrections
        match_brackets = re.search(r'\[\s*\{.*?\}\s*\]', text, re.DOTALL)
        if match_brackets:
            corrections = json.loads(match_brackets.group(0))
            if corrections:
                return corrections
    except:
        pass
        
    # ชั้นที่ 2: Fallback แกะจากตาราง Markdown อัตโนมัติทันที
    lines = text.split('\n')
    for line in lines:
        if '|' in line and '---' not in line:
            cols = [c.strip() for c in line.split('|') if c.strip()]
            if len(cols) >= 4:
                wrong_candidate = cols[2]  # คอลัมน์รายละเอียดไฟล์ B
                correct_candidate = cols[1] # คอลัมน์รายละเอียดไฟล์ A
                if wrong_candidate and correct_candidate and "รายละเอียด" not in wrong_candidate:
                    corrections.append({
                        "wrong": wrong_candidate[:30],
                        "correct": correct_candidate[:30]
                    })
                    
    return corrections

st.divider()

if st.button("🚀 เริ่มตรวจสอบและแก้ไขไฟล์ PDF", type="primary", use_container_width=True):
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
            
            status_box.write("🤖 AI กำลังสแกนเปรียบเทียบทุกหน้าในเอกสารอย่างละเอียด...")
            
            prompt = """
            จงตรวจสอบและเปรียบเทียบเอกสาร B (ไฟล์ตรวจสอบ PO) กับ เอกสาร A (ไฟล์ตั้งต้น Quotation) อย่างละเอียดทุกหน้า โดยเจาะจงตรวจสอบ:
            1. Description (รายละเอียดสินค้า/รหัสรุ่น): ต้องตรงกันแบบคำต่อคำ
            2. จำนวนสินค้า (Quantity / Qty): ตัวเลขจำนวนต้องตรงกัน
            3. ราคา (Unit Price / Total Price): ตัวเลขราคาต้องตรงกัน

            รูปแบบรายงานสรุป:
            ### ตารางเปรียบเทียบและผลการตรวจสอบ
            ให้แสดงผลเป็นตาราง Markdown 4 คอลัมน์หลัก:
            1. รายการ / รหัสสินค้า (ไฟล์ B) (ระบุหน้า และ Item No.)
            2. รายละเอียดใน เอกสาร A (ไฟล์ตั้งต้น)
            3. รายละเอียดใน เอกสาร B (ไฟล์ตรวจสอบ)
            4. ผลการตรวจสอบ / สิ่งที่ต้องแก้ไข

            ### สรุปภาพรวม
            สรุป 3 ด้านหลัก (ราคา, จำนวน, รายละเอียด)

            ที่บรรทัดท้ายสุด ส่ง JSON สรุปรายการคำผิดในไฟล์ B:
            ```json
            [
              {"wrong": "รหัสหรือคำผิดในไฟล์ B", "correct": "ข้อความที่ถูกต้องจากไฟล์ A"}
            ]
            ```
            ห้ามใช้แท็ก HTML เช่น <br>
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
            
            status_box.write("⚙️ กำลังดึงรายการและวาดเส้นแก้ไขลงบนไฟล์ PDF...")
            
            corrections_list = extract_corrections_robust(response.text)
            
            file_b.seek(0)
            pdf_bytes = file_b.read()
            annotated_pdf_bytes, applied_logs = annotate_pdf_corrections(pdf_bytes, corrections_list)
            
            status_box.update(label=f"✅ ตรวจสอบและแก้ไขเรียบร้อย! (วาดลง PDF ไปทั้งหมด {len(applied_logs)} จุด)", state="complete", expanded=False)
            
            st.markdown(clean_output)
            
            # เปิดให้ดูรายการพิกัดที่ระบบนำไปแก้ไข
            with st.expander("🔍 ตรวจสอบรายการพิกัดที่ระบบนำไปแก้ไขบน PDF"):
                st.write("**รายการ Corrections ที่แกะได้:**", corrections_list)
                st.write("**รายการที่ค้นหาพบและวาดลง PDF สำเร็จ (Logs):**", applied_logs)
            
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
                    label=f"📥 ดาวน์โหลดไฟล์ PDF ที่แก้ไขแล้ว ({len(applied_logs)} จุด) (.pdf)",
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
