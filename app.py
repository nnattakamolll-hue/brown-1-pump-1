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
st.caption("เปรียบเทียบไฟล์ Quotation (File A) กับ PO (File B) แล้วฝังเส้นขีดฆ่าสีแดงพร้อมข้อความที่ถูกต้องลงในไฟล์ PDF จริง")

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
    """
    ฝังเส้นขีดฆ่าสีแดง และพิมพ์ข้อความแก้ไขสีแดงทับลงเนื้อ PDF ถาวร (Burn-in / Flatten)
    """
    # 1. วาดเส้นขีดฆ่าสีแดงกลางข้อความผิด
    y_center = (inst.y0 + inst.y1) / 2
    page.draw_line(
        fitz.Point(inst.x0, y_center),
        fitz.Point(inst.x1, y_center),
        color=(1, 0, 0),  # สีแดง
        width=1.5
    )
    
    # 2. คำนวณตำแหน่งกล่องข้อความแก้ไข
    box_x0 = inst.x1 + 15
    box_y0 = inst.y1 + (index * 20)
    box_w = max(len(correct_text) * 7, 120)
    box_h = 16
    
    rect = fitz.Rect(box_x0, box_y0, box_x0 + box_w, box_y0 + box_h)
    
    # 3. วาดเส้นชี้สีแดงจากคำผิดไปยังข้อความแก้
    page.draw_line(
        fitz.Point(inst.x1, y_center),
        fitz.Point(box_x0, box_y0 + (box_h / 2)),
        color=(1, 0, 0),
        width=1.0
    )
    
    # 4. วาดกล่องกรอบสีแดง พื้นสีขาว
    page.draw_rect(
        rect,
        color=(1, 0, 0),
        fill=(1, 1, 1),
        width=1.0
    )
    
    # 5. พิมพ์ตัวหนังสือสีแดงลงในเนื้อ PDF ถาวร
    page.insert_text(
        fitz.Point(box_x0 + 4, box_y0 + 12),
        correct_text,
        fontsize=8.5,
        color=(1, 0, 0),
        fontname="helv"
    )

def annotate_pdf_corrections(input_pdf_bytes, corrections):
    doc = fitz.open(stream=input_pdf_bytes, filetype="pdf")
    total_matches = 0
    
    for page in doc:
        box_idx = 0
        for item in corrections:
            wrong_text = str(item.get("wrong", "")).strip()
            correct_text = str(item.get("correct", "")).strip()
            
            if not wrong_text or len(wrong_text) < 2:
                continue
                
            # ค้นหาพิกัดคำผิดใน PDF
            text_instances = page.search_for(wrong_text)
            
            # ถ้าหาทั้งคำไม่เจอ ให้ลองหาตัดคำสั้นๆ
            if not text_instances and (" " in wrong_text or "-" in wrong_text):
                parts = re.split(r'[\s\-]+', wrong_text)
                for part in parts:
                    if len(part) >= 3:
                        text_instances = page.search_for(part)
                        if text_instances:
                            break
            
            # สั่งเขียนเส้นสีแดงฝังลงเนื้อ PDF จริงทันที
            for inst in text_instances:
                draw_red_corrections_permanently(page, inst, correct_text, box_idx)
                box_idx += 1
                total_matches += 1

    # บันทึกไฟล์ PDF แบบฝังเนื้อหาชัวร์ๆ
    output_pdf_bytes = doc.write(clean=True, deflate=True)
    doc.close()
    return output_pdf_bytes, total_matches

def extract_json_corrections(text):
    try:
        match = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL)
        if match:
            return json.loads(match.group(1))
        match_brackets = re.search(r'\[\s*\{.*?\}\s*\]', text, re.DOTALL)
        if match_brackets:
            return json.loads(match_brackets.group(0))
    except:
        pass
    return []

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
            
            status_box.write("🤖 AI กำลังเปรียบเทียบจุดผิดพลาด (Description, ราคา, จำนวน)...")
            
            prompt = """
            จงเปรียบเทียบเอกสาร B (ไฟล์ตรวจสอบ PO) กับ เอกสาร A (ไฟล์ตั้งต้น Quotation) อย่างละเอียดที่สุด โดยเจาะจงตรวจสอบ 3 ส่วนหลัก:
            1. Description (รายละเอียด/คำอธิบายสินค้า): ต้องตรงกันแบบคำต่อคำ (Exact Match)
            2. จำนวนสินค้า (Quantity / Qty): ตัวเลขจำนวนต้องตรงกัน
            3. ราคา (Price / Unit Price / Total Price): ตัวเลขราคาต้องตรงกัน

            เงื่อนไขการคัดกรองหน้า:
            - สแกนและรายงานเฉพาะหน้าที่มีรายการสินค้า/Description, ราคา หรือจำนวน ในไฟล์ B เท่านั้น

            รูปแบบรายงานสรุป:
            ### ตารางเปรียบเทียบและผลการตรวจสอบ
            ให้แสดงผลเป็นตาราง Markdown 4 คอลัมน์หลัก (รายการ/รหัสสินค้า, รายละเอียดใน A, รายละเอียดใน B, ผลการตรวจสอบ/สิ่งที่ต้องแก้ไข)

            ### สรุปภาพรวม
            สรุป 3 ด้านหลัก (ราคา, จำนวน, รายละเอียด)

            คำสั่งสำคัญในการส่งพิกัดไปเขียนเส้นขีดฆ่าทับ PDF:
            ที่บรรทัดท้ายสุด ให้ส่ง JSON สรุปคำผิดในไฟล์ B โดยค่า "wrong" ต้องเป็น "คำหรือรหัสที่ผิดสั้นๆ ที่ปรากฏอยู่จริงในไฟล์ B" เท่านั้น:
            ```json
            [
              {"wrong": "คำหรือรหัสเดิมที่ผิดในไฟล์ B", "correct": "ข้อความที่ถูกต้องจากไฟล์ A"}
            ]
            ```

            ข้อห้ามสำคัญ:
            - ห้ามใช้แท็ก HTML เช่น <br> ในตารางหรือข้อความเด็ดขาด
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
            
            status_box.write("⚙️ กำลังขีดฆ่าและฝังกล่องข้อความแก้ไขสีแดงลงในไฟล์ PDF...")
            
            corrections_list = extract_json_corrections(response.text)
            file_b.seek(0)
            pdf_bytes = file_b.read()
            annotated_pdf_bytes, total_annots = annotate_pdf_corrections(pdf_bytes, corrections_list)
            
            status_box.update(label=f"✅ ตรวจสอบและแก้ไขเรียบร้อย! (ฝังรอยขีดฆ่าสีแดงลง PDF ไปทั้งหมด {total_annots} จุด)", state="complete", expanded=False)
            
            st.markdown(clean_output)
            
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
                    label=f"📥 ดาวน์โหลดไฟล์ PDF ที่แก้ไขแล้วจริง ({total_annots} จุด) (.pdf)",
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
