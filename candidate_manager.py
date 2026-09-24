"""Candidate records backed by reviews; text recognition follows each review mode."""
from recognition_config import resolve_local_ocr_enabled
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import re
import threading
from urllib.parse import quote
import cv2
import numpy as np
from candidate_identity import IDENTITY_FIELDS, merge_name_fields, name_fields, normalize_name, prepare_name_crop


def grade_confirmation_blockers(review):
    """返回成绩确认前仍需处理的题目状态。"""
    blockers = []
    pass_statuses = {"自动通过", "通过"}
    fail_statuses = {"不通过"}
    for item in review.get("objective", []):
        status = item.get("final_status") or item.get("auto_status") or "待复核"
        if status not in pass_statuses | fail_statuses:
            blockers.append("第{}题客观题{}".format(item.get("question", ""), status))
    for item in review.get("items", []):
        status = item.get("manual_status") if item.get("manual_status") in {"通过", "不通过"} else ""
        if not status:
            status = {"AI通过": "通过", "AI不通过": "不通过", "AI需复核": "待复核"}.get(
                item.get("ai_status"), item.get("final_status") or item.get("auto_status") or "待复核"
            )
        if status not in pass_statuses | fail_statuses:
            blockers.append("第{}题{}".format(item.get("question", ""), status))
    ai_status = (review.get("ai_judgment") or {}).get("status")
    if ai_status == "处理中":
        blockers.append("AI判断处理中")
    return blockers

def question_score_records(review):
    """返回导出成绩时使用的逐题得分明细。"""
    records = []
    pass_statuses = {"自动通过", "通过"}
    fail_statuses = {"不通过"}

    def append_items(items, question_type):
        for item in items or []:
            manual_status = item.get("manual_status")
            status = manual_status if manual_status in pass_statuses | fail_statuses else ""
            if not status:
                status = {"AI通过": "通过", "AI不通过": "不通过", "AI需复核": "待复核"}.get(
                    item.get("ai_status"), item.get("final_status") or item.get("auto_status") or "待复核"
                )
            score = item.get("score", 0) or 0
            awarded = item.get("awarded_score")
            if awarded is None:
                awarded = score if status in pass_statuses else 0
            records.append({
                "question": str(item.get("question", "")),
                "question_id": item.get("id") or item.get("question_id", ""),
                "id": item.get("id") or item.get("question_id", ""),
                "session_id": item.get("session_id", ""),
                "section_id": item.get("section_id", ""),
                "major_question": str(item.get("major_question") or ""),
                "type": question_type,
                "score": score,
                "awarded_score": awarded,
                "status": status,
                "score_basis": str(item.get("score_basis") or ""),
            })

    append_items(review.get("objective"), "客观题")
    append_items(review.get("items"), "文字题")
    return records


def valid_id(value):
    value=str(value or '')
    if not re.fullmatch(r'[A-Za-z0-9_-]+',value):raise ValueError('批改编号格式错误')
    return value


def candidate_record(review, root):
    identifier=valid_id(review.get('review_id'))
    blockers=grade_confirmation_blockers(review)
    image=Path(root)/identifier/'output/handwriting/identity/name.png'
    return {'review_id':identifier,'student_id':str(review.get('student_id') or ''),
            'session_id':review.get('session_id', ''),
            'student_name':str(review.get('student_name') or ''),
            'student_name_status':review.get('student_name_status','待识别'),
            'student_name_confidence':review.get('student_name_confidence',0),
            'student_name_source':review.get('student_name_source',''),
            'name_ocr':review.get('name_ocr',''),'name_ocr_error':review.get('name_ocr_error',''),
            'name_recognition_version':review.get('name_recognition_version',0),
            'name_image_url':('/reviews/'+quote(identifier)+'/output/handwriting/identity/name.png') if image.is_file() else '',
            'paper_type':review.get('paper_type',''),'import_id':review.get('import_id',''),
            'label':review.get('batch_label') or ' / '.join(review.get('card_files',[])),
            'score_summary':review.get('score_summary',{}),'question_scores':question_score_records(review),'ai_judgment':review.get('ai_judgment',{}),
            'grade_confirmed':bool(review.get('grade_confirmed')),'grade_confirmed_at':review.get('grade_confirmed_at',''),
            'grade_confirmation_status':('已确认' if review.get('grade_confirmed') else ('待处理' if blockers else '待确认')),
            'grade_blockers':blockers}


class CandidateManager:
    def __init__(self,root,load,write,review_lock,preview_builder,recognizer=None):
        self.root=root;self.load=load;self.write=write;self.review_lock=review_lock
        self.preview_builder=preview_builder;self.recognizer=recognizer
        self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='candidate-name')
        self.lock=threading.RLock();self.future=None
        self.job={'status':'空闲','total':0,'completed':0,'failed':0,'message':''}

    def records(self):
        records=[];skipped=0
        with self.review_lock:
            for path in sorted(Path(self.root()).glob('*/output/review.json'),reverse=True):
                try:
                    review=json.loads(path.read_text(encoding='utf-8'))
                    # Bind to the directory name so cached data cannot point at another record.
                    review['review_id']=path.parent.parent.name
                    records.append(candidate_record(review,self.root()))
                except (OSError,ValueError,TypeError):skipped+=1
        return records,skipped

    def list(self):
        records,skipped=self.records()
        with self.lock:job=dict(self.job)
        return {'ok':True,'candidates':records,'skipped':skipped,'name_job':job}

    def save(self,payload):
        identifier=valid_id(payload.get('review_id'))
        name=normalize_name(payload.get('student_name'))
        if len(name)>40 or (name and not all(c.isalpha() or c in " ·•.'-" for c in name)):
            raise ValueError('姓名请填写 40 字以内的中文或字母')
        sid=str(payload.get('student_id') or '').strip()
        if sid and not re.fullmatch(r'[0-9]{12}',sid):raise ValueError('学号需填写 12 位数字')
        paper=str(payload.get('paper_type') or '').strip().upper()
        if paper not in ('','A','B','C'):raise ValueError('试卷类型请选择 A、B 或 C')
        with self.review_lock:
            _,path,review=self.load(identifier)
            review.setdefault('student_id_ocr',review.get('student_id',''))
            review.update(student_name=name,student_name_status='已确认' if name else '待填写',
                          student_name_source='manual',student_id=sid,paper_type=paper,
                          paper_type_status='人工确认' if paper else '待复核',
                          grade_confirmed=False,grade_confirmed_at='',
                          grade_confirmation_status='待确认',grade_blockers=grade_confirmation_blockers(review))
            self.write(path,review)
            record=candidate_record(review,self.root())
        return {'ok':True,'candidate':record}

    def start(self,payload):
        identifiers=payload.get('review_ids')
        if identifiers is None:
            records,_=self.records()
            identifiers=[r['review_id'] for r in records if not r['name_recognition_version'] and r['student_name_source']!='manual']
        if not isinstance(identifiers,list) or len(identifiers)>1000:raise ValueError('每次最多识别 1000 份答题卡')
        identifiers=list(dict.fromkeys(valid_id(value) for value in identifiers))
        for identifier in identifiers:
            with self.review_lock:self.load(identifier)
        with self.lock:
            if self.future and not self.future.done():return {'ok':True,'name_job':dict(self.job)}
            self.job={'status':'处理中' if identifiers else '已完成','total':len(identifiers),
                      'completed':0,'failed':0,'message':'正在识别姓名…' if identifiers else '姓名记录已更新'}
            if identifiers:self.future=self.executor.submit(self._run,identifiers)
            return {'ok':True,'name_job':dict(self.job)}

    def _run(self,identifiers):
        for identifier in identifiers:
            try:
                with self.lock:self.job['message']='正在识别第 {} / {} 份姓名'.format(self.job['completed']+1,self.job['total'])
                with self.review_lock:
                    _,_,saved_review=self.load(identifier)
                local_ocr=resolve_local_ocr_enabled(saved_review.get('local_ocr_enabled'))
                self.preview_builder(identifier)
                folder=Path(self.root())/identifier/'output/handwriting'
                image=cv2.imdecode(np.frombuffer((folder/'objective_view/page.png').read_bytes(),np.uint8),cv2.IMREAD_GRAYSCALE)
                crop,ratio=prepare_name_crop(image,folder)
                if ratio>=.006:
                    if self.recognizer is None:
                        from exam_review import _recognize_crops
                        if not local_ocr:
                            crop=cv2.imdecode(np.frombuffer((folder/'identity/name.png').read_bytes(),np.uint8),cv2.IMREAD_GRAYSCALE)
                        predictions=_recognize_crops([crop],['姓名'],local_ocr_enabled=local_ocr)
                    else:predictions=self.recognizer([crop],['姓名'])
                    fields=name_fields(predictions[0],ratio)
                else:fields=name_fields(None,ratio)
                fields['student_name_source']='ocr' if local_ocr else 'ai'
                with self.review_lock:
                    _,path,review=self.load(identifier)
                    merge_name_fields(review,fields);self.write(path,review)
                if fields['name_ocr_error']:
                    with self.lock:self.job['failed']+=1
            except Exception as error:
                with self.lock:self.job['failed']+=1
                try:
                    with self.review_lock:
                        _,path,review=self.load(identifier)
                        merge_name_fields(review,{'student_name_status':'识别异常','name_ocr_error':str(error)})
                        self.write(path,review)
                except (OSError,ValueError):pass
            finally:
                with self.lock:self.job['completed']+=1
        with self.lock:
            self.job['status']='已完成'
            self.job['message']='姓名识别完成：{} 份，异常 {} 份'.format(self.job['completed'],self.job['failed'])
