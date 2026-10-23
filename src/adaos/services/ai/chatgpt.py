"""Official Sign in with ChatGPT public-client OAuth and streaming Responses."""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import padding, rsa

AUTH = 'https://auth.openai.com'
API = 'https://api.openai.com/v1'
SCOPE = 'openid profile email offline_access resource.invoke chatgpt.tokens.use.direct'
PLAN_SCOPE = 'chatgpt.tokens.use.direct'


class AccountError(ValueError):
    pass


def b64decode(value):
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


def validate_id_token(token, client_id, nonce, jwks):
    try:
        header64, payload64, signature64 = token.split('.')
        header, payload = json.loads(b64decode(header64)), json.loads(b64decode(payload64))
        if header.get('alg') != 'RS256':
            raise ValueError('algorithm')
        key = next(item for item in jwks['keys'] if item.get('kid') == header.get('kid') and item.get('kty') == 'RSA')
        public = rsa.RSAPublicNumbers(int.from_bytes(b64decode(key['e']), 'big'), int.from_bytes(b64decode(key['n']), 'big')).public_key()
        public.verify(b64decode(signature64), (header64 + '.' + payload64).encode(), padding.PKCS1v15(), hashes.SHA256())
        audience = payload.get('aud')
        if payload.get('iss') != AUTH or client_id not in (audience if isinstance(audience, list) else [audience]):
            raise ValueError('identity')
        if not isinstance(payload.get('exp'), (int, float)) or payload['exp'] <= time.time() - 5:
            raise ValueError('expiry')
        if not isinstance(payload.get('sub'), str) or not payload['sub'] or not hmac.compare_digest(str(payload.get('nonce') or ''), nonce):
            raise ValueError('nonce')
        return payload
    except Exception as exc:
        raise AccountError('Не удалось подтвердить личность аккаунта OpenAI. Повторите подключение.') from exc


class KeyringVault:
    def __init__(self, identity, path, service='AdaOS.AIConnections.key'):
        self.identity = hashlib.sha256(str(identity).encode()).hexdigest()
        self.path = Path(path)
        self.service = service

    def _key(self, create=False):
        import keyring
        key = keyring.get_password(self.service, self.identity)
        if not key and create:
            key = Fernet.generate_key().decode('ascii')
            keyring.set_password(self.service, self.identity, key)
        if not key:
            raise AccountError('Ключ сессии недоступен в хранилище ОС. Подключите аккаунт повторно.')
        return key.encode('ascii')

    def read(self):
        try:
            if not self.path.exists():
                return None
            if self.path.stat().st_size > 256 * 1024:
                raise AccountError('Сохранённая сессия повреждена. Подключите аккаунт повторно.')
            return json.loads(Fernet(self._key()).decrypt(self.path.read_bytes()))
        except Exception as exc:
            raise AccountError('Защищённое хранилище ОС недоступно.') from exc

    def write(self, value):
        staged = self.path.with_name(self.path.name + '.' + secrets.token_hex(8) + '.new')
        try:
            encrypted = Fernet(self._key(create=True)).encrypt(json.dumps(value).encode('utf-8'))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(staged, self.path)
        except Exception as exc:
            raise AccountError('Не удалось сохранить сессию в защищённом хранилище ОС.') from exc
        finally:
            staged.unlink(missing_ok=True)

    def clear(self):
        import keyring
        try:
            self.path.unlink(missing_ok=True)
            if keyring.get_password(self.service, self.identity) is not None:
                keyring.delete_password(self.service, self.identity)
        except Exception as exc:
            raise AccountError('Не удалось удалить сессию из защищённого хранилища ОС.') from exc


class ChatGPTAccount:
    def __init__(self, store, vault=None, http=None, *, agent_name='AdaOS'):
        self.store = store
        self.agent_name = agent_name
        if not store.setting('host_id'):
            store.set_setting('host_id', 'urn:uuid:' + str(uuid4()))
        # Vault identity survives importing a different OAuth host registration.
        if not store.setting('vault_id'):
            store.set_setting('vault_id', store.setting('host_id'))
        self.vault = vault or KeyringVault('adaos-ai:' + store.setting('vault_id'), store.root/'connection.enc')
        self.http = http or httpx.Client(timeout=httpx.Timeout(45, connect=15), follow_redirects=False)
        self.lock = threading.RLock()
        self.pending = None
        self.error = ''
        self._models = (0, [])
        self.probe = {'status': 'idle'}
        self.generation = store.setting('generation', 0)

    def status(self):
        try:
            credentials = self.vault.read() or {}
        except AccountError as exc:
            credentials = {}
            self.error = str(exc)
        expired = (credentials.get('expires_at', 0) <= time.time() and not credentials.get('refresh_token')) or self.store.setting('account_reauth') == self._registration(credentials)
        enabled = bool(credentials.get('access_token') and PLAN_SCOPE in credentials.get('scope', '').split() and not expired)
        proof = self.store.setting('inference_verification', {})
        verified = bool(enabled and proof.get('registration') == self._registration(credentials))
        return {'connected': bool(credentials.get('access_token')), 'plan_enabled': enabled,
                'binding_ref': self._registration(credentials) if enabled else None,
                'email': credentials.get('email', ''), 'error': self.error,
                'needs_reauth': bool(credentials.get('access_token') and expired),
                'generation': self.generation, 'inference_verified': verified,
                'verified_model': proof.get('model', '') if verified else '',
                'verified_at': proof.get('at', '') if verified else '', 'probe': dict(self.probe),
                'pending': bool(self.pending), 'usage_url': 'https://chatgpt.com/settings/usage'}

    @staticmethod
    def _registration(credentials):
        return hashlib.sha256((credentials.get('subject', '')+'|'+credentials.get('client_id', '')).encode()).hexdigest()

    def binding_ref(self):
        status=self.status()
        if not status['plan_enabled']: raise AccountError('Аккаунт отключён или доступ отозван.')
        return status['binding_ref']

    def cancel_sign_in(self):
        with self.lock:
            self.pending = None
            self.error = ''
        return {'ok': True}

    def verify(self):
        """A user-initiated real request; never infer success from sign-in alone."""
        with self.lock:
            if self.probe['status'] == 'running':
                return {'ok': True}
            if not self.status()['plan_enabled']:
                raise AccountError('Сначала подключите личный аккаунт ChatGPT.')
            generation = self.generation
            self.probe = {'status': 'running'}

        def work():
            try:
                models = self.models(refresh=True)
                if not models:
                    raise AccountError('Аккаунт не вернул доступных моделей.')
                text = ''.join(self.stream(models[0]['id'], [{'role': 'user', 'content': 'Reply with exactly OK.'}], '', threading.Event()))
                if not text.strip():
                    raise AccountError('Модель завершила запрос без текстового ответа.')
                result = {'status': 'completed'}
            except Exception as exc:
                result = {'status': 'failed', 'error': str(exc) if isinstance(exc, AccountError) else 'Проверка не завершилась. Повторите попытку.'}
            with self.lock:
                if generation == self.generation:
                    self.probe = result
        threading.Thread(target=work, daemon=True, name='context-chat-account-check').start()
        return {'ok': True}

    def start(self):
        with self.lock:
            if self.pending:
                raise AccountError('Подключение уже открыто. Завершите вход в браузере или дождитесь окончания попытки.')
            state, nonce, verifier = (secrets.token_urlsafe(40) for _ in range(3))
            account = self

            class Callback(BaseHTTPRequestHandler):
                def log_message(self, *_args):
                    pass

                def do_GET(self):
                    parsed = urlsplit(self.path)
                    query = parse_qs(parsed.query)
                    if parsed.path != '/auth/callback' or not hmac.compare_digest(query.get('state', [''])[0], state):
                        self.send_response(400)
                        self.send_header('Content-Length', '0')
                        self.send_header('Connection', 'close')
                        self.end_headers()
                        self.close_connection = True
                        return
                    try:
                        account._complete(query, pending)
                        message = 'Аккаунт подключён. Можно вернуться в AdaOS.'
                    except Exception as exc:
                        account.error = str(exc) if isinstance(exc, AccountError) else 'Не удалось завершить подключение. Повторите вход.'
                        message = account.error
                    finally:
                        with account.lock:
                            if account.pending is pending:
                                account.pending = None
                    body = ('<!doctype html><meta charset="utf-8"><title>AdaOS</title><p>' + html.escape(message) + '</p>').encode()
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html; charset=utf-8')
                    self.send_header('Content-Length', str(len(body)))
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('Referrer-Policy', 'no-referrer')
                    self.end_headers()
                    self.wfile.write(body)

            server = HTTPServer(('127.0.0.1', 0), Callback)
            server.timeout = 1
            pending = {'state': state, 'nonce': nonce, 'verifier': verifier,
                       'redirect_uri': f'http://127.0.0.1:{server.server_port}/auth/callback',
                       'client_id': self.store.setting('client_id') or 'dynamic_agent_client', 'expires': time.time() + 300}
            self.pending, self.error = pending, ''
            params = {'client_id': pending['client_id'], 'ext_agent_host_id': self.store.setting('host_id'),
                      'response_type': 'code', 'redirect_uri': pending['redirect_uri'], 'scope': SCOPE,
                      'resource': API, 'state': state, 'nonce': nonce, 'code_challenge_method': 'S256',
                      'code_challenge': base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')}
            if pending['client_id'] == 'dynamic_agent_client':
                params['agent_name_hint'] = self.agent_name
            # This URL crosses the SDK/UI boundary. Reauthorization without an
            # ID-token hint is supported; keep every retained token in Core.
            # The issued client id and validated subject still pin the account.

            def listen():
                try:
                    while self.pending is pending and time.time() < pending['expires']:
                        server.handle_request()
                finally:
                    server.server_close()
                    with self.lock:
                        if self.pending is pending:
                            self.pending = None
                            self.error = 'Время подключения истекло. Повторите вход.'

            threading.Thread(target=listen, daemon=True, name='context-chat-oauth').start()
            return {'url': AUTH + '/api/accounts/authorize?' + urlencode(params)}

    def _complete(self, query, pending):
        if self.pending is not pending or time.time() > pending['expires']:
            raise AccountError('Попытка подключения истекла.')
        if query.get('error'):
            raise AccountError('Разрешение на подключение не выдано. Можно попробовать снова.')
        client_id = query.get('client_id', [pending['client_id']])[0]
        if pending['client_id'] == 'dynamic_agent_client':
            if not client_id or client_id == 'dynamic_agent_client':
                raise AccountError('OpenAI не вернул идентификатор регистрации.')
            self.store.set_setting('client_id', client_id)
        elif client_id != pending['client_id']:
            raise AccountError('Регистрация аккаунта не совпала. Подключение отклонено.')
        code = query.get('code', [''])[0]
        if not code:
            raise AccountError('OpenAI не вернул код авторизации.')
        data = self._token({'grant_type': 'authorization_code', 'client_id': client_id, 'code': code,
                            'code_verifier': pending['verifier'], 'redirect_uri': pending['redirect_uri'], 'resource': API})
        jwks = self.http.get(AUTH + '/.well-known/jwks.json')
        jwks.raise_for_status()
        identity = validate_id_token(data.get('id_token', ''), client_id, pending['nonce'], jwks.json())
        prior = self.store.setting('subject')
        if prior and prior != identity['sub']:
            raise AccountError('Эта регистрация привязана к другому аккаунту. Сначала отключите текущий аккаунт.')
        if PLAN_SCOPE not in data.get('scope', '').split() or not data.get('access_token'):
            raise AccountError('Не выдано разрешение использовать план ChatGPT. Подключите подходящий Plus/Pro аккаунт и разрешите расход плана.')
        data.update(client_id=client_id, subject=identity['sub'], email=identity.get('email', ''), expires_at=time.time()+int(data.get('expires_in', 3600)))
        with self.lock:
            if self.pending is not pending:
                raise AccountError('Подключение отменено.')
            self.vault.write(data)
            self.store.set_setting('subject', identity['sub'])
            self._models = (0, [])
            self.generation += 1
            self.store.set_setting('generation', self.generation)
            self.probe = {'status': 'idle'}
            self.store.set_setting('account_reauth', None)

    def _token(self, data):
        try:
            response = self.http.post(AUTH + '/api/accounts/oauth/token', data=data)
            if response.status_code != 200:
                if data.get('grant_type') == 'refresh_token' and response.status_code in (400, 401):
                    self.store.set_setting('account_reauth', self._registration(self.vault.read() or {}))
                raise AccountError('Сессия OpenAI не обновилась. Подключите аккаунт повторно.')
            result = response.json()
            if not isinstance(result, dict):
                raise AccountError('Некорректный ответ авторизации OpenAI.')
            return result
        except httpx.HTTPError as exc:
            raise AccountError('Не удалось связаться с сервером авторизации OpenAI.') from exc

    def access_token(self):
        with self.lock:
            data = self.vault.read() or {}
            if not data.get('access_token') or PLAN_SCOPE not in data.get('scope', '').split():
                raise AccountError('Подключите личный аккаунт ChatGPT и разрешите использование плана.')
            if data.get('expires_at', 0) <= time.time()+45:
                if not data.get('refresh_token'):
                    raise AccountError('Сессия истекла. Подключите аккаунт повторно.')
                renewed = self._token({'grant_type': 'refresh_token', 'client_id': data['client_id'], 'refresh_token': data['refresh_token'], 'resource': API})
                data.update(renewed)
                data['expires_at'] = time.time()+int(renewed.get('expires_in', 3600))
                if PLAN_SCOPE not in data.get('scope', '').split():
                    self.vault.clear()
                    raise AccountError('Разрешение использовать план отозвано. Подключите аккаунт повторно.')
                self.vault.write(data)
            return data['access_token']

    def disconnect(self):
        warning = ''
        with self.lock:
            data = self.vault.read() or {}
            if data.get('refresh_token'):
                try:
                    discovery = self.http.get(AUTH+'/.well-known/openid-configuration')
                    discovery.raise_for_status()
                    endpoint = discovery.json().get('revocation_endpoint', '')
                    if not endpoint.startswith(AUTH+'/'):
                        raise ValueError('revocation endpoint')
                    response = self.http.post(endpoint, data={'token': data['refresh_token'], 'token_type_hint': 'refresh_token', 'client_id': data['client_id']})
                    response.raise_for_status()
                except Exception:
                    warning = 'Локальная сессия удалена. Отзыв на стороне OpenAI не подтверждён; отключите приложение в настройках ChatGPT.'
            self.vault.clear()
            self.pending = None
            self._models = (0, [])
            self.generation += 1
            self.store.set_setting('generation', self.generation)
            self.probe = {'status': 'idle'}
            self.store.set_setting('inference_verification', {})
            self.store.set_setting('client_id', None)
            self.store.set_setting('subject', None)
            self.store.set_setting('account_reauth', None)
            self.error = warning
        return self.status()

    def models(self, refresh=False):
        token = self.access_token()
        if not refresh and time.time()-self._models[0] < 90:
            return self._models[1]
        try:
            response = self.http.get(API+'/models', headers={'Authorization': 'Bearer '+token})
            if response.status_code == 401:
                self.store.set_setting('account_reauth', self._registration(self.vault.read() or {}))
                raise AccountError('Сессия OpenAI истекла или отозвана. Подключите аккаунт повторно.')
            response.raise_for_status()
            payload = response.json()
            rows = payload.get('models', payload.get('data', []))
            from .models import descriptor
            models = [descriptor(row) for row in rows if isinstance(row,dict) and row.get('visibility','list')=='list' and (row.get('slug') or row.get('id'))]
            for model in models:
                rejection=self.store.setting('model_rejection:'+str(model['id']))
                if rejection: model['reason']=rejection
            self._models = (time.time(), models)
            return models
        except httpx.HTTPError as exc:
            raise AccountError('Не удалось получить доступные модели. Проверьте подключение аккаунта.') from exc

    def stream(self, model, inputs, instructions, cancelled, web_search=False, on_citations=None, reasoning_effort=None, expected_binding=None, text=None, on_usage=None):
        with self.lock:
            if cancelled.is_set():
                return
            if expected_binding is not None and self.binding_ref()!=expected_binding:
                raise AccountError('Аккаунт изменился после отправки. Запрос остановлен.')
            token = self.access_token()
            generation = self.generation
            registration = self._registration(self.vault.read() or {})
        completed = False
        text_received = False
        payload = {'model': model, 'input': inputs, 'instructions': instructions, 'store': False, 'stream': True}
        if text is not None:
            payload['text'] = text
        if reasoning_effort is not None:
            payload['reasoning']={'effort':reasoning_effort}
        if web_search:
            payload['tools'] = [{'type': 'web_search'}]
        try:
            if cancelled.is_set():
                return
            with self.http.stream('POST', API+'/responses', headers={'Authorization': 'Bearer '+token},
                                  json=payload,
                                  timeout=httpx.Timeout(45, connect=15, read=600)) as response:
                if response.status_code != 200:
                    self.store.set_setting('model_rejection:'+model,{'http_status':response.status_code,'observed_at':time.time(),'effort':reasoning_effort,'web_search':web_search,'reason':'provider_rejected_request'})
                    if response.status_code == 401:
                        self.store.set_setting('account_reauth', registration)
                        raise AccountError('Сессия OpenAI истекла или отозвана. Подключите аккаунт повторно.')
                    if response.status_code == 429:
                        raise AccountError('Доступные ресурсы плана исчерпаны или временно ограничены. Проверьте расход в ChatGPT и повторите позже.')
                    if web_search and response.status_code in (400, 403, 422):
                        raise AccountError('OpenAI отклонил запрос с веб-поиском. Инструмент может быть недоступен для этой модели или аккаунта. Выберите другую модель или выключите поиск в меню «+».')
                    if reasoning_effort is not None and response.status_code in (400,403,422):
                        raise AccountError('Провайдер отклонил выбранную силу reasoning. Обновите модели или выберите «По умолчанию». Повтор автоматически не выполняется.')
                    raise AccountError(f'OpenAI отклонил запрос (HTTP {response.status_code}). Проверьте модель и расход плана.')
                for line in response.iter_lines():
                    if cancelled.is_set():
                        return
                    if generation!=self.generation:
                        raise AccountError('Аккаунт изменился во время запроса. Ответ остановлен.')
                    if not line.startswith('data:'):
                        continue
                    raw = line[5:].strip()
                    if not raw or raw == '[DONE]':
                        continue
                    event = json.loads(raw)
                    if event.get('type') == 'response.output_text.delta':
                        text_received = text_received or bool(str(event.get('delta') or '').strip())
                        yield str(event.get('delta') or '')
                    elif event.get('type') == 'response.completed':
                        if on_usage:
                            on_usage((event.get('response') or {}).get('usage') or {})
                        if on_citations:
                            citations, offset = [], 0
                            for item in (event.get('response') or {}).get('output', []):
                                if item.get('type') != 'message':
                                    continue
                                for part in item.get('content', []):
                                    if part.get('type') != 'output_text':
                                        continue
                                    for annotation in part.get('annotations', []):
                                        url = annotation.get('url', '')
                                        parsed = urlsplit(url)
                                        start, end = annotation.get('start_index'), annotation.get('end_index')
                                        if annotation.get('type') == 'url_citation' and parsed.scheme in ('https', 'http') and parsed.hostname and not parsed.username and type(start) is int and type(end) is int and 0 <= start < end <= len(part.get('text', '')):
                                            citations.append({'url': url, 'title': str(annotation.get('title') or parsed.hostname)[:400], 'start_index': offset + start, 'end_index': offset + end})
                                    offset += len(part.get('text', ''))
                            on_citations(citations[:100])
                        completed = True
                        break
                    elif event.get('type') in ('response.failed', 'response.incomplete', 'error'):
                        error = (event.get('response') or {}).get('error') or event.get('error') or {}
                        code = str(error.get('code') or 'request_failed')
                        if code in ('subscription_sharing_usage_limit_exceeded', 'subscription_sharing_usage_unavailable'):
                            raise AccountError('Доступные ресурсы плана исчерпаны. Откройте управление расходом в ChatGPT.')
                        if web_search and code in ('unsupported_tool', 'invalid_tool', 'tool_not_supported', 'invalid_request_error'):
                            raise AccountError('Веб-поиск недоступен для этой модели или аккаунта. Выберите другую модель или выключите поиск в меню «+».')
                        raise AccountError('Запрос OpenAI не завершён. Попробуйте повторить его.')
        except httpx.HTTPError as exc:
            raise AccountError('Соединение с OpenAI прервалось. Можно повторить запрос.') from exc
        if not completed and not cancelled.is_set():
            raise AccountError('Ответ прервался до подтверждения завершения. Можно повторить запрос.')
        if completed and not cancelled.is_set():
            if not text_received:
                raise AccountError('Модель завершила запрос без текстового ответа. Выберите текстовую модель и повторите.')
            with self.lock:
                if generation == self.generation:
                    self.store.set_setting('inference_verification', {'registration': registration, 'model': model, 'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})
