#!/usr/bin/env python3
"""Stand-in for bitchat-desktopd for GUI tests: no Bluetooth, no network.
Logs every command it gets to $FAKE_LOG and answers 'favorite' like the real one."""
import json, os, sys, threading, time
log = open(os.environ.get("FAKE_LOG", "/tmp/fake_backend.log"), "a")
def emit(o): print(json.dumps(o), flush=True)
peers = {
  "70d1e61c340e53fc": dict(peer_id="70d1e61c340e53fc", nickname="F3", fingerprint="70d1e61c340e53fc" + "a"*48,
                           verified=True, encrypted=True, online=True, blocked=False, favorite=False, favorited_us=True),
  "dea69e4ce7c491be": dict(peer_id="dea69e4ce7c491be", nickname="Biggus Dickus", fingerprint="dea69e4ce7c491be" + "b"*48,
                           verified=True, encrypted=False, online=True, blocked=False, favorite=False, favorited_us=False),
  "c75bdafbb4ab9821": dict(peer_id="c75bdafbb4ab9821", nickname="linxdot", fingerprint="c75bdafbb4ab9821" + "c"*48,
                           verified=False, encrypted=False, online=False, blocked=False, favorite=True, favorited_us=False, offline=True),
}
def plist():
    return sorted(peers.values(), key=lambda p: (not p["favorite"], p["nickname"].lower()))
emit({"ev":"ready","peer_id":"d52606304d8774f6","nickname":"ProB","fingerprint":"d5"*32,"nickname_set":True,"state_file":"/tmp/x"})
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
