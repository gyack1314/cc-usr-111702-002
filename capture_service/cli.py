"""重放合成的来源交互并展示接收状态、续抓游标与按报告号的追溯。"""
import argparse
import json

from .ledger import blob, connect, receive, receipts, trace
from .transport import Replay
from .view import overview, trace_report


def _drain(db, collection, replay, scope):
    received = 0
    while True:
        request = replay.pending(scope)
        if request is None:
            break
        raw, receipt = replay.request(request['method'], request['url'], collection=scope)
        receive(db, collection, raw, receipt)
        received += 1
    return received


def _replay(db, collection, manifest, collection_scope=None, resume_from=None):
    replay = Replay(manifest)
    if resume_from is not None:
        if not collection_scope:
            raise SystemExit('续抓需要指定来源集合')
        replay.seek(collection_scope, resume_from)
    if collection_scope is not None:
        return replay, _drain(db, collection, replay, collection_scope)
    scopes = replay.scopes()
    if scopes == ['']:
        return replay, _drain(db, collection, replay, None)
    received = 0
    for scope in scopes:
        received += _drain(db, collection, replay, scope)
    return replay, received


def main():
    p = argparse.ArgumentParser()
    p.add_argument('command', choices=['inspect', 'trace', 'cat'])
    p.add_argument('manifest')
    p.add_argument('--db', default=':memory:')
    p.add_argument('--collection', default='lab-investigation')
    p.add_argument('--source-collection', default=None,
                   help='按机构或目录划分的冻结来源集合')
    p.add_argument('--resume-from', type=int, default=None,
                   help='该来源集合的续抓游标')
    p.add_argument('--report', default=None, help='报告号，用于 trace')
    p.add_argument('--institution', default=None)
    p.add_argument('--version', default=None, help='cat 时的实体版本')
    p.add_argument('--entity-type', default=None, help='cat 时的实体类型')
    p.add_argument('--entity-id', default=None, help='cat 时的实体标识')
    p.add_argument('--output', default=None, help='cat 时写出原件字节')
    args = p.parse_args()

    db = connect(args.db)
    try:
        if args.command == 'inspect':
            replay, received = _replay(db, args.collection, args.manifest,
                                       args.source_collection, args.resume_from)
            result = overview(db, args.collection)
            result['source_scopes'] = replay.scopes()
            result['received_this_run'] = received
            result['resume_cursors'] = {scope: replay.position_for(scope)
                                        for scope in replay.scopes()}
            if args.source_collection:
                result['source_collection'] = args.source_collection
                result['next_cursor'] = replay.position_for(args.source_collection)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == 'trace':
            if not args.report:
                raise SystemExit('trace 需要 --report')
            if args.db == ':memory:':
                # 内存库需要先重放来源，追溯才有可读记录。
                _replay(db, args.collection, args.manifest,
                        args.source_collection, args.resume_from)
            result = trace_report(db, args.collection, args.report, args.institution)
            result['receipts'] = receipts(db, args.collection)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            if not (args.entity_type and args.entity_id and args.version):
                raise SystemExit('cat 需要 --entity-type --entity-id --version')
            data, media_type, source = blob(db, args.collection, args.entity_type,
                                            args.entity_id, args.version)
            if args.output:
                with open(args.output, 'wb') as handle:
                    handle.write(data)
                print(json.dumps({'written': args.output, 'bytes': len(data),
                                  'media_type': media_type, 'source_sha256': source},
                                 ensure_ascii=False, indent=2))
            else:
                print(data.decode('utf-8', 'replace'))
    finally:
        db.close()


if __name__ == '__main__':
    main()
