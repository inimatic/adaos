"""Loopback fault fixture using the production YRoom, admission and RTC adapter.

Only signaling and fault controls are fixture endpoints. No normal node state,
compiler, public Root, or user credential is used.
"""
import argparse
import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--runtime-dir', required=True)
parser.add_argument('--port', type=int, default=0)
parser.add_argument('--initial-delay-ms', type=int, default=0)
args = parser.parse_args()
os.environ['ADAOS_BASE_DIR'] = str(Path(args.runtime_dir).resolve())
os.environ['ADAOS_CREDENTIAL_VAULT_BACKEND'] = 'file'
from adaos.apps.bootstrap import init_ctx
from adaos.services.settings import Settings
ctx = init_ctx(Settings(base_dir=Path(os.environ['ADAOS_BASE_DIR']), env_type='dev',
                        owner_id='channel-fixture-owner', subnet_id='channel-fixture-subnet'))

import y_py as Y
from aiohttp import web, WSMsgType
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from adaos.services.desktop_access import DesktopAccessService, DesktopPrincipal
from adaos.services.webrtc.yjs_adapter import DataChannelYjsAdapter
from adaos.services.yjs.gateway_ws import DiagnosticYRoom, FastAPIWebsocketAdapter
from adaos.services.yjs.sync_protocol import decode_state_vector, encode_server_sync_update

room = None
room_task = None
peers, sockets, serving = set(), set(), set()
adapters = []
modes = []
errors = []
counts = {'rtc': 0, 'yws': 0, 'restarts': 0}
principal = DesktopPrincipal(DesktopAccessService().access.owner)


class Capture(logging.Handler):
    def emit(self, record):
        if record.exc_info or record.levelno >= logging.ERROR:
            errors.append(record.getMessage())


logger = logging.getLogger('channel.fixture.room')
logger.addHandler(Capture())
logger.setLevel(logging.DEBUG)


def track(coro):
    task = asyncio.create_task(coro)
    serving.add(task)
    task.add_done_callback(serving.discard)
    return task


async def start_room(snapshot=None):
    global room, room_task
    room = DiagnosticYRoom(ready=False, log=logger)
    room._webspace_id = 'channel-fixture'
    # The minimized cross-client GC origin used to manufacture future clocks.
    Y.apply_update(room.ydoc, snapshot or bytes.fromhex('0201020081010401020100000a010101720100'))
    if snapshot is None:
        with room.ydoc.begin_transaction() as txn:
            room.ydoc.get_map('data').set(txn, 'boot', 'authoritative')
            room.ydoc.get_map('data').set(txn, 'large', 'x' * (512 * 1024))

    def observe(message):
        if message[:2] == b'\x00\x00':
            # Read STEP1's length-prefixed vector without mutating the room.
            pos, length, shift = 2, 0, 0
            while pos < len(message):
                byte = message[pos]
                pos += 1
                length |= (byte & 127) << shift
                if byte < 128:
                    break
                shift += 7
            _, mode = encode_server_sync_update(room.ydoc, message[pos:pos+length])
            modes.append(mode)
        return False

    room.on_message = observe
    room.ready = True
    room_task = asyncio.create_task(room.start())
    await room.started.wait()


async def stop_room():
    if room is not None and room._task_group is not None:
        room.stop()
    if room_task is not None:
        await room_task


async def offer(request):
    body = await request.json()
    pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    peers.add(pc)
    counts['rtc'] += 1
    current_room = room

    @pc.on('datachannel')
    def channel(dc):
        class DelayedInitialSync(DataChannelYjsAdapter):
            pending_delay = True
            stalled = False

            def _enqueue(self, payload):
                if not self.stalled:
                    super()._enqueue(payload)

            async def send(self, message):
                if self.pending_delay and message[:2] == b'\x00\x01':
                    self.pending_delay = False
                    if args.initial_delay_ms:
                        # A live update may overtake a slow initial snapshot.
                        # It cannot prove that the complete initial state arrived.
                        with current_room.ydoc.begin_transaction() as txn:
                            current_room.ydoc.get_map('data').set(txn, 'early_update', counts['rtc'])
                    await asyncio.sleep(min(15000, max(0, args.initial_delay_ms)) / 1000)
                await super().send(message)

        adapter = DelayedInitialSync(dc, 'channel-fixture', device_id='fixture-browser',
                                        peer_id=str(counts['rtc']), desktop_principal=principal)
        adapters.append(adapter)
        track(current_room.serve(adapter))

    await pc.setRemoteDescription(RTCSessionDescription(sdp=body['sdp'], type=body['type']))
    await pc.setLocalDescription(await pc.createAnswer())
    return web.json_response({'sdp': pc.localDescription.sdp, 'type': pc.localDescription.type})


class Socket:
    """Starlette's three websocket methods over an actual aiohttp socket."""
    def __init__(self, socket):
        self.socket = socket

    async def receive(self):
        message = await self.socket.receive()
        if message.type == WSMsgType.BINARY:
            return {'type': 'websocket.receive', 'bytes': message.data}
        if message.type == WSMsgType.TEXT:
            return {'type': 'websocket.receive', 'text': message.data}
        return {'type': 'websocket.disconnect', 'code': self.socket.close_code or 1006}

    async def send_bytes(self, data):
        await self.socket.send_bytes(data)

    async def close(self, code=1000, reason=''):
        await self.socket.close(code=code, message=reason.encode())


async def yws(request):
    DesktopAccessService().require(principal, 'channel-fixture')
    socket = web.WebSocketResponse()
    await socket.prepare(request)
    sockets.add(socket)
    counts['yws'] += 1
    try:
        await room.serve(FastAPIWebsocketAdapter(Socket(socket), 'channel-fixture'))
    finally:
        sockets.discard(socket)
    return socket


async def state(request):
    data = json.loads(room.ydoc.get_map('data').to_json())
    return web.json_response({
        'vector': decode_state_vector(Y.encode_state_vector(room.ydoc)),
        'data': {key: (len(value) if key == 'large' else value) for key, value in data.items()},
        'digest': hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
        'modes': modes, 'errors': errors, 'counts': counts,
        'clients': len(room.clients), 'pending_sends': room._diag_pending_send_tasks,
    })


async def mutate(request):
    body = await request.json()
    with room.ydoc.begin_transaction() as txn:
        room.ydoc.get_map('data').set(txn, body['key'], body['value'])
    return web.json_response({'ok': True})


async def disconnect():
    await asyncio.gather(*(pc.close() for pc in list(peers)))
    peers.clear()
    await asyncio.gather(*(socket.close(code=1012, message=b'fixture_restart') for socket in list(sockets)))


async def fault(request):
    body = await request.json()
    if body['kind'] == 'rtc':
        await asyncio.gather(*(pc.close() for pc in list(peers)))
        peers.clear()
    elif body['kind'] == 'stall_rtc':
        for adapter in adapters:
            adapter.stalled = True
    elif body['kind'] == 'yws':
        await asyncio.gather(*(socket.close(code=1012, message=b'fixture_disconnect') for socket in list(sockets)))
    elif body['kind'] == 'restart':
        snapshot = Y.encode_state_as_update(room.ydoc)
        path = Path(args.runtime_dir) / 'channel.ysnap'
        path.write_bytes(snapshot)
        await disconnect()
        await stop_room()
        await start_room(path.read_bytes())
        counts['restarts'] += 1
    else:
        raise web.HTTPBadRequest()
    return web.json_response({'ok': True})


async def main():
    await start_room()
    app = web.Application()
    app.add_routes([web.get('/state', state), web.post('/offer', offer),
                    web.get('/yws/{name}', yws), web.post('/mutate', mutate), web.post('/fault', fault)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', args.port)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    print(json.dumps({'ready': True, 'port': port}), flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await disconnect()
        await stop_room()
        await runner.cleanup()


asyncio.run(main())
