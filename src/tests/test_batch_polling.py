"""Batch progress reads are bounded, workspace-scoped, and free of media work."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import scan_ui
from src.tests.test_saas_workspaces import ALICE, http_service, store
from workspace_context import workspace_scope


def seed(batch_id='batch-1', review_id='review-1'):
    batch={'batch_id':batch_id,'status':'已完成','total':1,'completed':1,'failed':0,
           'reviews':[{'status':'已完成','review_id':review_id,'label':'答卷'}]}
    _,path=scan_ui._batch_path(batch_id)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(batch),encoding='utf-8')
    report=scan_ui.REVIEW_ROOT/review_id/'output/review.json'
    report.parent.mkdir(parents=True,exist_ok=True)
    report.write_text(json.dumps({'review_id':review_id,'student_name':'测试考生',
        'score_summary':{'total_score':3,'possible_score':5},
        'ai_judgment':{'status':'处理中','completed_groups':1,'group_count':2}}),encoding='utf-8')
    return report


def forbidden(*args,**kwargs):
    raise AssertionError('progress polling invoked a full review, media rebuild, identity lookup, or COS upload')


def test_progress_read_skips_full_review_media_and_identity(monkeypatch,tmp_path):
    monkeypatch.setattr(scan_ui,'REVIEW_ROOT',tmp_path)
    report=seed();before=report.read_bytes()
    monkeypatch.setattr(scan_ui,'read_review_status',forbidden)
    monkeypatch.setattr(scan_ui,'_write_review',forbidden)
    monkeypatch.setattr(scan_ui,'CANDIDATE_MANAGER',SimpleNamespace(resolve_identity=forbidden))
    result=scan_ui.read_batch_status('batch-1')
    assert result['ai_processing']==1
    assert result['reviews'][0]['score_summary']['total_score']==3
    assert result['reviews'][0]['report_url']=='/reviews/review-1/output/review.json'
    assert report.read_bytes()==before


def test_bulk_progress_deduplicates_and_isolates_missing_batches(monkeypatch,tmp_path):
    monkeypatch.setattr(scan_ui,'REVIEW_ROOT',tmp_path)
    seed('batch-1','review-1');seed('batch-2','review-2')
    result=scan_ui.read_batch_statuses('batch-1,batch-2,batch-1,missing')
    assert [b['batch_id'] for b in result['batches']]==['batch-1','batch-2']
    assert result['errors']==[{'batch_id':'missing','status':400,'error':'批量任务不存在'}]


@pytest.mark.parametrize('ids',['',',',','.join('batch-'+str(i) for i in range(101)),'../outside','valid,bad/id'])
def test_bulk_progress_validates_bound_and_ids(ids):
    with pytest.raises(ValueError):scan_ui.read_batch_statuses(ids)


def test_bulk_progress_http_preserves_membership_boundary(http_service,monkeypatch):
    client,store,root=http_service
    workspace=store.create(ALICE,'Combined progress')
    with workspace_scope(workspace):seed('batch-1','review-1');seed('batch-2','review-2')
    monkeypatch.setattr(scan_ui,'read_review_status',forbidden)
    monkeypatch.setattr(scan_ui,'CANDIDATE_MANAGER',SimpleNamespace(resolve_identity=forbidden))
    route='/api/w/'+workspace['id']+'/api/review/batch/status?batch_ids=batch-1,batch-2,missing'
    response=client.get(route,headers={'Cookie':'alice'})
    assert response.status_code==200,response.text
    data=response.json()
    assert data['ok'] is True
    assert [b['batch_id'] for b in data['batches']]==['batch-1','batch-2']
    assert data['errors'][0]['batch_id']=='missing'
    assert client.get(route,headers={'Cookie':'bob'}).status_code==404
    assert client.get(route).status_code==401
