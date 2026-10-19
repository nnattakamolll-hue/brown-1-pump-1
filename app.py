import fitz  # PyMuPDF

def annotate_pdf_corrections(input_pdf_bytes, corrections):
    doc = fitz.open(stream=input_pdf_bytes, filetype="pdf")
    
    for page in doc:
        for item in corrections:
            wrong_text = item.get("wrong", "").strip()
            correct_text = item.get("correct", "").strip()
            
            if not wrong_text or len(wrong_text) < 2:
                continue
                
            # ค้นหาตำแหน่งข้อความเดิมในหน้า PDF (แบบไม่สนตัวพิมพ์เล็ก-ใหญ่)
            text_instances = page.search_for(wrong_text, hit_max=16)
            
            for inst in text_instances:
                # 1. วาดเส้นขีดฆ่าสีแดงทับข้อความเดิม
                strike = page.add_strikeout_annot(inst)
                strike.set_colors(stroke=(1, 0, 0))  # สีแดง
                strike.update()
                
                # 2. แปะ Pop-up Note หรือ Highlight สีเหลืองพร้อมข้อความแก้ไข
                # ใช้ add_text_annot เพื่อสร้างป๊อปอัปโน้ตที่รองรับภาษาไทยได้ชัวร์ 100%
                note_pos = fitz.Point(inst.x1 + 2, inst.y0)
                annot = page.add_text_annot(note_pos, f"แก้ไขเป็น: {correct_text}")
                annot.set_colors(stroke=(1, 0.8, 0)) # ไอคอนสีเหลือง
                annot.update()

    # บันทึกเป็นไบต์
    output_pdf_bytes = doc.write()
    doc.close()
    return output_pdf_bytes
