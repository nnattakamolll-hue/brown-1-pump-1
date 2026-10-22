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
st.caption("เปรียบเทียบไฟล์ Quotation (File A) กับ PO (File B) ยึดใบตั้งต้นเป็นหลัก 100% และแก้ไขลง PDF จริงอย่างแม่นยำ")

api_key = st.text_input("กรอก Gemini API Key ของคุณที่นี่", type="password", help="วาง API Key ที่ได้มาจาก Google AI Studio ตรงนี้")

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

def annotate_pdf_anti_overlap(input_pdf_bytes, corrections):
    doc = fitz.open(stream=input_pdf_bytes, filetype="pdf")
    applied_logs = []
    
    # 1. กรองรายการที่ซ้ำกันออก
    unique_corrections = []
    seen = set()
    for item in corrections:
        page_num = int(item.get("page", 1))
        wrong_text = str(item.get("wrong", "")).strip()
        correct_text = str(item.get("correct", "")).strip()
        
        if not wrong_text or len(wrong_text) < 1:
            continue
            
        key = (page_num, wrong_text, correct_text)
        if key not in seen:
            seen.add(key)
            unique_corrections.append(item)

    # 2. จัดกลุ่มรายการตามหน้า PDF
    page_corrections = {}
    for item in unique_corrections:
        wrong_text = str(item.get("wrong", "")).strip()
        correct_text = str(item.get("correct", "")).strip()
        page_hint = int(item.get("page", 1)) - 1
        box_2d = item.get("box_2d")
        
        target_page_idx = page_hint if (0 <= page_hint < len(doc)) else 0
        page = doc[target_page_idx]
        
        inst = None
        # วิธีที่ 1: ใช้พิกัด box_2d ถ้ามี
        if box_2d and len(box_2d) == 4:
            ymin, xmin, ymax, xmax = box_2d
            w, h = page.rect.width, page.rect.height
            inst = fitz.Rect(xmin * w / 1000.0, ymin * h / 1000.0, xmax * w / 1000.0, ymax * h / 1000.0)
            
        # วิธีที่ 2: ค้นหาข้อความแบบตรงตัว (Exact Search)
        if not inst or inst.is_empty:
            text_instances = page.search_for(wrong_text)
            if text_instances:
                inst = text_instances[0]
            else:
                for p_idx, p in enumerate(doc):
                    found = p.search_for(wrong_text)
                    if found:
                        page = p
                        target_page_idx = p_idx
                        inst = found[0]
                        break
                        
        # วิธีที่ 3: Smart Keyword Matching (รองรับทั้งภาษาไทยและอังกฤษ)
        if not inst or inst.is_empty:
            keywords = [w for w in re.findall(r'\w+', wrong_text) if len(w) >= 2]
            keywords.sort(key=len, reverse=True)
            for kw in keywords:
                for p_idx, p in enumerate(doc):
                    found = p.search_for(kw)
                    if found:
                        page = p
                        target_page_idx = p_idx
                        inst = found[0]
                        break
                if inst and not inst.is_empty:
                    break
                        
        if inst and not inst.is_empty:
            if target_page_idx not in page_corrections:
                page_corrections[target_page_idx] = []
            page_corrections[target_page_idx].append({
                "inst": inst,
                "wrong": wrong_text,
                "correct": correct_text
            })

    # 3. วาดลง PDF เรียงลำดับจากบนลงล่าง ป้องกันกล่องซ้อนทับกัน
    for p_idx, items in page_corrections.items():
        page = doc[p_idx]
        items.sort(key=lambda x: x["inst"].y0)
        
        last_box_bottom = 0
        for item in items:
            inst = item["inst"]
            correct_text = item["correct"]
            wrong_text = item["wrong"]
            
            y_center = (inst.y0 + inst.y1) / 2
            
            # วาดเส้นขีดฆ่าสีแดง
            page.draw_line(
                fitz.Point(inst.x0, y_center),
                fitz.Point(inst.x1, y_center),
                color=(1, 0, 0),
                width=1.8
            )
            
            box_h = 18
            box_w = max(len(correct_text) * 7.5, 110)
            box_x0 = inst.x1 + 10
            
            box_y0 = inst.y0
            if box_y0 < last_box_bottom + 4:
                box_y0 = last_box_bottom + 4
                
            box_y1 = box_y0 + box_h
            last_box_bottom = box_y1
            
            box_rect = fitz.Rect(box_x0, box_y0, box_x0 + box_w, box_y1)
            
            # วาดเส้นชี้
            page.draw_line(
                fitz.Point(inst.x1, y_center),
                fitz.Point(box_x0, box_y0 + (box_h / 2)),
                color=(1, 0, 0),
                width=1.0
            )
            
            # วาดกล่องข้อความพื้นขาว ขอบแดง
            page.draw_rect(
                box_rect,
                color=(1, 0, 0),
                fill=(1, 1, 1),
                width=1.2
            )
            
            # ใส่ข้อความที่ถูกต้อง
            page.insert_text(
                fitz.Point(box_x0 + 4, box_y0 + 13),
                correct_text,
                fontsize=8.5,
                color=(1, 0, 0),
                fontname="helv"
            )
            
            applied_logs.append({
                "page": p_idx + 1,
                "wrong": wrong_text,
                "correct": correct_text
            })

    output_pdf_bytes = doc.write(clean=True, deflate=True)
    doc.close()
    return output_pdf_bytes, applied_logs

def extract_sensible_corrections(text):
    corrections = []
    
    # 1. ดึงจาก JSON Block
    json_matches = re.findall(r'```json\s*(.*?)\s*```', text, re.DOTALL)
    for j_str in json_matches:
        try:
            parsed = json.loads(j_str)
            if isinstance(parsed, list):
                corrections.extend(parsed)
        except:
            pass
            
    if not corrections:
        try:
            match_brackets = re.search(r'\[\s*\{.*?\}\s*\]', text, re.DOTALL)
            if match_brackets:
                parsed = json.loads(match_brackets.group(0))
                if isinstance(parsed, list):
                    corrections.extend(parsed)
        except:
            pass
            
    # 2. Fallback: ดึงจากตาราง Markdown ทุกรูปแบบอัตโนมัติ (ไม่จำกัดจำนวนคอลัมน์)
    lines = text.split('\n')
    for line in lines:
        if '|' in line and '---' not in line and 'รายการ' not in line:
            cols = [c.strip() for c in line.split('|') if c.strip()]
            if len(cols) >= 3:
                # เลือกคอลัมน์รองสุดท้ายเป็น wrong และคอลัมน์ก่อนหน้าเป็น correct
                correct_val = cols[-3] if len(cols) >= 4 else cols[0]
                wrong_val = cols[-2] if len(cols) >= 4 else cols[1]
                remark = cols[-1]
                
                if any(kw in remark for kw in ["ผิด", "แก้", "ไม่ตรง", "เปลี่ยน", "ต่าง", "ขาด", "หาย", "ตกหล่น"]):
                    if wrong_val and correct_val and "รายละเอียด" not in wrong_val:
                        corrections.append({
                            "page": 1,
                            "wrong": wrong_val,
                            "correct": correct_val
                        })
    return corrections

st.divider()

if st.button("เริ่มตรวจสอบและแก้ไขไฟล์ PDF ตามใบตั้งต้น", type="primary", use_container_width=True):
    if not api_key:
        st.error("กรุณากรอก Gemini API Key ในช่องด้านบนก่อนครับ")
    elif not file_a or not file_b:
        st.error("กรุณาอัปโหลดไฟล์ PDF ให้ครบทั้ง 2 ไฟล์ครับ")
    else:
        status_box = st.status("กำลังประมวลผลตรวจสอบทุกหน้า ทุกแผ่น...", expanded=True)
        uploaded_files = []
        
        try:
            client = genai.Client(api_key=api_key)
            
            status_box.write("กำลังส่งไฟล์เข้าสู่ระบบวิเคราะห์ AI...")
            g_a = upload_to_gemini(client, file_a)
            uploaded_files.append(g_a.name)
            
            g_b = upload_to_gemini(client, file_b)
            uploaded_files.append(g_b.name)
            
            status_box.write("AI กำลังสแกนตรวจเทียบเอกสารทุกหน้า ค้นหาคำผิดและดึงพิกัดมาแก้ไข...")
            
            prompt = '''จงเปรียบเทียบเอกสาร B (ไฟล์ตรวจสอบ PO) กับ เอกสาร A (ไฟล์ตั้งต้น Quotation) อย่างละเอียดทุกหน้า ทุกแผ่น

หลักการตรวจสอบ:
1. ยึดข้อมูลในเอกสาร A (Quotation) เป็นความถูกต้องหลัก 100%
2. หากเอกสาร B มีข้อมูลผิดพลาด หรือการย่อข้อความทำให้สเปก/รหัส/ตัวเลขตกหล่นไม่ตรงกับ A ให้ถือว่าผิดและต้องแก้ไข
3. ห้ามแสดงรายการซ้ำซ้อนกัน ให้สรุปรายการที่ผิดพลาดแบบไม่ซ้ำกันอย่างเป็นระเบียบ

รูปแบบรายงานสรุป:
### ตารางเปรียบเทียบและผลการตรวจสอบ
แสดงผลเป็นตาราง Markdown สรุปรายละเอียดรายการที่ผิดพลาด

### สรุปภาพรวม
สรุปภาพรวมการตรวจสอบ

คำสั่งสำคัญมากสำหรับการนำจุดผิดไปวาดบน PDF:
ที่บรรทัดท้ายสุด ให้ส่ง JSON บล็อกเดียวเท่านั้น สรุปจุดที่ผิดเพี้ยนและต้องแก้ไขจริงแบบไม่ซ้ำกัน พร้อมระบุเลขหน้า (page เริ่มจาก 1), คำ/รหัสที่ผิดใน B (wrong), และคำที่ถูกต้องจาก A (correct):
```json
[
  {
    "page": 1,
    "wrong": "คำหรือรหัสที่ผิดใน B",
    "correct": "คำหรือรหัสที่ถูกต้องจาก A"
  }
]
```'''
            
            max_retries = 5
            retry_delay = 5
            response = None
            success = False
            
            config = types.GenerateContentConfig(
                max_output_tokens=8192,
                temperature=0.1
            )
            
            for attempt in range(max_retries):
                try:
                    status_box.write(f"กำลังวิเคราะห์ข้อมูล (พยายามครั้งที่ {attempt+1}/{max_retries})...")
                    response = client.models.generate_content(
                        model='gemini-3.8-flash',
                        contents=[g_a, g_b, prompt],
                        config=config
                    )
                    success = True
                    break
                except Exception as api_err:
                    if ("503" in str(api_err) or "UNAVAILABLE" in str(api_err)) and attempt < max_retries - 1:
                        status_box.warning(f"เซิร์ฟเวอร์หนาแน่น กำลังลองใหม่ในอีก {retry_delay} วินาที...")
                        time.sleep(retry_delay)
                        retry_delay += 3
                    else:
                        raise api_err
            
            if not success or not response:
                raise Exception("เซิร์ฟเวอร์หนาแน่นเกินไป โปรดลองกดปุ่มเริ่มตรวจสอบใหม่อีกครั้ง")
            
            clean_output = response.text.replace("<br>", " ").replace("<br/>", " ").replace("<BR>", " ")
            
            status_box.write("กำลังวาดขีดฆ่าและจัดระเบียบกล่องข้อความลงบนไฟล์ PDF จริง...")
            
            corrections_list = extract_sensible_corrections(response.text)
            file_b.seek(0)
            pdf_bytes = file_b.read()
            annotated_pdf_bytes, applied_logs = annotate_pdf_anti_overlap(pdf_bytes, corrections_list)
            
            status_box.update(label=f"ตรวจสอบและแก้ไขเรียบร้อย! (วาดลง PDF ไปทั้งหมด {len(applied_logs)} จุด)", state="complete", expanded=False)
            
            st.markdown(clean_output)
            
            with st.expander("ตรวจสอบรายการจุดแก้ไขทั้งหมดที่ฝังลงบน PDF"):
                st.write("รายการจุดแก้ไขที่ไม่ซ้ำกัน:", corrections_list)
                st.write("รายการที่ทำการวาดลงไฟล์ PDF สำเร็จจริง:", applied_logs)
            
            st.divider()
            col_dl1, col_dl2 = st.columns(2)
            with col_dl1:
                st.download_button(
                    label="ดาวน์โหลดรายงานผลสรุป (.txt)",
                    data=clean_output,
                    file_name="po_audit_report.txt",
                    mime="text/plain"
                )
            with col_dl2:
                st.download_button(
                    label=f"ดาวน์โหลดไฟล์ PDF ที่แก้ไขแล้ว ({len(applied_logs)} จุด) (.pdf)",
                    data=annotated_pdf_bytes,
                    file_name="annotated_po_document.pdf",
                    mime="application/pdf"
                )

        except Exception as e:
            status_box.update(label="เกิดข้อผิดพลาดในการประมวลผล", state="error", expanded=True)
            st.error(f"รายละเอียด: {str(e)}")
            
        finally:
            if uploaded_files and 'client' in locals():
                for name in uploaded_files:
                    try:
                        client.files.delete(name=name)
                    except:
                        pass
