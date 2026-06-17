import cv2
import os
import numpy as np
import uuid
import re
import sys
import json
from PIL import Image, ExifTags
from multiprocessing import Pool, cpu_count
from functools import partial

def natural_sort_key(s):
    """เรียงไฟล์ 1, 2, 10 ได้ถูกต้อง"""
    return [int(text) if text.isdigit() else text.lower() 
            for text in re.split('([0-9]+)', s)]

def fix_exif_orientation(pil_image):
    """
    แก้ไข Orientation ของรูปภาพตาม EXIF (Optimized & Modern)
    """
    try:
        # ใช้ getexif() API ใหม่แทน _getexif()
        exif = pil_image.getexif()
        if exif is None:
            return pil_image
        
        # 0x0112 คือ tag ID ของ Orientation
        orientation = exif.get(0x0112)
        
        # หมุนรูปตาม EXIF value (รองรับครบ 1-8)
        # 1: Normal (ไม่ต้องทำอะไร)
        if orientation == 3:
            pil_image = pil_image.rotate(180, expand=True)
        elif orientation == 6:
            pil_image = pil_image.rotate(270, expand=True)
        elif orientation == 8:
            pil_image = pil_image.rotate(90, expand=True)
        elif orientation == 2:
            pil_image = pil_image.transpose(Image.FLIP_LEFT_RIGHT)
        elif orientation == 4:
            pil_image = pil_image.rotate(180, expand=True).transpose(Image.FLIP_LEFT_RIGHT)
        elif orientation == 5:
            pil_image = pil_image.rotate(270, expand=True).transpose(Image.FLIP_LEFT_RIGHT)
        elif orientation == 7:
            pil_image = pil_image.rotate(90, expand=True).transpose(Image.FLIP_LEFT_RIGHT)
            
    except Exception:
        # กรณีเกิด error ใดๆ ให้คืนรูปเดิมกลับไป
        pass
    
    return pil_image


def imread_unicode(path, max_size_mb=50):
    """
    อ่านรูปภาพรองรับภาษาไทย + จำกัดขนาดไฟล์ + แก้ EXIF Orientation
    """
    try:
        # เช็คขนาดไฟล์ก่อนอ่าน
        file_size_mb = os.path.getsize(path) / (1024 * 1024)
        if file_size_mb > max_size_mb:
            print(f"⚠️ ไฟล์ใหญ่เกินไป ({file_size_mb:.1f}MB): {os.path.basename(path)}")
            return None
        
        # อ่านด้วย PIL แบบปลอดภัย (Context Manager)
        with Image.open(path) as pil_image:
            # ต้อง copy() เพราะถ้าออกจาก with แล้วไฟล์จะปิด
            pil_image.load() 
            pil_image = fix_exif_orientation(pil_image)
            
            # แปลง PIL → OpenCV (RGB → BGR)
            # ใช้ np.asarray จะเร็วกว่า np.array เล็กน้อยในบางกรณี
            img = cv2.cvtColor(np.asarray(pil_image), cv2.COLOR_RGB2BGR)
        
        if img is None:
            print(f"⚠️ Decode ล้มเหลว: {os.path.basename(path)}")
        return img
            
    except Exception as e:
        print(f"⚠️ อ่านไฟล์ไม่ได้: {os.path.basename(path)} ({e})")
        return None

def multiscale_match(img, gray_template, tH, tW, threshold=0.8):
    """
    Optimized Template Matching
    
    ปรับปรุง:
    - เริ่มจากขนาด 1.0 (normal) ก่อน → เจอเร็วขึ้น
    - ใช้ INTER_LINEAR (เร็วกว่า INTER_AREA 30-40%)
    - Early stopping ที่ 0.95
    """
    # ถ้าภาพเป็นสี ให้แปลงเป็น grayscale
    if len(img.shape) == 3:
        gray_img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        gray_img = img
        
    best_score = -1
    
    # OPTIMIZATION: เริ่มจาก 1.0 ลงมา 0.2 แล้วขึ้น 1.5 
    # (เพราะส่วนใหญ่รูปจะอยู่ขนาดปกติ)
    scales_down = np.linspace(1.0, 0.2, 8)  # 1.0 → 0.2
    scales_up = np.linspace(1.1, 1.5, 7)    # 1.1 → 1.5
    scales = np.concatenate([scales_down, scales_up])
    
    for scale in scales:
        resized_h = int(gray_img.shape[0] * scale)
        resized_w = int(gray_img.shape[1] * scale)
        
        # ข้ามถ้ารูปเล็กกว่า template
        if resized_h < tH or resized_w < tW:
            continue
        
        # ใช้ INTER_LINEAR เร็วกว่า INTER_AREA มาก
        resized = cv2.resize(gray_img, (resized_w, resized_h), 
                            interpolation=cv2.INTER_LINEAR)
        
        try:
            res = cv2.matchTemplate(resized, gray_template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(res)
            
            if max_val > best_score:
                best_score = max_val
            
            # Early stopping
            if best_score > 0.95:
                break
                
        except cv2.error:
            continue
    
    return best_score

def clear_line():
    """ลบบรรทัดปัจจุบันบน terminal"""
    sys.stdout.write('\r' + ' ' * 80 + '\r')
    sys.stdout.flush()

def safe_rename_with_rollback(folder_path, all_files, best_match_filename):
    """
    เปลี่ยนชื่อไฟล์แบบปลอดภัย พร้อม Rollback ที่สมบูรณ์
    
    กลยุทธ์:
    1. Rename ไฟล์เป้าหมายเดิม → temp (ไม่ลบ!)
    2. Rename source files → temp
    3. Rename temp → final names
    4. ลบ temp ของไฟล์เป้าหมายเดิม (ถ้าสำเร็จ)
    5. ถ้า error → rollback ทั้งหมด
    """
    temp_map = []
    backup_map = []  # เก็บไฟล์เดิมที่ถูกแทนที่
    
    try:
        # === STEP 0: Backup existing target files ===
        print("   [0/3] ตรวจสอบไฟล์ที่อาจชนกัน...")
        
        counter = 2
        potential_targets = []
        
        for filename in all_files:
            name, ext = os.path.splitext(filename)
            
            if filename == best_match_filename:
                final_name = f"1{ext}"
            else:
                final_name = f"รูปรถประกันที่{counter}{ext}"
                counter += 1
            
            potential_targets.append(final_name)
        
        # หาไฟล์ที่จะถูกเขียนทับ
        for target_name in set(potential_targets):
            target_path = os.path.join(folder_path, target_name)
            
            if os.path.exists(target_path) and target_name not in all_files:
                # มีไฟล์ชื่อนี้อยู่ก่อนแล้ว แต่ไม่ใช่ไฟล์ที่เรากำลังจะ rename
                backup_name = f"backup_{uuid.uuid4().hex}{os.path.splitext(target_name)[1]}"
                backup_path = os.path.join(folder_path, backup_name)
                
                print(f"      ⚠️ สำรองไฟล์: {target_name} → {backup_name}")
                os.rename(target_path, backup_path)
                
                backup_map.append({
                    'backup_path': backup_path,
                    'original_path': target_path
                })
        
        # === STEP 1: Rename all source files to temp ===
        print("   [1/3] สร้างไฟล์ชั่วคราว...")
        for filename in all_files:
            original_path = os.path.join(folder_path, filename)
            name, ext = os.path.splitext(filename)
            
            temp_name = f"temp_{uuid.uuid4().hex}{ext}"
            temp_path = os.path.join(folder_path, temp_name)
            
            os.rename(original_path, temp_path)
            
            temp_map.append({
                'temp_path': temp_path,
                'original_path': original_path,
                'original_name': filename,
                'original_ext': ext,
                'is_target': (filename == best_match_filename)
            })
        
        # === STEP 2: Rename temp to final names ===
        print("   [2/3] เปลี่ยนชื่อเป็นชื่อใหม่...")
        counter = 2
        rename_map = {}  # {ชื่อใหม่: ชื่อเดิม} เก็บไว้ให้ไล่หมวดรูปย้อนได้
        for item in temp_map:
            ext = item['original_ext']

            if item['is_target']:
                final_name = f"1{ext}"
            else:
                final_name = f"รูปรถประกัน{counter}{ext}"
                counter += 1

            final_path = os.path.join(folder_path, final_name)

            # ตอนนี้ไม่ควรมีไฟล์ชนเพราะ backup ไว้แล้ว
            # แต่เช็คอีกรอบเผื่อกรณีพิเศษ
            if os.path.exists(final_path):
                emergency_backup = f"emergency_{uuid.uuid4().hex}{ext}"
                emergency_path = os.path.join(folder_path, emergency_backup)
                os.rename(final_path, emergency_path)
                backup_map.append({
                    'backup_path': emergency_path,
                    'original_path': final_path
                })

            os.rename(item['temp_path'], final_path)
            rename_map[final_name] = item['original_name']
        
        # === STEP 3: Delete backups (only if successful) ===
        print("   [3/3] ลบไฟล์สำรอง...")
        for backup in backup_map:
            if os.path.exists(backup['backup_path']):
                os.remove(backup['backup_path'])

        # เก็บ map ชื่อใหม่→ชื่อเดิม ไว้ให้แกลเลอรีจัดกลุ่มตามหมวด (best-effort)
        try:
            with open(os.path.join(folder_path, "_rename_map.json"),
                      "w", encoding="utf-8") as f:
                json.dump(rename_map, f, ensure_ascii=False)
        except Exception:
            pass

        return True
        
    except Exception as e:
        print(f"\n❌ เกิดข้อผิดพลาด: {e}")
        print("🔄 กำลัง Rollback ทั้งหมด...")
        
        # === ROLLBACK PHASE ===
        rollback_success = True
        
        # 1. Restore temp files → original names
        for item in temp_map:
            try:
                if os.path.exists(item['temp_path']):
                    # ถ้ามี final file อยู่แล้ว ลบทิ้งก่อน
                    if os.path.exists(item['original_path']):
                        os.remove(item['original_path'])
                    os.rename(item['temp_path'], item['original_path'])
            except Exception as rollback_err:
                print(f"   ⚠️ Rollback temp ล้มเหลว: {item['original_name']} ({rollback_err})")
                rollback_success = False
        
        # 2. Restore backup files → original names
        for backup in backup_map:
            try:
                if os.path.exists(backup['backup_path']):
                    # ลบไฟล์ที่อาจถูกสร้างขึ้นมาใหม่
                    if os.path.exists(backup['original_path']):
                        os.remove(backup['original_path'])
                    os.rename(backup['backup_path'], backup['original_path'])
            except Exception as rollback_err:
                print(f"   ⚠️ Rollback backup ล้มเหลว: {backup['original_path']} ({rollback_err})")
                rollback_success = False
        
        if rollback_success:
            print("✅ Rollback สำเร็จ - ไฟล์ทั้งหมดถูกคืนสู่สถานะเดิม")
        else:
            print("⚠️ Rollback บางส่วนล้มเหลว - กรุณาตรวจสอบโฟลเดอร์")
        
        return False


def process_single_image(args):
    """
    ฟังก์ชันสำหรับ Multiprocessing - ประมวลผลรูปภาพทีละรูป
    """
    filename, folder_path, gray_template, tH, tW, threshold, i, total = args
    
    img_path = os.path.join(folder_path, filename)
    img = imread_unicode(img_path)
    
    if img is None:
        return None, -1, i, total, filename
    
    # ส่ง gray_template ไปโดยตรง ไม่ต้องแปลงซ้ำใน multiscale_match
    score = multiscale_match(img, gray_template, tH, tW, threshold)
    del img
    
    # Return แค่ครั้งเดียว (ลด data transfer loopback)
    return filename, score, i, total


def process_images_pro(folder_path, template_path, threshold=0.8, use_multiprocessing=True):
    """
    ค้นหารูปที่ตรงกับ Template มากที่สุด (ปรับปรุงความปลอดภัย + Multiprocessing)
    
    Parameters:
    -----------
    folder_path : str
        พาธของโฟลเดอร์รูปภาพ
    template_path : str
        พาธของรูป Template
    threshold : float
        เกณฑ์คะแนนขั้นต่ำ (0.0-1.0)
    use_multiprocessing : bool
        ใช้ multiprocessing หรือไม่ (Default: True)
    """
    if not os.path.exists(folder_path) or not os.path.isdir(folder_path):
        print("❌ ไม่พบโฟลเดอร์รูปภาพ (Path not found or not a directory)")
        return
    
    # Resolve absolute path for safety
    folder_path = os.path.abspath(folder_path)

    # อ่าน Template ครั้งเดียว
    template = imread_unicode(template_path)
    if template is None:
        print("❌ อ่านไฟล์ Template ไม่ได้")
        return
    
    gray_template = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    tH, tW = gray_template.shape[:2]
    del template

    # หาไฟล์ทั้งหมด (Validating filenames)
    valid_ext = ('.jpg', '.jpeg', '.png', '.bmp')
    all_files = sorted(
        [f for f in os.listdir(folder_path) 
         if f.lower().endswith(valid_ext) and os.path.isfile(os.path.join(folder_path, f))], 
        key=natural_sort_key
    )
    
    if not all_files:
        print("❌ ไม่พบไฟล์รูปภาพในโฟลเดอร์")
        return

    best_overall_score = -1
    best_match_filename = None
    total = len(all_files)
    
    num_cores = cpu_count()
    print(f"🔍 กำลังสแกน {total} รูป (Threshold: {threshold})")
    
    # ใช้ Multiprocessing เมื่อมีไฟล์มากกว่าจำนวน CPU cores เพื่อความคุ้มค่า Overhead
    should_use_mp = use_multiprocessing and (total > num_cores) and (total > 4)
    
    if should_use_mp:
        print(f"⚡ ใช้ {num_cores} CPU cores")
    print("="*50)

    # === SCANNING PHASE ===
    if should_use_mp:
        # เตรียม arguments สำหรับแต่ละรูป
        args_list = [
            (filename, folder_path, gray_template, tH, tW, threshold, i, total)
            for i, filename in enumerate(all_files, 1)
        ]
        
        # ใช้ Pool สำหรับ parallel processing
        with Pool(processes=num_cores) as pool:
            results = pool.map(process_single_image, args_list)
        
        # วิเคราะห์ผลลัพธ์
        for result in results:
            if result[0] is None:  # ข้ามไฟล์ที่อ่านไม่ได้
                continue
            
            filename, score, i, total = result
            
            if score > threshold and score > best_overall_score:
                best_overall_score = score
                best_match_filename = filename
                clear_line()
                print(f"🎯 [{i}/{total}] พบผู้นำใหม่: {filename} (Score: {score:.4f})")
    
    else:
        # Sequential processing (แบบเดิม)
        for i, filename in enumerate(all_files, 1):
            img_path = os.path.join(folder_path, filename)
            img = imread_unicode(img_path)
            
            if img is None:
                continue

            progress = f"[{i}/{total}] {filename[:30]:30s}"
            print(progress, end='\r')

            score = multiscale_match(img, gray_template, tH, tW, threshold)
            del img
            
            if score > threshold and score > best_overall_score:
                best_overall_score = score
                best_match_filename = filename
                clear_line()
                print(f"🎯 [{i}/{total}] พบผู้นำใหม่: {filename} (Score: {score:.4f})")

    clear_line()
    print("="*50)
    
    # === RESULT ===
    if best_match_filename:
        print(f"🏆 รูปที่เหมาะสมที่สุด: {best_match_filename}")
        print(f"📊 Score: {best_overall_score:.4f}")
        print("\n🔄 กำลังเปลี่ยนชื่อไฟล์...")
        
        success = safe_rename_with_rollback(folder_path, all_files, best_match_filename)
        
        if success:
            print("✨ เสร็จสมบูรณ์!")
        else:
            print("❌ การเปลี่ยนชื่อล้มเหลว (แต่ไฟล์ยังปลอดภัย)")
    else:
        print(f"❌ ไม่พบรูปที่คะแนนเกิน {threshold}")
        if best_overall_score > 0:
            print(f"   (คะแนนสูงสุดที่เจอ: {best_overall_score:.4f})")

# ==================================================
# USAGE (รันเดี่ยวๆ จะใช้โฟลเดอร์ของโปรเจกต์เอง ไม่ hardcode path เครื่อง)
# ==================================================
if __name__ == "__main__":
    BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    FOLDER = os.path.join(BASE, 'downloaded_images')
    TEMPLATE = os.path.join(BASE, 'templates', 'check.jpg')

    process_images_pro(FOLDER, TEMPLATE, threshold=0.75)