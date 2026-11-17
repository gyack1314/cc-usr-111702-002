"""将已接收原文、来源化实体、原件字节和版本关系写入同一事务。

账本只追加，不覆盖：同一 (机构, 报告号, 修订) 的不同正文视为冲突并整笔回滚；
补页、撤换附件等以新版本加 supersedes 关系保存，旧版本仍可按报告号追溯。
"""
import hashlib
import json
import sqlite3

from .domain import decode

SCHEMA = """
CREATE TABLE IF NOT EXISTS receipts(
  collection TEXT NOT NULL, body_sha256 TEXT NOT NULL, source_url TEXT NOT NULL,
  status INTEGER NOT NULL, raw BLOB NOT NULL,
  method TEXT NOT NULL DEFAULT '', media_type TEXT NOT NULL DEFAULT '',
  metadata_json TEXT NOT NULL DEFAULT '{}', received_seq INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(collection,body_sha256));
CREATE TABLE IF NOT EXISTS entities(
  collection TEXT NOT NULL, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
  version TEXT NOT NULL, receipt_sha256 TEXT NOT NULL, payload_json TEXT NOT NULL,
  blob BLOB, media_type TEXT NOT NULL DEFAULT '', received_seq INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(collection,entity_type,entity_id,version),
  FOREIGN KEY(collection,receipt_sha256) REFERENCES receipts(collection,body_sha256));
CREATE TABLE IF NOT EXISTS entity_relations(
  collection TEXT NOT NULL, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
  version TEXT NOT NULL, relation TEXT NOT NULL,
  target_type TEXT NOT NULL, target_id TEXT NOT NULL, target_version TEXT NOT NULL,
  PRIMARY KEY(collection,entity_type,entity_id,version,relation,target_type,target_id,target_version));
CREATE INDEX IF NOT EXISTS entities_by_report ON entities(collection,entity_type,entity_id);
"""

# 旧库可能由更早版本建立；补齐新增列，避免读取时失败。
MIGRATIONS = {
    'receipts': {
        'method': "TEXT NOT NULL DEFAULT ''",
        'media_type': "TEXT NOT NULL DEFAULT ''",
        'metadata_json': "TEXT NOT NULL DEFAULT '{}'",
        'received_seq': 'INTEGER NOT NULL DEFAULT 0',
    },
    'entities': {
        'blob': 'BLOB',
        'media_type': "TEXT NOT NULL DEFAULT ''",
        'received_seq': 'INTEGER NOT NULL DEFAULT 0',
    },
}


def _migrate(c):
    for table, columns in MIGRATIONS.items():
        present = {row[1] for row in c.execute('PRAGMA table_info(' + table + ')')}
        if not present:
            continue
        for name, declaration in columns.items():
            if name not in present:
                c.execute('ALTER TABLE ' + table + ' ADD COLUMN ' + name + ' ' + declaration)


def connect(path):
    c = sqlite3.connect(path)
    c.execute('PRAGMA foreign_keys=ON')
    c.executescript(SCHEMA)
    _migrate(c)
    return c


def _next_seq(c, collection):
    row = c.execute('SELECT COALESCE(MAX(received_seq),0)+1 FROM receipts WHERE collection=?',
                    (collection,)).fetchone()
    return int(row[0])


def _encode_metadata(receipt):
    meta = receipt.get('metadata') or {}
    if not isinstance(meta, dict):
        raise ValueError('收据元数据非法')
    return json.dumps(meta, ensure_ascii=False, sort_keys=True)


def receive(c, collection, raw, receipt):
    if receipt['status'] != 200:
        raise ValueError('来源响应非成功正文')
    digest = hashlib.sha256(raw).hexdigest()
    if digest != receipt['body_sha256']:
        raise ValueError('响应收据与原文不一致')
    rows = decode(raw, receipt)
    metadata_json = _encode_metadata(receipt)
    with c:
        seq = _next_seq(c, collection)
        prior_receipt = c.execute(
            'SELECT source_url,status,raw,media_type,metadata_json FROM receipts WHERE collection=? AND body_sha256=?',
            (collection, digest)).fetchone()
        if prior_receipt and prior_receipt != (receipt['url'], receipt['status'], raw,
                                               receipt.get('media_type') or '', metadata_json):
            raise ValueError('同一原文摘要对应的收据发生冲突')
        c.execute('INSERT OR IGNORE INTO receipts VALUES(?,?,?,?,?,?,?,?,?)',
                  (collection, digest, receipt['url'], receipt['status'], raw,
                   receipt.get('method', ''), receipt.get('media_type') or '', metadata_json, seq))
        for row in rows:
            payload = json.dumps(row['payload'], ensure_ascii=False, sort_keys=True)
            key = (collection, row['type'], row['id'], row['version'])
            prior = c.execute(
                'SELECT receipt_sha256,payload_json,blob,media_type FROM entities '
                'WHERE collection=? AND entity_type=? AND entity_id=? AND version=?', key).fetchone()
            if prior and prior != (digest, payload, row['blob'], row['media_type']):
                raise ValueError('同一实体版本的原文发生冲突')
            c.execute('INSERT OR IGNORE INTO entities VALUES(?,?,?,?,?,?,?,?,?)',
                      (*key, digest, payload, row['blob'], row['media_type'], seq))
            for relation in row['relations']:
                target = relation['to']
                c.execute('INSERT OR IGNORE INTO entity_relations VALUES(?,?,?,?,?,?,?,?)',
                          (collection, row['type'], row['id'], row['version'],
                           relation['relation'], target['type'], target['id'],
                           target['version']))
    return digest


def entities(c, collection):
    """按接收顺序返回实体；不含原件字节，只给字节摘要与来源。"""
    return [{'type': row[0], 'id': row[1], 'version': row[2], 'source': row[3],
             'payload': json.loads(row[4]), 'media_type': row[5],
             'blob_sha256': hashlib.sha256(row[6]).hexdigest() if row[6] is not None else '',
             'received_seq': row[7]}
            for row in c.execute(
                'SELECT entity_type,entity_id,version,receipt_sha256,payload_json,media_type,blob,received_seq '
                'FROM entities WHERE collection=? ORDER BY received_seq,entity_type,entity_id,version',
                (collection,))]


def blob(c, collection, entity_type, entity_id, version):
    """复查者按实体身份取回当次取得的原件字节。"""
    row = c.execute(
        'SELECT blob,media_type,receipt_sha256 FROM entities '
        'WHERE collection=? AND entity_type=? AND entity_id=? AND version=?',
        (collection, entity_type, entity_id, version)).fetchone()
    if not row or row[0] is None:
        raise KeyError('没有该版本的原件字节')
    return row[0], row[1], row[2]


def relations(c, collection, entity_type, entity_id, version=None):
    sql = ('SELECT relation,target_type,target_id,target_version FROM entity_relations '
           'WHERE collection=? AND entity_type=? AND entity_id=?')
    params = [collection, entity_type, entity_id]
    if version is not None:
        sql += ' AND version=?'
        params.append(version)
    return [{'relation': row[0], 'target': {'type': row[1], 'id': row[2], 'version': row[3]}}
            for row in c.execute(sql + ' ORDER BY relation,target_id,target_version', params)]


def receipts(c, collection, url=None, body_sha256=None):
    sql = ('SELECT body_sha256,source_url,status,method,media_type,metadata_json,received_seq '
           'FROM receipts WHERE collection=?')
    params = [collection]
    if url is not None:
        sql += ' AND source_url=?'
        params.append(url)
    if body_sha256 is not None:
        sql += ' AND body_sha256=?'
        params.append(body_sha256)
    return [{'body_sha256': row[0], 'url': row[1], 'status': row[2], 'method': row[3],
             'media_type': row[4], 'metadata': json.loads(row[5]), 'seq': row[6]}
            for row in c.execute(sql + ' ORDER BY received_seq', params)]


def trace(c, collection, report_number, institution=None):
    """按报告号（可再限定机构）列出每次取得的实体版本和来源收据。"""
    sql = ('SELECT entity_type,entity_id,version,receipt_sha256,payload_json,media_type,blob,received_seq '
           'FROM entities WHERE collection=? AND '
           '(entity_id=? OR entity_id LIKE ? OR entity_id LIKE ?)')
    # 报告实体以 <机构>/<报告号> 或旧式 <报告号> 命名；页、原件和映射在其下加层级。
    exact = (institution + '/' + report_number) if institution else report_number
    params = [collection, exact, '%/' + report_number, '%/' + report_number + '/%']
    rows = []
    for row in c.execute(sql + ' ORDER BY received_seq,entity_type,entity_id,version', params):
        payload = json.loads(row[4])
        if payload.get('report_number') != report_number:
            continue
        if institution and payload.get('institution') != institution:
            continue
        rows.append({
            'type': row[0], 'id': row[1], 'version': row[2],
            'source': row[3], 'media_type': row[5],
            'blob_sha256': hashlib.sha256(row[6]).hexdigest() if row[6] is not None else '',
            'payload': payload,
            'relations': relations(c, collection, row[0], row[1], row[2]),
        })
    return rows
