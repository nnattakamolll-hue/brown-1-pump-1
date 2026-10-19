import streamlit as st
from google import genai
from google.genai import types
import tempfile
import os
import time

# ตั้งค่าหน้าเว็บ
st.set_page_config(
    page_title="PDF PO & Quotation Auditor", 
    page_icon="📄", 
    layout="wide"
)

st.title("📄 ระบบตรวจสอบความถูกต้อง PO กับ Quotation ด้วย AI")
st.caption("เปรียบเทียบไฟล์ Quotation (File A) กับไฟล์ PO (File B) เจาะจงเฉพาะ Description, ราคา และจำนวน พร้อมสรุปรายงานแบบละเอียด")

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

# ฟังก์ชันอัปโหลดไฟล์เข้าสู่ Gemini API พร้อมเช็คสถานะ
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

st.divider()

# ปุ่มเริ่มทำงาน
if st.button("🚀 เริ่มตรวจสอบและออกรายงาน", type="primary", use_container_width=True):
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
            
            status_box.write("🤖 AI กำลังวิเคราะห์และตรวจสอบเอกสาร...")
            
            # Master Prompt ฉบับสมบูรณ์
            prompt = """
            จงเปรียบเทียบเอกสาร B (ไฟล์ตรวจสอบ PO) กับ เอกสาร A (ไฟล์ตั้งต้น Quotation) อย่างละเอียดที่สุด โดยเจาะจงตรวจสอบ 3 ส่วนหลัก:
            1. Description (รายละเอียด/คำอธิบายสินค้า): ต้องตรงกันแบบคำต่อคำ (Exact Match)
            2. จำนวนสินค้า (Quantity / Qty): ตัวเลขจำนวนต้องตรงกัน
            3. ราคา (Price / Unit Price / Total Price): ตัวเลขราคาต้องตรงกัน

            เงื่อนไขการคัดกรองหน้า:
            - ให้สแกนและรายงานเฉพาะหน้าที่มีรายการสินค้า/Description, ราคา หรือจำนวน ในไฟล์ B เท่านั้น (มองข้ามหน้าเงื่อนไขสัญญา หน้าปก หรือหน้าข้อกำหนดอื่นๆ ที่ไม่เกี่ยวข้อง)

            โครงสร้างและรูปแบบการรายงานผลที่ต้องแสดงผล (ต้องตามรูปแบบนี้ทุกประการ):

            ### ตารางเปรียบเทียบและผลการตรวจสอบ
            ให้แสดงผลเป็นตาราง Markdown ที่มี 4 คอลัมน์หลัก ดังนี้:
            1. รายการ / รหัสสินค้า (ไฟล์ B): ระบุหน้า, ประเภทเอกสาร (เช่น PO, Quotation), Item No., และรหัสสินค้า
            2. รายละเอียดใน เอกสาร A (ไฟล์ตั้งต้น): ระบุ Description เต็มๆ พร้อมระบุ Qty, Unit Price, และ Amount ให้ครบถ้วน
            3. รายละเอียดใน เอกสาร B (ไฟล์ตรวจสอบ): ระบุรายละเอียดที่ปรากฏจริงในไฟล์ B พร้อมรูปแบบการจัดหน้า/แยกบรรทัด, Qty, Unit Price, และ Amount
            4. ผลการตรวจสอบ / สิ่งที่ต้องแก้ไข: ระบุจุดที่แตกต่างอย่างละเอียด (เช่น สเปกที่ขาดหายไป, รหัสรุ่นที่ถูกตัดทอน, การขีดฆ่า, หน่วยนับที่ต่างกัน เช่น SET กับ pcs) พร้อมบอกชัดเจนว่าต้องเติมหรือแก้ไขอย่างไร

            ### สรุปภาพรวม
            (ให้วางหัวข้อนี้ไว้ใต้ตาราง โดยแบ่งเป็น 3 ประเด็นหลักดังนี้):
            1. ด้านราคา (Price): สรุปความถูกต้องของราคาต่อหน่วยและราคารวมทั้งหมด
            2. ด้านจำนวน (Quantity): สรุปความถูกต้องของปริมาณ และรูปแบบการนับหน่วย
            3. ด้านรายละเอียด (Description): สรุปภาพรวมแยกตามประเภทเอกสารในไฟล์ B ว่ามีจุดผิดเพี้ยนหลักอะไรบ้าง และข้อเสนอแนะในการแก้ไข

            ข้อห้ามสำคัญ:
            - ห้ามใช้แท็ก HTML เช่น <br> ในตารางหรือข้อความเด็ดขาด หากต้องการขึ้นบรรทัดใหม่ให้ใช้การเว้นบรรทัดปกติหรือใช้สัญลักษณ์ Bullet (•) เพื่อความสะอาดตา
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
            
            # ดักลบแท็ก <br> ทิ้งทั้งหมดเพื่อความสะอาดตา
            clean_output = response.text.replace("<br>", " ").replace("<br/>", " ").replace("<BR>", " ")
            
            status_box.update(label="✅ ตรวจสอบและออกรายงานเสร็จสิ้น!", state="complete", expanded=False)
            
            # แสดงผลรายงานบนหน้าเว็บ
            st.markdown(clean_output)
            
            # ปุ่มดาวน์โหลดรายงานผล
            st.divider()
            st.download_button(
                label="📥 ดาวน์โหลดรายงานผลเป็นไฟล์ข้อความ (.txt)",
                data=clean_output,
                file_name="po_audit_report.txt",
                mime="text/plain"
            )

        except Exception as e:
            status_box.update(label="❌ เกิดข้อผิดพลาดในการประมวลผล", state="error", expanded=True)
            st.error(f"รายละเอียด: {str(e)}")
            
        finally:
            # ลบไฟล์ออกจากเซิร์ฟเวอร์ Gemini ทันทีหลังทำงานเสร็จ
            if uploaded_files and 'client' in locals():
                for name in uploaded_files:
                    try:
                        client.files.delete(name=name)
                    except:
                        pass
