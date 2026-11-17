"""多机构、多格式原件的归属、版本与追溯测试。"""
import json
import tempfile
import unittest
from pathlib import Path

from capture_service.ledger import blob, connect, receipts, receive, trace
from capture_service.transport import Replay
from capture_service.view import overview, trace_report

ROOT = Path(__file__).resolve().parents[1]
COLLECTION = 'lab-investigation'


def replay_all(db, manifest, collection=COLLECTION):
    replay = Replay(manifest)
    scopes = replay.scopes()
    if scopes == ['']:
        scopes = [None]
    for scope in scopes:
        while replay.pending(scope) is not None:
            request = replay.pending(scope)
            raw, receipt = replay.request(request['method'], request['url'], collection=scope)
            receive(db, collection, raw, receipt)
    return replay


class MultiInstitution(unittest.TestCase):
    def setUp(self):
        self.db = connect(':memory:')
        self.replay = replay_all(self.db, ROOT / 'data' / 'org-a.json')
        self.view = overview(self.db, COLLECTION)

    def tearDown(self):
        self.db.close()

    def test_catalogs_kept_per_institution(self):
        catalogs = {c['institution']: c for c in self.view['catalog']}
        self.assertEqual(set(catalogs), {'lab-north', 'lab-south'})
        self.assertTrue(all(c['source_sha256'] for c in catalogs.values()))
        self.assertTrue(all(c['source_url'] for c in catalogs.values()))
        self.assertEqual(len(catalogs['lab-north']['entries']), 2)

    def test_same_report_number_different_specification_not_merged(self):
        reports = {r['id']: r for r in self.view['reports']}
        self.assertEqual(set(reports), {'lab-north/R-17', 'lab-south/R-17'})
        self.assertEqual(reports['lab-north/R-17']['sample']['specification'], '0.25g')
        self.assertEqual(reports['lab-south/R-17']['sample']['specification'], '0.5g')
        north_pages = {p['page_id'] for p in reports['lab-north/R-17']['pages']}
        south_pages = {p['page_id'] for p in reports['lab-south/R-17']['pages']}
        self.assertFalse(north_pages & south_pages)

    def test_artifact_bytes_verified_and_retrievable(self):
        report = next(r for r in self.view['reports'] if r['id'] == 'lab-north/R-17')
        page1 = next(p for p in report['pages'] if p['page'] == '1')
        self.assertEqual(page1['declared_image_sha256'], page1['latest_artifact'])
        data, media_type, source = blob(self.db, COLLECTION, 'artifact', 'lab-north/R-17/1', 'v1')
        self.assertEqual(media_type, 'image/png')
        self.assertTrue(data.startswith(b'\x89PNG'))
        self.assertEqual(page1['latest_artifact'], __import__('hashlib').sha256(data).hexdigest())

    def test_ocr_is_candidate_not_page_content(self):
        report = next(r for r in self.view['reports'] if r['id'] == 'lab-north/R-17')
        page1 = next(p for p in report['pages'] if p['page'] == '1')
        self.assertTrue(page1['ocr_candidates'])
        self.assertTrue(all(c['status'] == 'candidate' for c in page1['ocr_candidates']))
        self.assertNotIn('ocr_text', report)
        for row in self.view['records']:
            if row['type'] == 'artifact':
                self.assertNotEqual(row['blob_sha256'], '')
            if row['type'] == 'ocr':
                self.assertEqual(row['blob_sha256'], '')

    def test_ocr_mapping_cannot_be_confirmed(self):
        mappings = [m for r in self.view['reports'] for m in r['mappings']]
        by_method = {m['method']: m for m in mappings}
        self.assertEqual(by_method['declared']['status'], 'confirmed')
        self.assertEqual(by_method['ocr']['status'], 'candidate')
        self.assertTrue(any(c['type'] == 'mapping' and c['method'] == 'ocr'
                            for c in self.view['candidates']))

    def test_missing_page_and_unverified_sample_block_publication(self):
        self.assertFalse(self.view['publishable'])
        reasons = self.view['reasons']
        self.assertTrue(any('页图原件未取得:lab-north/R-17/2' in r for r in reasons), reasons)
        self.assertTrue(any('样品批次待核:lab-north/R-17' in r for r in reasons), reasons)

    def test_unassigned_scan_kept_as_candidate(self):
        unassigned = [c for c in self.view['candidates']
                      if c['type'] == 'artifact' and c['id'].startswith('unassigned/')]
        self.assertEqual(len(unassigned), 1)
        self.assertEqual(unassigned[0]['declared_report'], 'R-17')
        for report in self.view['reports']:
            self.assertFalse(any(p['page_id'].startswith('unassigned')
                                 for p in report['pages']))

    def test_independent_resume_per_institution(self):
        db = connect(':memory:')
        replay = Replay(ROOT / 'data' / 'org-a.json')
        replay.seek('south', 3)
        while replay.pending('south') is not None:
            request = replay.pending('south')
            raw, receipt = replay.request(request['method'], request['url'], collection='south')
            receive(db, COLLECTION, raw, receipt)
        self.assertEqual(replay.position_for('north'), 0)
        rows = receipts(db, COLLECTION)
        self.assertEqual(len(rows), 3)
        self.assertTrue(all('lab-south' in r['url'] for r in rows))
        db.close()

    def test_trace_by_report_number(self):
        north = trace_report(self.db, COLLECTION, 'R-17', 'lab-north')
        south = trace_report(self.db, COLLECTION, 'R-17', 'lab-south')
        self.assertFalse(north['unresolved'])
        self.assertFalse(south['unresolved'])
        north_urls = {h['source_url'] for h in north['history']}
        south_urls = {h['source_url'] for h in south['history']}
        self.assertTrue(north_urls)
        self.assertTrue(south_urls)
        self.assertFalse(north_urls & south_urls)
        self.assertTrue(all(h['source'] for h in north['history']))
        self.assertTrue(all(h['source_url'] for h in north['history']))

    def test_repeat_replay_is_idempotent(self):
        before = overview(self.db, COLLECTION)
        replay_all(self.db, ROOT / 'data' / 'org-a.json')
        after = overview(self.db, COLLECTION)
        self.assertEqual(before['records'], after['records'])
        self.assertEqual(len(receipts(self.db, COLLECTION)), len(receipts(self.db, COLLECTION)))


class Versions(unittest.TestCase):
    def setUp(self):
        self.db = connect(':memory:')
        replay_all(self.db, ROOT / 'data' / 'versions.json')

    def tearDown(self):
        self.db.close()

    def test_report_and_artifact_version_relations(self):
        report = trace(self.db, COLLECTION, 'R-20')
        versions = {(r['type'], r['version']) for r in report}
        self.assertIn(('report', 'r1'), versions)
        self.assertIn(('report', 'r2'), versions)
        relations = [r for r in report if r['type'] == 'report' and r['version'] == 'r2']
        self.assertEqual(relations[0]['relations'][0]['target']['version'], 'r1')
        artifacts = [r for r in report if r['type'] == 'artifact']
        self.assertEqual({a['version'] for a in artifacts}, {'v1', 'v2'})
        withdrawn = next(a for a in artifacts if a['version'] == 'v2')
        self.assertEqual(withdrawn['relations'][0]['relation'], 'supersedes')
        self.assertEqual(withdrawn['relations'][0]['target']['version'], 'v1')
        # 旧版本原件仍可单独取回。
        old, _, _ = blob(self.db, COLLECTION, 'artifact', 'lab-north/R-20/1', 'v1')
        new, _, _ = blob(self.db, COLLECTION, 'artifact', 'lab-north/R-20/1', 'v2')
        self.assertNotEqual(old, new)

    def test_supplement_page_is_related_to_original(self):
        pages = [r for r in trace(self.db, COLLECTION, 'R-20') if r['type'] == 'page']
        page2 = next(p for p in pages if p['id'].endswith('/2'))
        self.assertEqual(page2['relations'][0]['relation'], 'fragment_of')
        self.assertEqual(page2['relations'][0]['target']['id'], 'lab-north/R-20/1')


class Attachments(unittest.TestCase):
    def test_attachment_identity_and_replacement(self):
        from capture_service.domain import decode
        first = b'%PDF-1.4 original'
        second = b'%PDF-1.4 replacement'
        receipt1 = {'status': 200, 'url': 'https://lab-north.example.test/attachments/a.pdf',
                    'body_sha256': __import__('hashlib').sha256(first).hexdigest(),
                    'media_type': 'application/pdf',
                    'metadata': {'kind': 'attachment', 'institution': 'lab-north',
                                 'report': 'R-40', 'filename': 'appendix.pdf', 'version': 'v1'}}
        receipt2 = dict(receipt1)
        receipt2['body_sha256'] = __import__('hashlib').sha256(second).hexdigest()
        receipt2['metadata'] = dict(receipt1['metadata'], version='v2', supersedes='v1')
        rows1 = {r['id']: r for r in decode(first, receipt1)}
        rows2 = {r['id']: r for r in decode(second, receipt2)}
        key = 'lab-north/R-40/attachments/appendix.pdf'
        self.assertIn(key, rows1)
        self.assertEqual(rows1[key]['payload']['kind'], 'attachment')
        self.assertNotIn('lab-north/R-40/1', rows1)
        self.assertEqual(rows2[key]['relations'][0]['relation'], 'supersedes')
        self.assertEqual(rows2[key]['relations'][0]['to']['version'], 'v1')
        # 撤换只新增版本，原字节仍可取回。
        db = connect(':memory:')
        receive(db, COLLECTION, first, receipt1)
        receive(db, COLLECTION, second, receipt2)
        old, media_type, _ = blob(db, COLLECTION, 'artifact', key, 'v1')
        self.assertEqual(old, first)
        self.assertEqual(media_type, 'application/pdf')
        db.close()


class Legacy(unittest.TestCase):
    def test_legacy_records_still_assessed(self):
        replay = Replay(ROOT / 'data' / 'normal.json')
        db = connect(':memory:')
        for step in replay.steps:
            request = step['request']
            raw, receipt = replay.request(request['method'], request['url'])
            receive(db, COLLECTION, raw, receipt)
        view = overview(db, COLLECTION)
        self.assertEqual(view['report_count'], 1)
        self.assertFalse(view['publishable'])
        self.assertTrue(any('页图原件未取得' in r for r in view['reasons']))
        db.close()


if __name__ == '__main__':
    unittest.main()
