const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../jailbreak/smoke/Resources/webio-transport.js'), 'utf8');
const target = 'wss://ru.api.inimatic.com/hubs/test-subnet/ws?token=test.jwt.signature';
function harness() {
    const events = [], sockets = [];
    class FakeWebSocket {
        constructor(url) { this.url = url; this.readyState = 0; this.sent = []; sockets.push(this); }
        send(text) { this.sent.push(text); }
        close() { this.closed = true; }
    }
    const window = {WebSocket: FakeWebSocket, webkit: {messageHandlers: {adaio: {postMessage: e => events.push(e)}}}};
    vm.runInNewContext(source, {window});
    return {transport: window.AdaTransport, events, sockets};
}
test('allows only the declared TLS subnet targets', () => {
    for (const url of ['ws://ru.api.inimatic.com/hubs/test/ws?token=t', 'wss://evil.test/hubs/test/ws?token=t',
        target + '&redirect=evil', target.replace('test-subnet', '../admin'), 'javascript:alert(1)']) {
        const h = harness();
        assert.equal(h.transport.connect(url), false);
        assert.equal(h.sockets.length, 0);
        assert.equal(h.events[0].kind, 'invalid-target');
    }
    assert.equal(harness().transport.connect(target), true);
});
test('commands are explicit and are never queued or resent after reconnect', () => {
    const h = harness();
    assert.equal(h.transport.send({id:'offline'}), false);
    h.transport.connect(target);
    const first = h.sockets[0];
    assert.equal(h.transport.send({id:'connecting'}), false);
    first.readyState = 1; first.onopen();
    const payload = {id:'one', text:'Привет\n"quoted"; </script>\u2028'};
    assert.equal(h.transport.send(payload), true);
    assert.deepEqual(JSON.parse(first.sent[0]), payload);
    h.transport.connect(target);
    assert.equal(first.closed, true);
    const second = h.sockets[1]; second.readyState = 1; second.onopen();
    assert.equal(second.sent.length, 0);
});
test('stale callbacks cannot restore or deliver a stopped connection', () => {
    const h = harness(); h.transport.connect(target);
    const old = h.sockets[0], staleMessage = old.onmessage, staleOpen = old.onopen;
    h.transport.stop(); staleMessage({data:'private'}); staleOpen();
    assert.equal(h.events.length, 0);
    assert.equal(h.transport.send({id:'late'}), false);
});
test('native connection identity is attached before a message crosses the bridge', () => {
    const h = harness(); h.transport.connect(target, 'first');
    const first = h.sockets[0]; first.onmessage({data:'old'});
    h.transport.connect(target, 'second');
    h.sockets[1].onmessage({data:'new'});
    assert.equal(h.events[0].connection, 'first');
    assert.equal(h.events[1].connection, 'second');
});
test('an asynchronously evaluated Stop cannot close a newer connection', () => {
    const h = harness(); h.transport.connect(target, 'first');
    const first = h.sockets[0];
    h.transport.connect(target, 'second');
    const second = h.sockets[1]; second.readyState = 1;
    h.transport.stop('first');
    assert.equal(first.closed, true);
    assert.equal(second.closed, undefined);
    assert.equal(h.transport.status(), 1);
    assert.equal(h.transport.send({id:'fresh'}), true);
    h.transport.stop('second');
    assert.equal(second.closed, true);
    assert.equal(h.transport.status(), -1);
});
test('bounds inbound and outbound frames and exposes no URL through errors', () => {
    const h = harness(); h.transport.connect(target);
    const socket = h.sockets[0]; socket.readyState = 1;
    assert.equal(h.transport.send({text:'x'.repeat(786433)}), false);
    socket.onerror({url:target});
    assert.equal(h.events[0].kind, 'error');
    assert.equal(h.events[0].data, '');
    socket.onmessage({data:'x'.repeat(196609)});
    assert.equal(h.events[1].kind, 'oversized');
    assert.equal(socket.closed, true);
});
