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
st.caption("เปรียบเทียบไฟล์ Quotation (File A) กับ PO (File B) ทุกหน้า พร้อมระบบวาดแก้ไขครบถ้วนทุกจุดแบบ 100%")

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

def draw_red_correction_box_on_rect(page, inst, correct_text, index):
    """
    ฟังก์ชันวาดเส้นขีดฆ่าสีแดง ลากเส้นชี้ และกล่องข้อความสีแดงลงบนพิกัดที่กำหนด
    """
    y_center = (inst.y0 + inst.y1) / 2
    
    # 1. วาดเส้นขีดฆ่าสีแดงทับคำผิด
    page.draw_line(
        fitz.Point(inst.x0, y_center),
        fitz.Point(inst.x1, y_center),
        color=(1, 0, 0),
        width=1.8
    )
    
    # 2. คำนวณตำแหน่งกล่องข้อความด้านข้าง
    box_x0 = inst.x1 + 10
    box_y0 = inst.y0 + (index * 18)
    box_w = max(len(correct_text) * 7, 120)
    box_h = 18
    
    rect = fitz.Rect(box_x0, box_y0, box_x0 + box_w, box_y0 + box_h)
    
    # 3. วาดเส้นชี้สีแดง
    page.draw_line(
        fitz.Point(inst.x1, y_center),
        fitz.Point(box_x0, box_y0 + (box_h / 2)),
        color=(1, 0, 0),
        width=1.0
    )
    
    # 4. วาดกล่องข้อความสีแดง พื้นหลังสีขาว
    page.draw_rect(
        rect,
        color=(1, 0, 0),
        fill=(1, 1, 1),
        width=1.2
    )
    
    # 5. พิมพ์ข้อความที่ถูกต้องลงในกล่อง
    page.insert_text(
        fitz.Point(box_x0 + 4, box_y0 + 13),
        correct_text,
        fontsize=8.5,
        color=(1, 0, 0),
        fontname="helv"
    )

def annotate_pdf_hybrid_complete(input_pdf_bytes, corrections):
    """
    ระบบวาดรอยแก้ไขผสมผสาน (รองรับทั้งพิกัด Box 2D จาก AI และ Text Search เก็บตกครบทุกจุด)
    """
    doc = fitz.open(stream=input_pdf_bytes, filetype="pdf")
    applied_logs = []
    
    for item in corrections:
        page_num = int(item.get("page", 1)) - 1
        if page_num < 0 or page_num >= len(doc):
            page_num = 0  # ถ้าหน้าเกิน ให้เริ่มที่หน้า 1 เสมอ
            
        page = doc[page_num]
        box_2d = item.get("box_2d")
        wrong_text = str(item.get("wrong", "")).strip()
        correct_text = str(item.get("correct", "")).strip()
        
        if not wrong_text:
            continue
            
        inst = None
        
        # วิธีที่ 1: ถ้ามีพิกัด box_2d ที่แม่นยำจาก AI นำมาแปลงเป็นพิกัดจริง
        if box_2d and len(box_2d) == 4:
            ymin, xmin, ymax, xmax = box_2d
            w = page.rect.width
            h = page.rect.height
            inst = fitz.Rect(
                xmin * w / 1000.0,
                ymin * h / 1000.0,
                xmax * w / 1000.0,
                ymax * h / 1000.0
            )
            
        # วิธีที่ 2: ถ้าไม่มีพิกัด หรือพิกัดใช้ไม่ได้ ให้ใช้ระบบ Text Search ค้นหาคำผิดบนหน้านั้นๆ ทันที
        if not inst or inst.is_empty:
            text_instances = page.search_for(wrong_text)
            if not text_instances and (" " in wrong_text or "-" in wrong_text or "/" in wrong_text):
                parts = re.split(r'[\s\-\/]+', wrong_text)
                for part in parts:
                    if len(part) >= 3 and not part.isdigit():
                        text_instances = page.search_for(part)
                        if text_instances:
                            break
            if text_instances:
                inst = text_instances[0]
                
        # หากได้ตำแหน่งแล้ว สั่งวาดลง PDF ทันที
        if inst and not inst.is_empty:
            draw_red_correction_box_on_rect(page, inst, correct_text, len(applied_logs))
            applied_logs.append({
                "page": page_num + 1,
                "wrong": wrong_text,
                "correct": correct_text
            })

    output_pdf_bytes = doc.write(clean=True, deflate=True)
    doc.close()
    return output_pdf_bytes, applied_logs

def extract_all_corrections_robust(text):
    """
    ระบบดึงข้อมูลแบบผสมผสาน (ดึงจาก JSON + ดึงเก็บตกจากตาราง Markdown ทุกบรรทัด)
    """
    corrections = []
    
    # 1. ดึงข้อมูลจาก JSON Block
    try:
        match = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL)
        if match:
            parsed = json.loads(match.group(1))
            if isinstance(parsed, list):
                corrections.extend(parsed)
    except:
        pass
        
    # 2. แกะข้อมูลเก็บตกจากตาราง Markdown อัตโนมัติ เพื่อให้มั่นใจว่าครบทุกจุดตามตารางรายงาน
    lines = text.split('\n')
    for line in lines:
        if '|' in line and '---' not in line and 'รายการ' not in line:
            cols = [c.strip() for c in line.split('|') if c.strip()]
            if len(cols) >= 4:
                wrong_val = cols[2]  # รายละเอียดไฟล์ B
                correct_val = cols[1] # รายละเอียดไฟล์ A
                
                # เช็คว่ามีคำนี้ในรายการรึยัง ถ้ายังให้แอดเพิ่มเข้าไป
                if wrong_val and correct_val and "รายละเอียด" not in wrong_val:
                    if not any(c.get("wrong") == wrong_val for c in corrections):
                        corrections.append({
                            "page": 1,
                            "wrong": wrong_val,
                            "correct": correct_val,
                            "box_2d": None # ให้ระบบ Text Search วิ่งหาพิกัดเก็บตกให้
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
            
            status_box.write("🤖 AI กำลังสแกนทุกหน้าและรวบรวมจุดที่ต้องแก้ไขอย่างละเอียด...")
            
            prompt = """
            จงตรวจสอบและเปรียบเทียบเอกสาร B (ไฟล์ตรวจสอบ PO) กับ เอกสาร A (ไฟล์ตั้งต้น Quotation) อย่างละเอียดทุกหน้า ทุกรายการสินค้า โดยต้องรายงานให้ครบทุกจุด ห้ามละเว้น:
            1. Description (รายละเอียด/รหัสรุ่นสินค้า): ต้องตรงกันทุกประการ
            2. จำนวนสินค้า (Quantity / Qty): ตัวเลขจำนวนต้องตรงกัน
            3. ราคา (Unit Price / Total Price): ตัวเลขราคาต้องตรงกัน

            รูปแบบรายงานสรุป:
            ### ตารางเปรียบเทียบและผลการตรวจสอบ
            แสดงผลเป็นตาราง Markdown 4 คอลัมน์หลัก (รายการ/รหัสสินค้า, รายละเอียดใน A, รายละเอียดใน B, ผลการตรวจสอบ/สิ่งที่ต้องแก้ไข)

            ### สรุปภาพรวม
            สรุป 3 ด้านหลัก (ราคา, จำนวน, รายละเอียด)

            คำสั่งสำคัญมากๆ สำหรับการวาดเส้นแก้บน PDF:
            ที่บรรทัดท้ายสุด ส่ง JSON สรุปรายการคำผิดทั้งหมดที่พบในเอกสาร B แบบครบทุกจุดทุกหน้า พร้อมระบุเลขหน้า (page เริ่มจาก 1), คำผิด (wrong), คำถูก (correct) และพิกัดกล่องข้อความ (box_2d ในรูปแบบ [ymin, xmin, ymax, xmax] สเกล 0 ถึง 1000):
            ```json
            [
              {
                "page": 1,
                "wrong": "คำผิดในเอกสาร B",
                "correct": "คำที่ถูกต้องจากเอกสาร A",
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
            
            status_box.write("⚙️ กำลังวาดเส้นขีดฆ่าและกล่องข้อความแก้ไขลงตามพิกัดแบบครบทุกจุด...")
            
            corrections_list = extract_all_corrections_robust(response.text)
            file_b.seek(0)
            pdf_bytes = file_b.read()
            annotated_pdf_bytes, applied_logs = annotate_pdf_hybrid_complete(pdf_bytes, corrections_list)
            
            status_box.update(label=f"✅ ตรวจสอบและแก้ไขเรียบร้อย! (วาดพิกัดลง PDF ครบถ้วนทั้งหมด {len(applied_logs)} จุด)", state="complete", expanded=False)
            
            st.markdown(clean_output)
            
            with st.expander("🔍 ตรวจสอบพิกัดและรายการที่ระบบนำไปวาดบน PDF"):
                st.write("รายการ Corrections ทั้งหมดที่ดึงได้:", corrections_list)
                st.write("รายการที่วาดลงไฟล์ PDF สำเร็จจริง:", applied_logs)
            
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
                    label=f"📥 ดาวน์โหลดไฟล์ PDF ที่แก้ไขครบถ้วน ({len(applied_logs)} จุด) (.pdf)",
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
