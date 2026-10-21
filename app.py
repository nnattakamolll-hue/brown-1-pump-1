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
st.caption("เปรียบเทียบไฟล์ Quotation (File A) กับ PO (File B) ทุกหน้าแบบเจาะลึกครบถ้วนทุกจุด 100% (รองรับเอกสารหลายหน้า)")

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

def draw_precise_red_correction(page, inst, correct_text, index):
    y_center = (inst.y0 + inst.y1) / 2
    
    # 1. วาดเส้นขีดฆ่าสีแดงกระชับเฉพาะความยาวคำผิด
    page.draw_line(
        fitz.Point(inst.x0, y_center),
        fitz.Point(inst.x1, y_center),
        color=(1, 0, 0),
        width=1.8
    )
    
    # 2. คำนวณตำแหน่งกล่องข้อความด้านข้าง
    box_x0 = inst.x1 + 10
    box_y0 = inst.y0 + (index * 18)
    box_w = max(len(correct_text) * 7.5, 110)
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
    
    # 5. พิมพ์ตัวหนังสือสีแดงเฉพาะคำแก้ที่ถูกต้อง
    page.insert_text(
        fitz.Point(box_x0 + 4, box_y0 + 13),
        correct_text,
        fontsize=8.5,
        color=(1, 0, 0),
        fontname="helv"
    )

def annotate_pdf_exhaustive_all(input_pdf_bytes, corrections):
    doc = fitz.open(stream=input_pdf_bytes, filetype="pdf")
    applied_logs = []
    
    for item in corrections:
        wrong_text = str(item.get("wrong", "")).strip()
        correct_text = str(item.get("correct", "")).strip()
        page_hint = int(item.get("page", 1)) - 1
        box_2d = item.get("box_2d")
        
        if not wrong_text or len(wrong_text) < 1:
            continue
            
        inst = None
        target_page_idx = page_hint if (0 <= page_hint < len(doc)) else 0
        page = doc[target_page_idx]
        
        # 1. ค้นหาด้วยคำผิดสั้นๆ บนหน้า PDF (Exact Search)
        text_instances = page.search_for(wrong_text)
        if text_instances:
            inst = text_instances[0]
        else:
            # ถ้าหน้าฮินต์หาไม่เจอ ลองสแกนหาทั่วทุกหน้าในเอกสาร
            for p_idx, p in enumerate(doc):
                found = p.search_for(wrong_text)
                if found:
                    page = p
                    target_page_idx = p_idx
                    inst = found[0]
                    break
                    
        # 2. ถ้ายังไม่เจอ ลองใช้พิกัด box_2d จาก AI
        if not inst and box_2d and len(box_2d) == 4:
            ymin, xmin, ymax, xmax = box_2d
            w, h = page.rect.width, page.rect.height
            inst = fitz.Rect(xmin * w / 1000.0, ymin * h / 1000.0, xmax * w / 1000.0, ymax * h / 1000.0)
            
        if inst and not inst.is_empty:
            draw_precise_red_correction(page, inst, correct_text, len(applied_logs))
            applied_logs.append({
                "page": target_page_idx + 1,
                "wrong": wrong_text,
                "correct": correct_text
            })

    output_pdf_bytes = doc.write(clean=True, deflate=True)
    doc.close()
    return output_pdf_bytes, applied_logs

def extract_all_corrections_deep(text):
    corrections = []
    
    # 1. ดึงจากบล็อก JSON ทั้งหมดที่ AI ส่งมา
    json_matches = re.findall(r'```json\s*(.*?)\s*```', text, re.DOTALL)
    for j_str in json_matches:
        try:
            parsed = json.loads(j_str)
            if isinstance(parsed, list):
                corrections.extend(parsed)
        except:
            pass
            
    # ถ้ายังไม่เจอ ลองหาแบ็คเก็ต [] ตรงๆ
    if not corrections:
        try:
            match_brackets = re.search(r'\[\s*\{.*?\}\s*\]', text, re.DOTALL)
            if match_brackets:
                parsed = json.loads(match_brackets.group(0))
                if isinstance(parsed, list):
                    corrections.extend(parsed)
        except:
            pass
            
    # 2. แกะเก็บตกจากตาราง Markdown ทุกแถว เพื่อให้มั่นใจว่าครอบคลุมครบทุกรายการ
    lines = text.split('\n')
    for line in lines:
        if '|' in line and '---' not in line and 'รายการ' not in line:
            cols = [c.strip() for c in line.split('|') if c.strip()]
            if len(cols) >= 4:
                wrong_val = cols[2]  # รายละเอียดใน B ที่ผิด
                correct_val = cols[1] # รายละเอียดใน A ที่ถูกต้อง
                
                if wrong_val and correct_val and "รายละเอียด" not in wrong_val:
                    # ถ้ายังไม่มีในรายการ ให้แอดเพิ่มเข้าไป
                    if not any(c.get("wrong") == wrong_val for c in corrections):
                        corrections.append({
                            "page": 1,
                            "wrong": wrong_val,
                            "correct": correct_val
                        })
                        
    return corrections

st.divider()

if st.button("🚀 เริ่มตรวจสอบและแก้ไขไฟล์ PDF ครบทุกหน้าแบบละเอียด", type="primary", use_container_width=True):
    if not api_key:
        st.error("❌ กรุณากรอก Gemini API Key ในช่องด้านบนก่อนครับ")
    elif not file_a or not file_b:
        st.error("❌ กรุณาอัปโหลดไฟล์ PDF ให้ครบทั้ง 2 ไฟล์ครับ")
    else:
        status_box = st.status("🔍 กำลังประมวลผลระบบแบบเจาะลึกทุกหน้า...", expanded=True)
        uploaded_files = []
        
        try:
            client = genai.Client(api_key=api_key)
            
            status_box.write("📤 กำลังส่งไฟล์เข้าสู่ระบบวิเคราะห์ AI...")
            g_a = upload_to_gemini(client, file_a)
            uploaded_files.append(g_a.name)
            
            g_b = upload_to_gemini(client, file_b)
            uploaded_files.append(g_b.name)
            
            status_box.write("🤖 AI กำลังสแกนตรวจทุกหน้า ทุกบรรทัด แบบห้ามละเว้น...")
            
            prompt = """
            จงตรวจสอบเปรียบเทียบเอกสาร B (ไฟล์ตรวจสอบ PO) กับ เอกสาร A (ไฟล์ตั้งต้น Quotation) อย่างละเอียดถี่ถ้วนที่สุด 'ทุกหน้า ทุกรายการสินค้า (Exhaustive Scan)' 
            ห้ามสรุปย่อ ห้ามตัดทอน และห้ามข้ามรายการสินค้าในหน้าถัดไปเด็ดขาด:
            1. ตรวจสอบ Description (รหัสรุ่นสินค้า/สเปก), ราคา (Unit Price/Total), และจำนวน (Qty) ของทุกหน้า
            2. ระบุรายการที่ผิดพลาดทั้งหมดลงในตารางรายงานให้ครบถ้วนทุกจุด

            รูปแบบรายงานสรุป:
            ### ตารางเปรียบเทียบและผลการตรวจสอบ
            แสดงผลเป็นตาราง Markdown 4 คอลัมน์หลัก (รายการ/รหัสสินค้า, รายละเอียดใน A (ถูกต้อง), รายละเอียดใน B (ที่ผิด), ผลการตรวจสอบ/สิ่งที่ต้องแก้ไข)

            ### สรุปภาพรวม
            สรุป 3 ด้านหลัก (ราคา, จำนวน, รายละเอียด)

            คำสั่งสำคัญมากๆ สำหรับการวาดแก้ไขบน PDF:
            ที่บรรทัดท้ายสุด ให้ส่ง JSON สรุปรายการคำผิดทั้งหมดแบบครอบคลุมครบทุกจุด ทุกหน้า ทุกบรรทัด โดยระบุเลขหน้า (page เริ่มจาก 1), คำผิดสั้นๆ (wrong), และคำที่ถูกต้อง (correct):
            ```json
            [
              {
                "page": 1,
                "wrong": "คำผิดสั้นๆ ใน B",
                "correct": "คำถูกสั้นๆ จาก A"
              }
            ]
            ```
            ห้ามใช้แท็ก HTML เช่น <br> เด็ดขาด
            """
            
            max_retries = 5
            retry_delay = 5
            response = None
            success = False
            
            # ตั้งค่าเพิ่ม Token Limit ให้สูงขึ้น เพื่อป้องกันการโดนตัดทอนข้อความ
            config = types.GenerateContentConfig(
                max_output_tokens=8192,
                temperature=0.1
            )
            
            for attempt in range(max_retries):
                try:
                    status_box.write(f"🔄 กำลังวิเคราะห์ข้อมูลเชิงลึก (พยายามครั้งที่ {attempt+1}/{max_retries})...")
                    response = client.models.generate_content(
                        model='gemini-3.8-flash',
                        contents=[g_a, g_b, prompt],
                        config=config
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
            
            status_box.write("⚙️ กำลังสแกนและวาดขีดฆ่าคำผิดลงบนไฟล์ PDF ครบทุกจุด...")
            
            corrections_list = extract_all_corrections_deep(response.text)
            file_b.seek(0)
            pdf_bytes = file_b.read()
            annotated_pdf_bytes, applied_logs = annotate_pdf_exhaustive_all(pdf_bytes, corrections_list)
            
            status_box.update(label=f"✅ ตรวจสอบและแก้ไขครบถ้วนทุกหน้า! (วาดลง PDF ไปทั้งหมด {len(applied_logs)} จุด)", state="complete", expanded=False)
            
            st.markdown(clean_output)
            
            with st.expander("🔍 ตรวจสอบรายการจุดแก้ไขทั้งหมดที่ฝังลงบน PDF"):
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
                    label=f"📥 ดาวน์โหลดไฟล์ PDF ที่แก้ไขครบถ้วนทุกจุด ({len(applied_logs)} จุด) (.pdf)",
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
