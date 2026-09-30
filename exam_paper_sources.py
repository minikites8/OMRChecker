"""Resolve separate A/B/C imports within an explicitly named exam family."""
from __future__ import annotations
import re
import unicodedata


def paper_identity(imported):
    name = unicodedata.normalize('NFKC', str(imported.get('name') or '')).strip()
    match = re.search(r'(?<![A-Za-z0-9])([ABC])\s*卷\s*[)\]】]?\s*$', name, re.I)
    named = match.group(1).upper() if match else ''
    declared = str(imported.get('paper_type') or '').strip().upper()
    kind = declared if declared in {'A', 'B', 'C'} else named
    family = ''
    if match and kind == named:
        family = re.sub(r'\s+', '', name[:match.start()].rstrip(' ([【-_—')).casefold()
    return kind, family


def single_paper_type(imported):
    return '' if imported.get('paper_variants') else paper_identity(imported)[0]


def regrade_sources(import_id, imported):
    """Keep embedded variants; expose independently imported siblings by exact family."""
    import scan_ui as service
    variants = imported.get('paper_variants') or {}
    name = imported.get('name') or import_id
    if variants:
        return [{'value':kind, 'import_id':import_id, 'paper_type':kind, 'name':name,
                 'label':kind+' 卷 · '+name} for kind in variants]
    kind, family = paper_identity(imported)
    sources = [{'value':import_id+':'+kind, 'import_id':import_id, 'paper_type':kind, 'name':name,
                'label':(kind+' 卷 · ' if kind else '当前试卷答案 · ')+name}]
    if family:
        for item in service.list_exam_imports(limit=10000):
            other_kind, other_family = paper_identity(item)
            if item['import_id'] == import_id or item.get('variant_count') or not other_kind or other_family != family:
                continue
            sources.append({'value':item['import_id']+':'+other_kind, 'import_id':item['import_id'],
                            'paper_type':other_kind, 'name':item['name'], 'label':other_kind+' 卷 · '+item['name']})
    for source in sources:
        if sum(other['paper_type'] == source['paper_type'] for other in sources) > 1:
            source['label'] += ' · '+source['import_id']
    return sorted(sources, key=lambda source:(source['paper_type'], source['import_id']))


def resolve_regrade_source(import_id, imported, review, paper_type=None, answer_import_id=None):
    """Return the actual reference import and selected variant, before any mutation."""
    import scan_ui as service
    kind = str(paper_type if paper_type is not None else review.get('paper_type') or review.get('answer_paper_type') or '').strip().upper()
    variants = imported.get('paper_variants') or {}
    current_kind = single_paper_type(imported)
    if answer_import_id is not None:
        if not isinstance(answer_import_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', answer_import_id):
            raise ValueError('请选择有效的参考试卷')
        matching = [source for source in regrade_sources(import_id, imported)
                    if source['import_id'] == answer_import_id and (not kind or source['paper_type'] == kind)]
        if len(matching) != 1:
            raise ValueError('请选择本场考试中与卷型对应的参考试卷')
        target = matching[0]
        _id, _path, actual = service._review_import({'import_id':target['import_id']})
        actual_variants = actual.get('paper_variants') or {}
        actual_kind = target['paper_type']
        if (actual_variants and actual_kind not in actual_variants) or (not actual_variants and single_paper_type(actual) != actual_kind):
            raise ValueError('参考试卷卷型已更新，请重新选择')
        return _id, actual, actual_kind
    if variants:
        if kind not in variants:
            raise ValueError('请先选择该试卷已导入的卷型')
        return import_id, imported, kind
    if current_kind and kind and current_kind != kind:
        candidates = [source for source in regrade_sources(import_id, imported) if source['paper_type'] == kind]
        if len(candidates) != 1:
            raise ValueError('请在重新批改窗口明确选择对应卷型的参考试卷')
        return resolve_regrade_source(import_id, imported, review, kind, candidates[0]['import_id'])
    return import_id, imported, current_kind
