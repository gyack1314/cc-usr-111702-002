"""解释检验机构目录、报告原件、OCR 候选与通告映射。

本模块只把来源正文解释成可写入账本的行，不做归属或推断。报告身份始终是
(机构, 报告号)；样品标识单独保存。OCR 只是候选，不并入页图摘要或报告正文。
缺少机构身份的原件保留为未归属候选，不按药名或报告号硬归批次。
"""
import hashlib
import json

CANDIDATE_METHODS = {'ocr', 'ocr_candidate', 'text_inference', 'name_match'}
MAPPING_STATUS = {'confirmed', 'candidate', 'rejected'}
BINARY_MEDIA = {'application/pdf', 'application/octet-stream'}


def _text(value, default=''):
    if value is None:
        return default
    value = str(value).strip()
    return value or default


def _version(value, default):
    version = _text(value, default)
    if any(ch in version for ch in '/\n\r\t'):
        raise ValueError('版本标识非法')
    return version


def _row(type_, id_, version, payload, blob=None, relations=None, media_type=''):
    return {
        'type': str(type_),
        'id': str(id_),
        'version': str(version),
        'payload': payload,
        'blob': blob,
        'relations': list(relations or []),
        'media_type': media_type or '',
    }


def _ref(type_, id_, version):
    return {'type': type_, 'id': id_, 'version': str(version)}


def _supersedes(type_, id_, version):
    return [{'relation': 'supersedes', 'to': _ref(type_, id_, version)}]


def _institution(d):
    raw = d.get('institution')
    if isinstance(raw, dict):
        ident = _text(raw.get('id') or raw.get('slug') or raw.get('name'))
        name = _text(raw.get('name') or ident)
        return ident, name
    ident = _text(d.get('institution_id') or d.get('institutionId') or raw)
    name = _text(d.get('institution_name') or d.get('institutionName') or ident)
    return ident, name


def _sample(raw):
    """只保留声明值；批次无法核实时不补全、不并入报告身份。"""
    if isinstance(raw, dict):
        ident = _text(raw.get('id') or raw.get('sample') or raw.get('label'))
        specification = _text(raw.get('specification') or raw.get('spec'))
        batch = _text(raw.get('batch'))
        status = _text(raw.get('batch_status')) or ('unverified' if batch else 'unknown')
        return {'id': ident, 'specification': specification,
                'batch': batch, 'batch_status': status}
    return {'id': _text(raw), 'specification': '', 'batch': '', 'batch_status': 'unknown'}


def _sample_unverified(sample):
    """样品标识缺失，或声明了批次但批次未经核实时，保持待核。"""
    if not sample.get('id'):
        return True
    if sample.get('batch') and sample.get('batch_status') not in ('verified', 'confirmed'):
        return True
    return sample.get('batch_status') in ('unverified', 'candidate', 'uncertain')


def _page_id(institution, number, page):
    base = institution + '/' + number if institution else number
    return base + '/' + str(page)


def _pages(value):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError('报告页序非法')
    pages = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, str)):
            raise ValueError('报告页序非法')
        page = _text(item)
        if not page:
            raise ValueError('报告页序非法')
        pages.append(page)
    return pages


def _catalog(d):
    institution, name = _institution(d)
    if not institution:
        raise ValueError('目录缺少机构身份')
    entries = d.get('catalog') if 'catalog' in d else d.get('entries')
    if not isinstance(entries, list):
        raise ValueError('机构目录非法')
    kept = []
    for entry in entries:
        if not isinstance(entry, dict) or not _text(entry.get('report') or entry.get('report_number')):
            raise ValueError('目录条目非法')
        kept.append(dict(entry))
    canonical = json.dumps(kept, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    version = _version(d.get('revision'), '') or hashlib.sha256(canonical.encode()).hexdigest()[:16]
    payload = {
        'institution': institution,
        'institution_name': name,
        'catalog_url': _text(d.get('catalog_url') or d.get('url')),
        'observed_at': _text(d.get('observed_at')),
        'entries': kept,
    }
    return [_row('catalog', institution + '/catalog', version, payload)]


def _report(d, institution, name):
    number = _text(d.get('report') or d.get('report_number'))
    if not number:
        raise ValueError('报告缺少报告号')
    report_id = _page_id(institution, number, '').rstrip('/')
    revision = _version(d.get('revision'), '')
    if not revision:
        raise ValueError('报告缺少修订')
    sample = _sample(d.get('sample'))
    items = []
    for item in d.get('items') or []:
        if isinstance(item, dict):
            items.append({
                'item': _text(item.get('item') or item.get('name') or item.get('code')),
                'result': _text(item.get('result') or item.get('value')),
                'unit': _text(item.get('unit')),
                'method': _text(item.get('method')),
            })
        else:
            items.append({'item': _text(item), 'result': '', 'unit': '', 'method': ''})
    relations = []
    prior = d.get('supersedes') or d.get('previous_revision') or d.get('replaces')
    if prior:
        relations = _supersedes('report', report_id, _version(prior, ''))
    payload = {
        'institution': institution,
        'institution_name': name,
        'report_number': number,
        'revision': revision,
        'sample': sample,
        'pages': _pages(d.get('pages')),
        'items': items,
        'attachments': d.get('attachments') or [],
    }
    return [_row('report', report_id, revision, payload, relations=relations)]


def _page(d, institution):
    number = _text(d.get('report') or d.get('report_number'))
    if not number:
        raise ValueError('报告页缺少报告号')
    revision = _version(d.get('revision'), '')
    if not revision:
        raise ValueError('报告页缺少修订')
    page = d.get('page')
    if isinstance(page, bool) or not isinstance(page, (int, str)):
        raise ValueError('报告页序非法')
    page = _text(page)
    if not page:
        raise ValueError('报告页序非法')
    page_id = _page_id(institution, number, page)
    sample = _sample(d.get('sample'))
    fragment = _text(d.get('fragment'))
    fragment_of = _text(d.get('fragment_of'))
    payload = {
        'institution': institution,
        'report_number': number,
        'page': page,
        'revision': revision,
        'sample': sample,
        'declared_image_sha256': _text(d.get('image_sha256') or d.get('sha256')),
        'declared_bytes': d.get('bytes'),
        'declared_media_type': _text(d.get('media_type')),
        'fragment_of': fragment_of,
        'fragment': fragment,
    }
    relations = []
    if fragment_of:
        # 补页必须指向它补充的原页；目标标识与页标识使用同一机构前缀。
        target = fragment_of
        if institution and not target.startswith(institution + '/'):
            target = institution + '/' + target
        relations = [{'relation': 'fragment_of',
                      'to': _ref('page', target,
                                 _version(d.get('fragment_of_revision'), ''))}]
    rows = [_row('page', page_id, revision, payload, relations=relations)]
    ocr_text = _text(d.get('ocr_text'))
    if ocr_text:
        rows.append(_row('ocr', page_id, 'page@' + revision, {
            'page': page_id,
            'page_revision': revision,
            'source': 'page_document',
            'text': ocr_text,
            'method': _text(d.get('ocr_method')) or 'ocr',
            'status': 'candidate',
            'declared_image_sha256': payload['declared_image_sha256'],
        }))
    return rows


def _mapping(d, institution):
    source = d.get('source') if isinstance(d.get('source'), dict) else {}
    target = d.get('target') if isinstance(d.get('target'), dict) else {}
    if not source:
        source = {'report': d.get('report'), 'item': d.get('source_item') or d.get('item'),
                  'page': d.get('page')}
    if not target:
        target = {'notice': d.get('notice') or d.get('notice_item'),
                  'item': d.get('notice_item') or d.get('item')}
    number = _text(source.get('report'))
    item = _text(source.get('item'))
    notice = _text(target.get('notice') or d.get('notice_id'))
    if not number or not item or not notice:
        raise ValueError('映射缺少报告项目或通告项目')
    method = _text(d.get('method')) or 'declared'
    status = _text(d.get('status')) or 'confirmed'
    if method in CANDIDATE_METHODS:
        # 识别或名称匹配永远不能自动升级为已确认。
        status = 'candidate'
    if status not in MAPPING_STATUS:
        raise ValueError('映射状态非法')
    notice_institution = _text(target.get('institution') or d.get('notice_institution'))
    base = institution + '/' if institution else ''
    page_ref = _text(source.get('page') or d.get('page'))
    source_id = base + number + '/' + item + ('@' + page_ref if page_ref else '')
    target_item = _text(target.get('item') or d.get('notice_item'))
    target_id = notice + '/' + target_item
    payload = {
        'institution': institution,
        'source': {'report': number, 'item': item, 'page': page_ref,
                   'result': _text(source.get('result') or d.get('result'))},
        'target': {'notice': notice, 'item': target_item,
                   'institution': notice_institution},
        'method': method,
        'status': status,
        'evidence': _text(d.get('evidence')),
    }
    if d.get('ocr_text') is not None:
        payload['ocr_candidate'] = _text(d.get('ocr_text'))
    version = _version(d.get('revision') or d.get('mapping_version'), '1')
    return [_row('mapping', source_id + '->' + target_id, version, payload,
                 relations=_supersedes('mapping', source_id + '->' + target_id,
                                       _version(d['supersedes'], ''))
                 if d.get('supersedes') else None)]


def _binary(raw, receipt):
    meta = receipt.get('metadata') or {}
    if not isinstance(meta, dict):
        raise ValueError('收据元数据非法')
    media_type = _text(receipt.get('media_type') or meta.get('media_type'))
    if not (media_type.startswith('image/') or media_type in BINARY_MEDIA):
        raise ValueError('报告资料非法')
    digest = hashlib.sha256(raw).hexdigest()
    if receipt.get('body_sha256') and receipt['body_sha256'] != digest:
        raise ValueError('原件字节与收据不一致')
    institution = _text(meta.get('institution'))
    number = _text(meta.get('report') or meta.get('report_number'))
    page = meta.get('page')
    filename = _text(meta.get('filename')) or receipt['url'].rsplit('/', 1)[-1]
    kind = _text(meta.get('kind')) or ('attachment' if meta.get('attachment') else 'page')
    ocr_text = _text(meta.get('ocr_text'))
    version = _version(meta.get('version'), '0')
    payload = {
        'kind': kind,
        'byte_sha256': digest,
        'bytes': len(raw),
        'media_type': media_type,
        'filename': filename,
        'institution': institution,
        'report_number': number,
        'page': '' if page is None else str(page),
        'sample': _sample(meta.get('sample')),
        'ocr_candidate': ocr_text,
        'ocr_status': 'candidate' if ocr_text else '',
    }
    if not number or not institution:
        # 报告号或机构无法核实：保留未归属原件，不并入任何报告页。
        return [_row('artifact', 'unassigned/' + digest, version, payload, blob=raw,
                     media_type=media_type)]
    if kind == 'attachment':
        safe = filename.replace('\\', '/').rsplit('/', 1)[-1]
        base = (institution + '/' + number + '/attachments/' + safe).replace('//', '/')
    else:
        base = _page_id(institution, number, page)
    relations = []
    if meta.get('supersedes'):
        relations = _supersedes('artifact', base, _version(meta['supersedes'], ''))
    rows = [_row('artifact', base, version, payload, blob=raw, media_type=media_type,
                 relations=relations)]
    if ocr_text:
        rows.append(_row('ocr', base, 'artifact@' + version, {
            'page': base,
            'page_revision': version,
            'source': 'artifact',
            'text': ocr_text,
            'method': _text(meta.get('ocr_method')) or 'ocr',
            'status': 'candidate',
            'artifact_sha256': digest,
        }))
    return rows


def decode(raw, receipt):
    try:
        d = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _binary(raw, receipt)
    if not isinstance(d, dict):
        raise ValueError('报告资料非法')
    if 'catalog' in d or 'entries' in d:
        return _catalog(d)
    institution, name = _institution(d)
    if 'page' in d or 'fragment' in d:
        return _page(d, institution)
    if 'notice' in d or 'notice_item' in d or 'source' in d and 'target' in d:
        return _mapping(d, institution)
    if 'report' in d or 'report_number' in d or 'revision' in d:
        return _report(d, institution, name)
    raise ValueError('报告资料非法')


def _latest(rows, type_):
    """同一实体按版本保留最后一条可见记录；旧版本仍留在 rows 中供追溯。"""
    latest = {}
    order = []
    for row in rows:
        if row['type'] != type_:
            continue
        if row['id'] not in latest:
            order.append(row['id'])
        latest[row['id']] = row
    return [(i, latest[i]) for i in order]


def _group(rows, type_):
    grouped = {}
    for row in rows:
        if row['type'] == type_:
            grouped.setdefault(row['id'], []).append(row)
    return grouped


def report_key(report_number, institution):
    return (institution + '/' + report_number) if institution else report_number


def page_key(report_number, page, institution):
    return report_key(report_number, institution) + '/' + str(page)


def resolve_page_id(reports_by_number, number, page, institution):
    """优先使用带机构身份的页标识；只有报告号在范围内唯一时才回退旧式标识。

    回退依据是页本身声明的报告号，不是药名或样品猜测；报告号跨机构重复时
    保持未解析，交给候选列表，避免串案。
    """
    exact = page_key(number, page, institution)
    if not institution:
        return exact if len(reports_by_number.get(number, set())) <= 1 else None
    return exact


def _number_owners(reports):
    owners = {}
    for _, report in reports:
        payload = report['payload']
        number = payload.get('report_number')
        if number:
            owners.setdefault(number, set()).add(payload.get('institution') or '')
    return owners


def assess(rows):
    reports = _latest(rows, 'report')
    pages = _group(rows, 'page')
    artifacts = _group(rows, 'artifact')
    owners = _number_owners(reports)
    mappings = _latest(rows, 'mapping')
    ocr = _group(rows, 'ocr')
    reasons = []
    if not reports:
        reasons.append('尚无报告记录')
    institution_ids = sorted({r['payload'].get('institution') for _, r in reports if r['payload'].get('institution')})
    for report_id, report in reports:
        payload = report['payload']
        number = payload.get('report_number')
        sample = payload.get('sample') or {}
        if not sample.get('id'):
            reasons.append('样品身份无法核实:' + report_id)
        elif _sample_unverified(sample):
            reasons.append('样品批次待核:' + report_id)
        if not payload.get('pages'):
            reasons.append('报告未声明页序:' + report_id)
        if not payload.get('institution'):
            reasons.append('机构身份无法核实:' + report_id)
        for page in payload.get('pages') or []:
            page_id = page_key(number, page, payload.get('institution') or '')
            page_rows = pages.get(page_id) or []
            if not page_rows and len(owners.get(number, set())) == 1:
                # 旧式页目录未声明机构；该报告号在采集范围内唯一时才回退。
                page_id = page_key(number, page, '')
                page_rows = pages.get(page_id) or []
            artifact_rows = artifacts.get(page_id) or []
            if not page_rows:
                reasons.append('报告缺页:' + page_key(number, page, payload.get('institution') or ''))
            if not artifact_rows:
                reasons.append('页图原件未取得:' + page_key(number, page, payload.get('institution') or ''))
                continue
            artifact = artifact_rows[-1]['payload']
            declared = _text(page_rows[-1]['payload'].get('declared_image_sha256'))
            actual = _text(artifact.get('byte_sha256'))
            if declared and actual and declared != actual:
                reasons.append('页图摘要不符:' + page_id)
            artifact_sample = artifact.get('sample') or {}
            if artifact_sample.get('id') and sample.get('id') and artifact_sample['id'] != sample['id']:
                reasons.append('样品身份不一致:' + page_id)
            if not artifact_sample.get('id'):
                reasons.append('原件样品身份无法核实:' + page_id)
        for mapping_id, mapping in mappings:
            if mapping['payload'].get('institution') == payload.get('institution') and mapping['payload'].get('source', {}).get('report') == number:
                if mapping['payload'].get('status') == 'candidate':
                    reasons.append('通告映射仍为候选:' + mapping_id)
                elif mapping['payload'].get('status') == 'rejected':
                    reasons.append('通告映射已被否决:' + mapping_id)
    return {
        'publishable': not reasons,
        'reasons': reasons,
        'report_count': len(reports),
        'institutions': institution_ids,
        'mapping_count': len(mappings),
        'ocr_candidate_count': sum(len(v) for v in ocr.values()),
        'artifact_count': sum(len(v) for v in artifacts.values()),
    }
