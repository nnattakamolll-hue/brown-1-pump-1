import streamlit as st
from google import genai
from google.genai import types
import tempfile
import os
import time
import json
import re
import fitz  # PyMuPDF สำหรับจัดการและวาดโน้ตลงใน PDF

# ตั้งค่าหน้าเว็บ
st.set_page_config(
    page_title="PDF Auto-Correction & Note Annotation", 
    page_icon="📄", 
    layout="wide"
)

st.title("📄 ระบบตรวจสอบและทำโน้ตแก้ไขไฟล์ PO บน PDF ด้วย AI")
st.caption("เปรียบเทียบไฟล์ Quotation กับ PO พร้อมสร้างไฟล์ PDF ฉบับแก้ไข (ขีดค่าคำผิดและแปะโน้ตคำที่ถูกต้องให้อัตโนมัติ)")

# ช่องกรอก API Key
api_key = st.text_input("🔑 กรอก Gemini API Key ของคุณที่นี่", type="password", help="วาง API Key ที่ได้มาจาก Google AI Studio ตรงนี้")

st.divider()

# ส่วนอัปโหลดไฟล์
col1, col2 = st.columns(2)
with col1:
    st.subheader("1. ไฟล์ตั้งต้น (Baseline - Quotation)")
    file_a = st.file_uploader("เลือกไฟล์ PDF Quotation", type=["pdf"], key="file_quotation")

with col2:
    st.subheader("2. ไฟล์ที่ต้องการตรวจ (To Check - PO)")
    file_b = st.file_uploader("เลือกไฟล์ PDF PO", type=["pdf"], key="file_po")

# ฟังก์ชันอัปโหลดไฟล์เข้าสู่ Gemini API
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

# ฟังก์ชันใช้ PyMuPDF ขีดฆ่าข้อความที่ผิดและแปะโน้ตข้อความที่ถูกต้องลงใน PDF
def annotate_pdf_corrections(input_pdf_bytes, corrections):
    doc = fitz.open(stream=input_pdf_bytes, filetype="pdf")
    
    for page in doc:
        for item in corrections:
            wrong_text = item.get("wrong", "").strip()
            correct_text = item.get("correct", "").strip()
            
            if not wrong_text:
                continue
                
            # ค้นหาตำแหน่งของข้อความที่ผิดในหน้ากระดาษ
            text_instances = page.search_for(wrong_text)
            for inst in text_instances:
                # 1. วาดเส้นขีดฆ่าสีแดงทับข้อความที่ผิด
                strike = page.add_strikeout_annot(inst)
                strike.set_colors(stroke=(1, 0, 0)) # สีแดง
                strike.update()
                
                # 2. สร้างกล่องโน้ตข้อความ (FreeText Annotation) สีเหลืองแสดงคำที่ถูกต้องไว้ใกล้ๆ
                note_rect = fitz.Rect(inst.x0, inst.y1 + 3, inst.x0 + 250, inst.y1 + 25)
                freetext = page.add_freetext_annot(
                    note_rect, 
                    f"-> แก้ไขเป็น: {correct_text}", 
                    fontsize=9, 
                    text_color=(1, 0, 0), # ตัวหนังสือสีแดง
                    fill=(1, 1, 0.8)      # พื้นหลังสีเหลืองอ่อนสไตล์กระดาษโน้ต
                )
                freetext.update()
                
    output_pdf_bytes = doc.write()
    doc.close()
    return output_pdf_bytes

# ฟังก์ชันช่วยแกะ JSON รายการที่ต้องแก้ไขจากข้อความที่ AI ตอบกลับ
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

# ปุ่มเริ่มทำงาน
if st.button("🚀 เริ่มตรวจสอบและสร้างไฟล์ PDF แก้ไข", type="primary", use_container_width=True):
    if not api_key:
        st.error("❌ กรุณากรอก Gemini API Key ในช่องด้านบนก่อนครับ")
    elif not file_a or not file_b:
        st.error("❌ กรุณาอัปโหลดไฟล์ PDF ให้ครบทั้ง 2 ไฟล์ครับ")
    else:
        status_box = st.status("🔍 กำลังประมวลผลระบบ...", expanded=True)
        uploaded_files = []
        
        try:
            client = genai.Client(api_key=api_key)
            
            status_box.write("📤 กำลังอัปโหลดไฟล์เข้าสู่ระบบวิเคราะห์ของ AI...")
            g_a = upload_to_gemini(client, file_a)
            uploaded_files.append(g_a.name)
            
            g_b = upload_to_gemini(client, file_b)
            uploaded_files.append(g_b.name)
            
            status_box.write("🤖 AI กำลังวิเคราะห์เปรียบเทียบจุดที่ต้องแก้ไข...")
            
            # Master Prompt ที่สั่งให้ AI ออกตารางรายงานและสรุปข้อมูล JSON สำหรับทำ Annotations
            prompt = """
            จงเปรียบเทียบเอกสาร B (ไฟล์ตรวจสอบ PO) กับ เอกสาร A (ไฟล์ตั้งต้น Quotation) อย่างละเอียด โดยเจาะจงตรวจสอบ Description, ราคา และจำนวน

            เงื่อนไขการคัดกรองหน้า:
            - สแกนและรายงานเฉพาะหน้าที่มีรายการสินค้า/Description, ราคา หรือจำนวน ในไฟล์ B เท่านั้น

            รูปแบบการรายงานผล:
            1. แสดงตาราง Markdown 4 คอลัมน์ (รายการ/รหัสสินค้า, รายละเอียดใน A, รายละเอียดใน B, ผลการตรวจสอบ/สิ่งที่ต้องแก้ไข)
            2. ส่วนสรุปภาพรวมด้านล่าง (ราคา, จำนวน, รายละเอียด)
            
            คำสั่งพิเศษสำหรับระบบอัดโน้ต PDF:
            ที่บรรทัดสุดท้ายสุดของข้อความ ให้ใส่บล็อกโค้ด JSON ของรายการที่ต้องแก้ไขในรูปแบบนี้เท่านั้น เพื่อให้โปรแกรมนำไปขีดฆ่าและแปะโน้ตใน PDF:
            ```json
            [
              {"wrong": "ข้อความผิดหรือข้อความเดิมที่ปรากฏในไฟล์ B จริงๆ", "correct": "ข้อความที่ถูกต้องจากไฟล์ A ที่ต้องแก้ไขใส่แทน"}
            ]
            ```
            ห้ามใช้แท็ก HTML เช่น <br> เด็ดขาด
            """
            
            # ระบบลองส่งซ้ำอัตโนมัติ (Auto-Retry) เมื่อเจอ 503 High Demand
            max_retries = 5
            retry_delay = 5
            response = None
            success = False
            
            for attempt in range(max_retries):
                try:
                    status_box.write(f"🔄 กำลังประมวลผล (พยายามครั้งที่ {attempt+1}/{max_retries})...")
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
            
            status_box.update(label="✅ ตรวจสอบและสร้างไฟล์โน้ตเสร็จสิ้น!", state="complete", expanded=False)
            
            # แสดงผลรายงานตารางบนหน้าเว็บ
            st.markdown(clean_output)
            
            # ดึงข้อมูล JSON รายการที่ต้องแก้เพื่อนำมาวาดโน้ตลง PDF
            corrections_list = extract_json_corrections(response.text)
            
            status_box.write("⚙️ กำลังวาดเส้นขีดฆ่าและแปะโน้ตคำที่ถูกต้องลงในไฟล์ PDF...")
            file_b.seek(0)
            pdf_bytes = file_b.read()
            
            # ทำการ Annotate ขีดฆ่าและใส่โน้ตลงใน PDF
            annotated_pdf_bytes = annotate_pdf_corrections(pdf_bytes, corrections_list)
            
            # ส่วนปุ่มดาวน์โหลด
            st.divider()
            col_dl1, col_dl2 = st.columns(2)
            with col_dl1:
                st.download_button(
                    label="📥 ดาวน์โหลดรายงานผล (.txt)",
                    data=clean_output,
                    file_name="po_audit_report.txt",
                    mime="text/plain"
                )
            with col_dl2:
                st.download_button(
                    label="📥 ดาวน์โหลดไฟล์ PDF ที่ขีดฆ่าและแปะโน้ตแล้ว (.pdf)",
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
