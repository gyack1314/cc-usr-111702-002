"""记录冻结 HTTP 交互，限制请求与授权作用域。

来源步骤可以标注所属机构集合；每个集合有自己的读取游标，因此一个机构的
重试或补抓不会消耗另一个机构的冻结响应。未标注集合时保持原有的单一顺序。
"""
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_SCOPE = ''


def origin(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError('来源地址非法')
    return parsed.scheme + '://' + parsed.netloc.lower()


class Replay:
    def __init__(self, manifest_path):
        path = Path(manifest_path)
        document = json.loads(path.read_text(encoding='utf-8'))
        self.manifest_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        self.root = path.parent.resolve()
        self.allowed = set(document['allowed_origins'])
        self.steps = document['steps']
        self.position = 0
        self.history = []
        self._cursors = {}
        self._pools = {}
        for index, step in enumerate(self.steps):
            scope = step.get('collection') or DEFAULT_SCOPE
            self._pools.setdefault(scope, []).append(index)

    def scopes(self):
        return sorted(self._pools, key=lambda s: (s != DEFAULT_SCOPE, s))

    def position_for(self, collection=None):
        scope = collection or DEFAULT_SCOPE
        return self._cursors.get(scope, self.position if scope == DEFAULT_SCOPE else 0)

    def seek(self, collection, position):
        scope = collection or DEFAULT_SCOPE
        total = len(self._pools.get(scope, []))
        if not isinstance(position, int) or position < 0 or position > total:
            raise ValueError('续抓游标越界')
        self._cursors[scope] = position

    def _step_index(self, collection):
        if collection is None:
            if self.position >= len(self.steps):
                raise EOFError('没有冻结响应')
            index = self.position
            if self.steps[index].get('collection'):
                raise ValueError('来源步骤属于独立集合，需指定集合读取')
            return index, None
        scope = collection or DEFAULT_SCOPE
        pool = self._pools.get(scope)
        if not pool:
            raise ValueError('未知来源集合')
        cursor = self._cursors.get(scope, 0)
        if cursor >= len(pool):
            raise EOFError('该集合没有更多冻结响应')
        return pool[cursor], scope

    def pending(self, collection=None):
        """返回下一步的请求描述；没有可读步骤时为 None。"""
        try:
            index, _ = self._step_index(collection)
        except EOFError:
            return None
        return dict(self.steps[index]['request'])

    def request(self, method, url, body=b'', credential_scope='', collection=None):
        if origin(url) not in self.allowed or credential_scope and credential_scope != origin(url):
            raise ValueError('请求或凭据越界')
        index, scope = self._step_index(collection)
        row = self.steps[index]
        expected = row['request']
        if (method, url, hashlib.sha256(body).hexdigest()) != (
                expected['method'], expected['url'], expected['body_sha256']):
            raise ValueError('请求与冻结交互不符')
        path = (self.root / row['response']['file']).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError('响应文件越界')
        raw = path.read_bytes()
        if len(raw) != row['response']['bytes'] or hashlib.sha256(raw).hexdigest() != row['response']['sha256']:
            raise ValueError('响应字节身份不符')
        if scope is None:
            self.position += 1
        else:
            self._cursors[scope] = self._cursors.get(scope, 0) + 1
        receipt = {'method': method, 'url': url, 'status': row['response']['status'],
                   'body_sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
                   'credential_scope': credential_scope, 'step': index + 1,
                   'collection': scope or (collection or DEFAULT_SCOPE),
                   'media_type': row['response'].get('media_type') or '',
                   'metadata': row['response'].get('metadata') or {}}
        self.history.append(receipt)
        return raw, receipt
