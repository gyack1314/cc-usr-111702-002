"""按本项目约定展示已接收资料、候选归属与明确缺口。

展示层只读账本：版本关系、原件摘要和 OCR 候选分别列出，OCR 文本不进入
报告字段。未归属原件与候选映射单独作为 candidates 暴露，便于人工复核。
"""
import hashlib

from .domain import assess
from .ledger import entities, receipts, relations


def _receipt_index(c, collection):
    return {r['body_sha256']: r for r in receipts(c, collection)}


def _versions(rows, index):
    grouped = {}
    for row in rows:
        grouped.setdefault(row['id'], []).append({
            'version': row['version'],
            'source_sha256': row['source'],
            'url': index.get(row['source'], {}).get('url', ''),
            'received_seq': row['received_seq'],
            'media_type': row['media_type'],
            'blob_sha256': row['blob_sha256'],
        })
    return grouped


def overview(c, collection):
    rows = entities(c, collection)
    result = assess(rows)
    index = _receipt_index(c, collection)
    by_type = {}
    for row in rows:
        by_type.setdefault(row['type'], []).append(row)

    reports = []
    report_order = []
    latest_report = {}
    for row in by_type.get('report', []):
        key = row['id']
        if key not in latest_report:
            report_order.append(key)
        latest_report[key] = row
    page_ids = {}
    for row in by_type.get('page', []):
        page_ids.setdefault(row['id'], []).append(row)
    artifact_ids = {}
    for row in by_type.get('artifact', []):
        artifact_ids.setdefault(row['id'], []).append(row)
    ocr_ids = {}
    for row in by_type.get('ocr', []):
        ocr_ids.setdefault(row['id'], []).append(row)
    mapping_ids = {}
    for row in by_type.get('mapping', []):
        mapping_ids.setdefault(row['id'], []).append(row)

    for report_id in report_order:
        row = latest_report[report_id]
        payload = row['payload']
        institution = payload.get('institution', '')
        number = payload.get('report_number') or report_id.rsplit('/', 1)[-1]
        prefix = (institution + '/' + number) if institution else report_id
        page_states = []
        for page in payload.get('pages') or []:
            page_id = prefix + '/' + str(page)
            artifacts = artifact_ids.get(page_id, [])
            candidates = ocr_ids.get(page_id, [])
            latest = artifacts[-1] if artifacts else None
            declared = ''
            if page_ids.get(page_id):
                declared = page_ids[page_id][-1]['payload'].get('declared_image_sha256', '')
            page_states.append({
                'page': str(page),
                'page_id': page_id,
                'declared_image_sha256': declared,
                'artifact_versions': [{
                    'version': a['version'],
                    'byte_sha256': a['payload'].get('byte_sha256'),
                    'media_type': a['media_type'],
                    'received_seq': a['received_seq'],
                    'relations': relations(c, collection, 'artifact', a['id'], a['version']),
                } for a in artifacts],
                'latest_artifact': latest['payload'].get('byte_sha256') if latest else '',
                'ocr_candidates': [{'version': o['version'], 'status': 'candidate',
                                    'source': o['payload'].get('source', ''),
                                    'text': o['payload'].get('text', '')} for o in candidates],
            })
        reports.append({
            'id': report_id,
            'institution': institution,
            'report_number': number,
            'latest_version': row['version'],
            'versions': [{
                'version': v['version'],
                'source_sha256': v['source'],
                'relations': relations(c, collection, 'report', v['id'], v['version']),
            } for v in by_type.get('report', []) if v['id'] == report_id],
            'sample': payload.get('sample') or {},
            'items': payload.get('items') or [],
            'pages': page_states,
            'mappings': [{
                'id': m['id'], 'version': m['version'],
                'status': m['payload'].get('status'),
                'method': m['payload'].get('method'),
                'target': m['payload'].get('target'),
                'source_item': m['payload'].get('source', {}).get('item'),
            } for m in [x for values in mapping_ids.values() for x in values]
                if m['payload'].get('institution') == institution
                and m['payload'].get('source', {}).get('report') == number],
        })

    candidates = []
    for artifact in by_type.get('artifact', []):
        if artifact['type'] == 'artifact' and artifact['id'].startswith('unassigned/'):
            candidates.append({
                'type': 'artifact', 'id': artifact['id'], 'version': artifact['version'],
                'byte_sha256': artifact['payload'].get('byte_sha256'),
                'declared_report': artifact['payload'].get('report_number', ''),
                'reason': '报告号或机构无法核实，保留为未归属原件',
                'source_sha256': artifact['source'],
            })
    for mapping in by_type.get('mapping', []):
        if mapping['payload'].get('status') == 'candidate':
            candidates.append({
                'type': 'mapping', 'id': mapping['id'], 'version': mapping['version'],
                'method': mapping['payload'].get('method'),
                'reason': '通告映射仅为候选，需人工核对原件',
                'source_sha256': mapping['source'],
            })
    for ocr in by_type.get('ocr', []):
        candidates.append({
            'type': 'ocr', 'id': ocr['id'], 'version': ocr['version'],
            'status': 'candidate',
            'source': ocr['payload'].get('source', ''),
            'reason': '文字识别结果，不能替代原页或作为无误差事实',
            'source_sha256': ocr['source'],
        })

    result['collection'] = collection
    result['records'] = [{
        'type': r['type'], 'id': r['id'], 'version': r['version'],
        'source_sha256': r['source'], 'media_type': r['media_type'],
        'blob_sha256': r['blob_sha256'],
        'relations': relations(c, collection, r['type'], r['id'], r['version']),
    } for r in rows]
    result['reports'] = reports
    result['candidates'] = candidates
    result['catalog'] = [{
        'id': r['id'], 'version': r['version'],
        'institution': r['payload'].get('institution'),
        'institution_name': r['payload'].get('institution_name'),
        'entries': r['payload'].get('entries'),
        'source_url': r['payload'].get('catalog_url'),
        'source_sha256': r['source'],
    } for r in by_type.get('catalog', [])]
    return result


def trace_report(c, collection, report_number, institution=None):
    """复查者按报告号追溯每次取得的原件版本、收据与版本关系。"""
    index = _receipt_index(c, collection)
    rows = entities(c, collection)
    history = []
    for row in rows:
        payload = row['payload']
        if row['type'] == 'mapping':
            number = payload.get('source', {}).get('report', '')
        else:
            number = payload.get('report_number', '')
        if number != report_number:
            continue
        if institution and payload.get('institution') != institution:
            continue
        receipt = index.get(row['source'], {})
        history.append({
            'type': row['type'], 'id': row['id'], 'version': row['version'],
            'source': row['source'], 'source_url': receipt.get('url', ''),
            'received_seq': row['received_seq'],
            'media_type': row['media_type'],
            'blob_sha256': row['blob_sha256'],
            'relations': relations(c, collection, row['type'], row['id'], row['version']),
            'ocr': row['payload'].get('text') if row['type'] == 'ocr' else None,
            'declared_image_sha256': (
                row['payload'].get('declared_image_sha256')
                if row['type'] == 'page' else None),
            'byte_sha256': (row['payload'].get('byte_sha256')
                            if row['type'] == 'artifact' else None),
        })
    return {
        'collection': collection,
        'report_number': report_number,
        'institution': institution,
        'history': history,
        'receipts': [r for r in receipts(c, collection)
                     if r['body_sha256'] in {h['source'] for h in history}],
        'unresolved': not history,
    }
