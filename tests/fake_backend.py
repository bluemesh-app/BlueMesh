#!/usr/bin/env python3
"""Stand-in for bluemeshd for GUI tests: no Bluetooth, no network.
Logs every command it gets to $FAKE_LOG and answers 'favorite' like the real one."""
import json, os, sys, threading, time
log = open(os.environ.get("FAKE_LOG", "/tmp/fake_backend.log"), "a")
def emit(o): print(json.dumps(o), flush=True)
peers = {
  "a1b2c3d4e5f60718": dict(peer_id="a1b2c3d4e5f60718", nickname="Alice", fingerprint="a1b2c3d4e5f60718" + "a"*48,
                           verified=True, encrypted=True, online=True, blocked=False, favorite=False, favorited_us=True),
  "b2c3d4e5f6071829": dict(peer_id="b2c3d4e5f6071829", nickname="Bob", fingerprint="b2c3d4e5f6071829" + "b"*48,
                           verified=True, encrypted=False, online=True, blocked=False, favorite=False, favorited_us=False),
  "c3d4e5f60718293a": dict(peer_id="c3d4e5f60718293a", nickname="Carol", fingerprint="c3d4e5f60718293a" + "c"*48,
                           verified=False, encrypted=False, online=False, blocked=False, favorite=True, favorited_us=False, offline=True),
}
def plist():
    return sorted(peers.values(), key=lambda p: (not p["favorite"], p["nickname"].lower()))
emit({"ev":"ready","peer_id":"0f1e2d3c4b5a6978","nickname":"You","fingerprint":"d5"*32,"nickname_set":True,"state_file":"/tmp/x"})
emit({"ev":"peripheral","ok":True,"text":"Advertising"})
emit({"ev":"status","state":"connected","links":2,"text":"Linked (fake)"})
emit({"ev":"peers","peers":plist()})
for line in sys.stdin:
    log.write(line); log.flush()
    c = json.loads(line)
    if c.get("cmd") == "favorite":
        p = peers.get(c["peer_id"])
        p["favorite"] = c["on"]
        emit({"ev":"favorite","peer_id":p["peer_id"],"fingerprint":p["fingerprint"],"on":c["on"],"told_peer":p["encrypted"],"saved":True})
        emit({"ev":"peers","peers":plist()})
    elif c.get("cmd") == "quit":
        emit({"ev":"bye"}); break
