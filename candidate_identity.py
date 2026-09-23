"""Read the handwritten name on page one of the two-page answer-card template."""
from pathlib import Path
import math
import re
import unicodedata
import cv2
import numpy as np

NAME_VERSION = 1
# PDF coordinates from the top-left; the printed label stays outside this box.
NAME_RECT = (447.0, 139.0, 550.0, 175.0)
IDENTITY_FIELDS = ('student_name','name_ocr','student_name_confidence','student_name_status',
                   'student_name_source','name_ocr_error','name_recognition_version')


def prepare_name_crop(image, image_dir=None):
    if image is None or image.size == 0:
        raise ValueError('姓名区域图像为空')
    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    sx, sy = gray.shape[1] / 595.2756, gray.shape[0] / 841.8898
    left,top,right,bottom = NAME_RECT
    raw = gray[max(0,round(top*sy)):min(gray.shape[0],round(bottom*sy)),
               max(0,round(left*sx)):min(gray.shape[1],round(right*sx))].copy()
    if raw.size == 0:
        raise ValueError('姓名区域尺寸异常')
    # Keep the original crop for review; remove the underline only in the OCR input.
    mask = np.where(raw < 160,255,0).astype(np.uint8)
    lines = cv2.morphologyEx(mask,cv2.MORPH_OPEN,np.ones((1,max(20,round(raw.shape[1]*.55))),np.uint8))
    cleaned = raw.copy(); cleaned[cv2.dilate(lines,np.ones((2,1),np.uint8))>0] = 255
    ink = np.where(cleaned < 170,255,0).astype(np.uint8)
    count,labels,stats,_ = cv2.connectedComponentsWithStats(ink,8)
    area = sum(int(stats[i,cv2.CC_STAT_AREA]) for i in range(1,count) if stats[i,cv2.CC_STAT_AREA]>=max(4,round(sx*sy)))
    ratio = area / raw.size
    if image_dir is not None:
        dest=Path(image_dir)/'identity';dest.mkdir(parents=True,exist_ok=True)
        ok,encoded=cv2.imencode('.png',raw)
        if ok:(dest/'name.png').write_bytes(encoded.tobytes())
    return cleaned, ratio


def normalize_name(value):
    text=unicodedata.normalize('NFKC',str(value or '')).strip()
    text=re.sub(r'^姓\s*名\s*[:：]?\s*','',text)
    text=re.sub(r'[_\-—:：|]+$','',text).strip()
    if re.search(r'[\u3400-\u9fff]',text):text=re.sub(r'\s+','',text)
    else:text=' '.join(text.split())
    return text


def name_fields(prediction=None, ink_ratio=0.0):
    raw=str(getattr(prediction,'text','') or '')
    text=normalize_name(raw) if ink_ratio>=.006 else ''
    error=str(getattr(prediction,'error','') or '')
    try:confidence=float(getattr(prediction,'confidence',0.0))
    except (TypeError,ValueError):confidence=0.0
    confidence=min(1.0,max(0.0,confidence)) if math.isfinite(confidence) else 0.0
    valid=bool(text) and len(text)<=40 and all(c.isalpha() or c in " ·•.'-" for c in text)
    if not valid:text=''
    status='待确认' if text else '识别异常' if error else '待填写' if ink_ratio<.006 else '待确认'
    return {'student_name':text,'name_ocr':raw if ink_ratio>=.006 else '',
            'student_name_confidence':round(confidence,4) if text else 0.0,
            'student_name_status':status,'student_name_source':'ocr',
            'name_ocr_error':error,'name_recognition_version':NAME_VERSION}


def merge_name_fields(review, fields):
    # A background OCR result preserves a teacher's current identity correction.
    manual=review.get('student_name_source')=='manual'
    keep={key:review.get(key) for key in ('student_name','student_name_status','student_name_source')}
    review.update({key:value for key,value in fields.items() if key in IDENTITY_FIELDS})
    if manual:review.update(keep)
    return review
