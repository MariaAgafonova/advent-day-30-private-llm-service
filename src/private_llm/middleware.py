"""Limit raw HTTP bytes before JSON parsing; log only operational metadata."""
import json
from time import monotonic
from uuid import uuid4
from starlette.responses import JSONResponse


class Guard:
    def __init__(self, app, config, logger, queue_status):
        self.app, self.config, self.logger, self.queue_status = app, config, logger, queue_status

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        started, rid, status = monotonic(), str(uuid4()), 500
        scope.setdefault('state', {})['request_id'] = rid
        state = scope['state']

        async def send_safe(event):
            nonlocal status
            if event['type'] == 'http.response.start':
                status = event['status']
                event.setdefault('headers', []).extend([
                    (b'x-request-id', rid.encode()), (b'cache-control', b'no-store'),
                    (b'x-content-type-options', b'nosniff'), (b'referrer-policy', b'no-referrer'),
                    (b'content-security-policy', b"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")])
            await send(event)

        async def error(code, name, message):
            state['error_type'] = name
            await JSONResponse({'request_id': rid, 'error': {'code': name, 'message': message}}, status_code=code)(scope, receive, send_safe)

        try:
            headers = dict(scope['headers'])
            length = headers.get(b'content-length', b'0')
            try:
                size = int(length)
                if size < 0:
                    raise ValueError()
            except ValueError:
                return await error(400, 'invalid_request', 'Неверный Content-Length.')
            if size > self.config.max_body_bytes:
                return await error(413, 'body_too_large', 'Размер запроса превышает допустимый лимит.')
            # Enforce the cap even for chunked transfer without Content-Length.
            body = bytearray()
            if scope['method'] in ('POST', 'PUT', 'PATCH'):
                while True:
                    event = await receive()
                    if event['type'] == 'http.disconnect':
                        status = 499
                        return
                    body.extend(event.get('body', b''))
                    if len(body) > self.config.max_body_bytes:
                        return await error(413, 'body_too_large', 'Размер запроса превышает допустимый лимит.')
                    if not event.get('more_body'):
                        break
            consumed = False

            async def replay():
                nonlocal consumed
                if not consumed and scope['method'] in ('POST', 'PUT', 'PATCH'):
                    consumed = True
                    return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
                return await receive()

            await self.app(scope, replay, send_safe)
        finally:
            self.logger.info(json.dumps({'request_id': rid, 'processing_ms': round((monotonic()-started)*1000, 2),
                'http_status': status, 'queue_length': self.queue_status().get('waiting', 0),
                'error_type': state.get('error_type'), 'result_status': state.get('result_status', status)}))
