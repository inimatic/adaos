/* ES5 transport adapter for the iOS 9 WebKit WebSocket implementation.
 * It renders no remote content and never replays commands after reconnect.
 */
(function (global) {
    'use strict';
    var socket = null, generation = 0, connection = '', limit = 196608;
    function post(kind, data) {
        global.webkit.messageHandlers.adaio.postMessage({kind: kind, data: data || '', connection: connection});
    }
    function stop() {
        generation += 1;
        var old = socket;
        socket = null;
        if (old) {
            old.onopen = old.onmessage = old.onerror = old.onclose = null;
            try { old.close(1000, 'client stopped'); } catch (ignore) {}
        }
    }
    function connect(url, identifier) {
        stop();
        connection = typeof identifier === 'string' ? identifier : '';
        if (typeof url !== 'string' || !/^wss:\/\/(ru\.api\.inimatic\.com|api\.inimatic\.com)\/hubs\/[A-Za-z0-9_-]{1,128}\/ws\?token=[A-Za-z0-9._%~-]{1,8192}$/.test(url)) {
            post('invalid-target'); return false;
        }
        var ownGeneration = generation, own;
        try { own = new global.WebSocket(url); } catch (ignore) { post('error'); return false; }
        socket = own;
        function current() { return generation === ownGeneration && socket === own; }
        own.onopen = function () { if (current()) post('open'); };
        own.onmessage = function (event) {
            if (!current()) return;
            if (typeof event.data !== 'string' || event.data.length > limit) {
                post('oversized'); stop(); return;
            }
            post('message', event.data);
        };
        own.onerror = function () { if (current()) post('error'); };
        own.onclose = function () { if (current()) { socket = null; post('closed'); } };
        return true;
    }
    function send(envelope) {
        if (!socket || socket.readyState !== 1) return false;
        var text;
        try { text = JSON.stringify(envelope); } catch (ignore) { return false; }
        if (!text || text.length > limit) return false;
        try { socket.send(text); return true; } catch (ignore) { post('error'); return false; }
    }
    global.AdaTransport = {connect: connect, send: send, stop: stop};
}(window));
