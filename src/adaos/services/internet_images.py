"""Node-owned, task-scoped image retrieval. No arbitrary file or HTTP executor."""
from __future__ import annotations

import hashlib
import http.client
import io
import ipaddress
import json
from pathlib import Path
import secrets
import socket
import sqlite3
import ssl
import time
from urllib.parse import urljoin, urlsplit, urlunsplit
import warnings
from contextlib import contextmanager

from PIL import Image, ImageFile
from adaos.services.artifact_pipeline.storage import atomic_write_bytes

MAX_BYTES = 10 * 1024 * 1024
TASK_BYTES = 50 * 1024 * 1024
TASK_FILES = 12
FORMATS = {'JPEG': ('image/jpeg', 'jpg'), 'PNG': ('image/png', 'png'), 'WEBP': ('image/webp', 'webp')}


def verify_image(raw):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_BYTES:
        raise ValueError('image_size_exceeded')
    if ImageFile.LOAD_TRUNCATED_IMAGES:
        raise ValueError('strict_image_decoding_required')
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(raw)) as im:
            if im.format not in FORMATS or getattr(im, 'n_frames', 1) != 1:
                raise ValueError('only_static_jpeg_png_webp')
            w, h = im.size
            if not (0 < w <= 8192 and 0 < h <= 8192 and w*h <= 16_000_000):
                raise ValueError('image_dimensions_exceeded')
            mime, extension = FORMATS[im.format]
            im.verify()
        with Image.open(io.BytesIO(raw)) as im:
            im.load()
    return dict(media_type=mime, extension=extension, width=w, height=h, size_bytes=len(raw),
                digest='sha256:'+hashlib.sha256(raw).hexdigest())


def public_target(url, resolver=socket.getaddrinfo):
    if not isinstance(url, str) or len(url) > 4096 or any(c.isspace() or ord(c) < 32 for c in url) or '\\' in url:
        raise ValueError('public_https_image_url_required')
    p = urlsplit(url)
    if p.scheme != 'https' or not p.hostname or p.username is not None or p.password is not None or p.port not in (None, 443):
        raise ValueError('public_https_image_url_required')
    host = p.hostname.encode('idna').decode('ascii').lower().rstrip('.')
    if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')) or '%' in host:
        raise ValueError('non_public_image_host')
    records = resolver(host, 443, type=socket.SOCK_STREAM)
    ips = sorted({r[4][0] for r in records})
    if not ips:
        raise ValueError('image_host_not_resolved')
    for value in ips:
        address = ipaddress.ip_address(value)
        address = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped else address
        if not address.is_global or address.is_multicast:
            raise ValueError('non_public_image_host')
    normalized = urlunsplit(('https', '['+host+']' if ':' in host else host, p.path or '/', p.query, ''))
    return normalized, host, ips[0]


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address, timeout):
        super().__init__(host, 443, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        # Connect to the admitted address without a second DNS resolution. TLS
        # still verifies the original hostname; environment proxies are unused.
        sock = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


def download_image(url):
    deadline = time.monotonic()+30
    for attempt in range(4):
        url, host, address = public_target(url)
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            raise TimeoutError('image_download_timeout')
        conn = _PinnedHTTPS(host, address, min(remaining, 10))
        try:
            p = urlsplit(url)
            conn.request('GET', p.path+('?' + p.query if p.query else ''), headers={
                'Accept': 'image/jpeg,image/png,image/webp', 'Accept-Encoding': 'identity',
                'User-Agent': 'AdaOS-ImageImport/1'})
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                if attempt == 3 or not response.getheader('Location'):
                    raise ValueError('image_redirect_limit')
                url = urljoin(url, response.getheader('Location'))
                continue
            if response.status != 200:
                raise ValueError(f'image_http_{response.status}')
            if response.getheader('Content-Encoding', 'identity').lower() != 'identity':
                raise ValueError('encoded_image_response_unsupported')
            length = response.getheader('Content-Length')
            if length is not None and (not length.isdigit() or not 0 < int(length) <= MAX_BYTES):
                raise ValueError('image_size_exceeded')
            chunks, size = [], 0
            while True:
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('image_download_timeout')
                if conn.sock:
                    conn.sock.settimeout(min(remaining, 10))
                chunk = response.read1(min(65536, MAX_BYTES+1-size))
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError('image_size_exceeded')
                chunks.append(chunk)
            raw = b''.join(chunks)
            metadata = verify_image(raw)
            declared = response.getheader('Content-Type', '').split(';')[0].lower().strip()
            if declared and declared not in (metadata['media_type'], 'application/octet-stream'):
                raise ValueError('image_content_type_mismatch')
            return raw, {**metadata, 'final_url': url}
        finally:
            conn.close()
    raise ValueError('image_redirect_limit')


class InternetImages:
    """Authority state is shared by consumers; every grant and object is scoped."""
    def __init__(self, root, *, downloader=download_image):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.downloader = downloader
        with self.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS settings(id TEXT PRIMARY KEY,value TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS grants(id TEXT PRIMARY KEY,scope TEXT NOT NULL,expires REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS images(id TEXT PRIMARY KEY,grant_id TEXT NOT NULL,request_id TEXT NOT NULL,url TEXT NOT NULL,status TEXT NOT NULL,body TEXT NOT NULL,UNIQUE(grant_id,request_id));''')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.root/'images.sqlite3', timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def policy(self):
        with self.db() as db:
            row = db.execute("SELECT value FROM settings WHERE id='mode'").fetchone()
        return {'mode': row[0] if row else 'ask', 'formats': ['JPEG','PNG','WebP'],
                'max_file_bytes': MAX_BYTES, 'max_task_bytes': TASK_BYTES, 'max_task_files': TASK_FILES}

    def set_policy(self, mode):
        if mode not in ('disabled', 'ask'):
            raise ValueError('invalid_image_policy')
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES('mode',?)", (mode,))
            if mode == 'disabled':
                db.execute('UPDATE grants SET expires=0')
        return self.policy()

    def grant(self, scope, *, confirmed):
        if confirmed is not True or self.policy()['mode'] == 'disabled':
            raise PermissionError('image_task_consent_required_or_disabled')
        if not isinstance(scope, str) or not 1 <= len(scope) <= 1500:
            raise ValueError('image_scope_required')
        identity, expires = 'img-grant-'+secrets.token_hex(24), time.time()+3600
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self._expire(db)
            if db.execute('SELECT count(*) FROM grants').fetchone()[0] >= 5000:
                raise ValueError('image_audit_capacity_exceeded')
            db.execute('INSERT INTO grants VALUES(?,?,?)', (identity, scope, expires))
        return {'grant_id': identity, 'expires_at': expires, **self.policy()}

    def _grant(self, db, scope, grant_id):
        row = db.execute('SELECT * FROM grants WHERE id=? AND scope=?', (grant_id, scope)).fetchone()
        if self.policy()['mode'] == 'disabled' or row is None or row['expires'] <= time.time():
            raise PermissionError('image_task_grant_expired_or_denied')

    def task_status(self, scope, grant_id):
        with self.db() as db:
            try:
                self._grant(db, scope, grant_id)
            except PermissionError:
                return {'enabled':False}
            row = db.execute('SELECT expires FROM grants WHERE id=?',(grant_id,)).fetchone()
        return {'enabled':True, 'expires_at':row[0]}

    def revoke(self, scope, grant_id):
        with self.db() as db:
            db.execute('UPDATE grants SET expires=0 WHERE id=? AND scope=?',(grant_id,scope))

    def _expire(self, db):
        rows = db.execute("SELECT i.id,i.body FROM images i JOIN grants g ON g.id=i.grant_id WHERE i.status='completed' AND g.expires<=?",(time.time(),)).fetchall()
        for row in rows:
            digest = json.loads(row['body'])['digest']
            live = db.execute("SELECT i.body FROM images i JOIN grants g ON g.id=i.grant_id WHERE i.status='completed' AND g.expires>?",(time.time(),)).fetchall()
            if not any(json.loads(r[0])['digest']==digest for r in live):
                (self.root/'objects'/digest[7:]).unlink(missing_ok=True)
            db.execute("UPDATE images SET status='expired' WHERE id=?",(row['id'],))
        # Interrupted downloads retain their audit row but must not occupy the
        # node's four concurrent slots forever after their grants expire.
        db.execute("UPDATE images SET status='expired' WHERE status='fetching' "
                   "AND grant_id IN (SELECT id FROM grants WHERE expires<=?)", (time.time(),))

    def fetch(self, scope, grant_id, request_id, url):
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 200:
            raise ValueError('image_request_id_required')
        if not isinstance(url, str) or not 1 <= len(url) <= 4096:
            raise ValueError('image_url_required')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self._grant(db, scope, grant_id)
            row = db.execute('SELECT * FROM images WHERE grant_id=? AND request_id=?', (grant_id,request_id)).fetchone()
            if row:
                if row['url'] != url:
                    raise ValueError('image_request_conflict')
                if row['status'] != 'completed':
                    raise ValueError('image_fetch_unresolved_use_new_request')
                return json.loads(row['body'])
            rows = db.execute('SELECT status,body FROM images WHERE grant_id=?', (grant_id,)).fetchall()
            reserved = sum(json.loads(r['body']).get('size_bytes', MAX_BYTES) for r in rows)
            if len(rows) >= TASK_FILES or reserved+MAX_BYTES > TASK_BYTES:
                raise ValueError('image_task_quota_exceeded')
            active = db.execute("SELECT status,body FROM images WHERE status IN ('fetching','completed')").fetchall()
            if sum(r['status']=='fetching' for r in active) >= 4:
                raise ValueError('image_node_concurrency_limit')
            if sum(json.loads(r['body']).get('size_bytes',MAX_BYTES) for r in active)+MAX_BYTES > 256*1024*1024:
                raise ValueError('image_node_storage_budget_exceeded')
            identity = 'image-fetch-'+secrets.token_hex(20)
            db.execute('INSERT INTO images VALUES(?,?,?,?,?,?)', (identity,grant_id,request_id,url,'fetching','{}'))
        try:
            raw, metadata = self.downloader(url)
            # Enforce the invariant even with a future transport implementation.
            checked = verify_image(raw)
            result = {**checked, 'fetch_id': identity, 'source_url': url,
                      'final_url': metadata['final_url'], 'fetched_at': time.time()}
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                self._grant(db, scope, grant_id)
                atomic_write_bytes(self.root/'objects'/checked['digest'][7:], raw)
                db.execute("UPDATE images SET status='completed',body=? WHERE id=?", (json.dumps(result),identity))
            return result
        except BaseException:
            with self.db() as db:
                db.execute("UPDATE images SET status='failed' WHERE id=?", (identity,))
            raise

    def read(self, scope, grant_id, fetch_id):
        with self.db() as db:
            self._grant(db, scope, grant_id)
            row = db.execute("SELECT body FROM images WHERE id=? AND grant_id=? AND status='completed'", (fetch_id,grant_id)).fetchone()
        if row is None:
            raise PermissionError('image_fetch_not_owned')
        result = json.loads(row[0])
        with (self.root/'objects'/result['digest'][7:]).open('rb') as stream:
            raw = stream.read(MAX_BYTES+1)
        if verify_image(raw)['digest'] != result['digest']:
            raise ValueError('image_object_digest_mismatch')
        return raw, result
