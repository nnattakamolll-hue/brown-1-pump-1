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
st.caption("เปรียบเทียบไฟล์ Quotation (File A) กับ PO (File B) แบบละเอียดครบถ้วนทุกหน้า ทุกแผ่น ตามใบตั้งต้น 100%")

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

def draw_sensible_red_correction(page, inst, correct_text, index):
    y_center = (inst.y0 + inst.y1) / 2
    
    # 1. วาดเส้นขีดฆ่าสีแดงกระชับเฉพาะความยาวคำผิด
    page.draw_line(
        fitz.Point(inst.x0, y_center),
        fitz.Point(inst.x1, y_center),
        color=(1, 0, 0),
        width=1.8
    )
    
    # 2. คำนวณตำแหน่งกล่องข้อความแก้ไข
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

def annotate_pdf_smart_search(input_pdf_bytes, corrections):
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
        
        # 1. ใช้พิกัด box_2d จาก AI ถ้ามี
        if box_2d and len(box_2d) == 4:
            ymin, xmin, ymax, xmax = box_2d
            w, h = page.rect.width, page.rect.height
            inst = fitz.Rect(xmin * w / 1000.0, ymin * h / 1000.0, xmax * w / 1000.0, ymax * h / 1000.0)
            
        # 2. ค้นหาด้วย Exact Search บนหน้าพิกัด หรือสแกนหาทั่วทุกหน้า
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
                        
        # 3. แยก Token ค้นหาเฉพาะรหัส/ตัวเลขย่อยสำคัญ
        if not inst or inst.is_empty:
            keywords = re.findall(r'[A-Za-z0-9\-\/\.]+', wrong_text)
            keywords.sort(key=len, reverse=True)
            for kw in keywords:
                if len(kw) >= 3:
                    for p_idx, p in enumerate(doc):
                        found = p.search_for(kw)
                        if found:
