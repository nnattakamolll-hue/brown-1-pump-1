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

st.title("📄 ระบบตรวจสอบและทำโน้ตแก้ไขไฟล์ PO กับ Quotation ด้วย AI")
st.caption("เปรียบเทียบไฟล์ Quotation (File A) กับ PO (File B) พร้อมขีดค่าและแปะกล่องข้อความสีแดงลงในไฟล์ PDF")

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

def draw_custom_red_annotation(page, inst, correct_text, index):
    """
    ฟังก์ชันสำหรับวาดเส้นขีดฆ่าสีแดง + วาดเส้นชี้ + สร้างกล่องข้อความสีแดงตามสไตล์ตัวอย่าง
    """
    # 1. วาดเส้นขีดฆ่าสีแดงทับคำผิด
    strike = page.add_strikeout_annot(inst)
    strike.set_colors(stroke=(1, 0, 0))  # สีแดงสด (RGB: 1, 0, 0)
    strike.update()

    # 2. คำนวณตำแหน่งกล่องข้อความให้อยู่ด้านล่างขวาของจุดที่ผิด
    box_x0 = inst.x1 + 30
    box_y0 = inst.y1 + 15 + (index * 25) # ขยับตำแหน่งลงมาหากมีหลายจุดใกล้กัน
    box_w = max(len(correct_text) * 7, 140)
    box_h = 20
    
    rect = fitz.Rect(box_x0, box_y0, box_x0 + box_w, box_y0 + box_h)
    
    # 3. วาดเส้นลูกศร/เส้นชี้จากข้อความที่ขีดฆ่าไปยังกล่องข้อความ
    line = page.add_line_annot(fitz.Point(inst.x1, inst.y1), fitz.Point(box_x0, box_y0 + (box_h / 2)))
    line.set_colors(stroke=(1, 0, 0))
    line.update()

    # 4. วาดกล่องสี่เหลี่ยมขอบสีแดง พื้นหลังสีขาว
    rect_annot = page.add_rect_annot(rect)
    rect_annot.set_colors(stroke=(1, 0, 0), fill=(1, 1, 1))
    rect_annot.set_border(width=1.5)
    rect_annot.update()

    # 5. ใส่ตัวหนังสือสีแดงลงในกล่องข้อความ
    page.insert_text(
        fitz.Point(box_x0 + 5, box_y0 + 14),
        correct_text,
        fontsize=9,
        color=(1, 0, 0),
        fontname="helv"
    )

def annotate_pdf_corrections(input_pdf_bytes, corrections):
    doc = fitz.open(stream=input_pdf_bytes, filetype="pdf")
    total_annots = 0
    
    for page in doc:
        page_annot_idx = 0
        for item in corrections:
            wrong_text = item.get("wrong", "").strip()
            correct_text = item.get("correct", "").strip()
            
            if not wrong_text or len(wrong_text) < 2:
                continue
                
            # ค้นหาตำแหน่งข้อความเดิมในหน้า PDF
            text_instances = page.search_for(wrong_text)
            
            # หากประโยคยาวไป ให้ลองค้นด้วยรหัสคำแรก
            if not text_instances and " " in wrong_text:
                first_word = wrong_text.split()[0]
                if len(first_word) >= 3:
                    text_instances = page.search_for(first_word)
            
            for inst in text_instances:
                draw_custom_red_annotation(page, inst, correct_text, page_annot_idx)
                page_annot_idx += 1
                total_annots += 1

    output_pdf_bytes = doc.write(clean=True, deflate=True)
    doc.close()
    return output_pdf_bytes, total_annots

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

if st.button("🚀 เริ่มตรวจสอบและสร้างรายงาน", type="primary", use_container_width=True):
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
            
            status_box.write("🤖 AI กำลังวิเคราะห์และคัดกรองเฉพาะหน้าที่เกี่ยวข้อง...")
            
            prompt = """
            จงเปรียบเทียบเอกสาร B (ไฟล์ตรวจสอบ PO) กับ เอกสาร A (ไฟล์ตั้งต้น Quotation) อย่างละเอียดที่สุด โดยเจาะจงตรวจสอบ 3 ส่วนหลัก:
            1. Description (รายละเอียด/คำอธิบายสินค้า): ต้องตรงกันแบบคำต่อคำ (Exact Match)
            2. จำนวนสินค้า (Quantity / Qty): ตัวเลขจำนวนต้องตรงกัน
            3. ราคา (Price / Unit Price / Total Price): ตัวเลขราคาต้องตรงกัน

            เงื่อนไขการคัดกรองหน้า:
            - ให้สแกนและรายงานเฉพาะหน้าที่มีรายการสินค้า/Description, ราคา หรือจำนวน ในไฟล์ B เท่านั้น

            โครงสร้างและรูปแบบการรายงานผลที่ต้องแสดงผล:
            ### ตารางเปรียบเทียบและผลการตรวจสอบ
            ให้แสดงผลเป็นตาราง Markdown 4 คอลัมน์หลัก (รายการ/รหัสสินค้า, รายละเอียดใน A, รายละเอียดใน B, ผลการตรวจสอบ/สิ่งที่ต้องแก้ไข)

            ### สรุปภาพรวม
            สรุป 3 ด้านหลัก (ราคา, จำนวน, รายละเอียด)

            คำสั่งสำคัญสำหรับการสร้างพิกัดขีดฆ่าและใส่ข้อความแก้ไข:
            ที่บรรทัดท้ายสุด ให้ระบุรายการข้อความที่ผิดในรูปแบบ JSON โดยค่า "wrong" ต้องเป็น "รหัสสินค้าหรือคำสั้นๆ ที่ผิดในไฟล์ B" เท่านั้น เพื่อให้ระบบค้นหาตำแหน่งเจอ:
            ```json
            [
              {"wrong": "ข้อความ/รหัสเดิมที่ผิดในไฟล์ B", "correct": "ข้อความ/รหัสที่ถูกต้องจากไฟล์ A"}
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
            
            status_box.write("⚙️ กำลังวาดเส้นขีดฆ่าและกล่องข้อความแก้ไขสีแดงลงในไฟล์ PDF...")
            
            corrections_list = extract_json_corrections(response.text)
            file_b.seek(0)
            pdf_bytes = file_b.read()
            annotated_pdf_bytes, total_annots = annotate_pdf_corrections(pdf_bytes, corrections_list)
            
            status_box.update(label=f"✅ ตรวจสอบเสร็จสิ้น! (ทำการขีดฆ่าและแปะกล่องข้อความไปทั้งหมด {total_annots} จุด)", state="complete", expanded=False)
            
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
                    label=f"📥 ดาวน์โหลดไฟล์ PDF ฉบับขีดฆ่าและแก้คำแล้ว ({total_annots} จุด) (.pdf)",
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
