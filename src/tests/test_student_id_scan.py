"""Keep the scanned student ID verbatim, including leading zeroes and readable positions."""
import numpy as np
import pytest
import exam_review
from src.tests.test_candidates import manager_fixture


def scanned_id(monkeypatch, digits, blank_columns=(), close_columns=(), weak_columns=()):
    circles=np.array([[[30+col*40,20+digit*20,6] for col in range(12) for digit in range(10)]],dtype=np.float32)
    monkeypatch.setattr(exam_review.cv2,"HoughCircles",lambda *args,**kwargs:circles)
    def darkness(_image,x,y,_radius):
        col=round((x-80)/40); digit=round((y-320)/20)
        if col in blank_columns:return .02
        if digit==int(digits[col]):return .22 if col in weak_columns else .85
        if col in close_columns and digit==(int(digits[col])+1)%10:return .82
        return .02
    monkeypatch.setattr(exam_review,"_dark_ratio",darkness)
    image=np.full((1684,1191),255,np.uint8)
    return exam_review._read_student_id(image,image)


def test_scanned_id_keeps_leading_zeroes(monkeypatch):
    assert scanned_id(monkeypatch,"002619240110")=="002619240110"


def test_close_marks_use_the_strongest_scanned_digit(monkeypatch):
    assert scanned_id(monkeypatch,"002619240110",close_columns=(4,))=="002619240110"


def test_light_marks_keep_the_scanned_digit(monkeypatch):
    assert scanned_id(monkeypatch,"002619240110",weak_columns=(7,))=="002619240110"


def test_empty_position_keeps_other_scanned_digits(monkeypatch):
    assert scanned_id(monkeypatch,"002619240110",blank_columns=(5,))=="00261?240110"


def test_empty_grid_stays_empty(monkeypatch):
    assert scanned_id(monkeypatch,"002619240110",blank_columns=tuple(range(12)))==""


@pytest.mark.parametrize("student_id",["00261?240110","001234","000123456789"])
def test_saving_identity_preserves_scanned_characters(tmp_path,student_id):
    manager,load,_=manager_fixture(tmp_path)
    before=load("review-01")[2]
    result=manager.save({"review_id":"review-01","student_name":"张三","student_id":student_id,"paper_type":"A"})
    assert result["candidate"]["student_id"]==student_id
    saved=load("review-01")[2]
    assert saved["objective"]==before["objective"]
    assert saved["score_summary"]==before["score_summary"]


def test_printed_blank_reference_keeps_empty_id():
    image=exam_review._reference_pages()[0]
    assert exam_review._read_student_id(image,exam_review._normalize_illumination(image))==""


def test_unlocated_grid_keeps_empty_id(monkeypatch):
    monkeypatch.setattr(exam_review.cv2,"HoughCircles",lambda *args,**kwargs:None)
    image=np.full((1684,1191),255,np.uint8)
    assert exam_review._read_student_id(image,image)==""
