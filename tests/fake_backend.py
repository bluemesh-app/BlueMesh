#!/usr/bin/env python3
"""Stand-in for bluemeshd for GUI tests: no Bluetooth, no network.
Logs every command it gets to $FAKE_LOG and answers 'favorite' like the real one."""
import json, os, sys, threading, time
log = open(os.environ.get("FAKE_LOG", "/tmp/fake_backend.log"), "a")
def emit(o): print(json.dumps(o), flush=True)
peers = {
  "a1b2c3d4e5f60718": dict(peer_id="a1b2c3d4e5f60718", nickname="Alice", fingerprint="a1b2c3d4e5f60718" + "a"*48,
                           verified=True, encrypted=True, online=True, blocked=False, favorite=True, favorited_us=True, mutual=True),
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
if os.environ.get("FAKE_FILE"):
    # pretend Alice sent us a file (path must already exist)
    fp = os.environ["FAKE_FILE"]
    emit({"ev":"file","peer_id":"a1b2c3d4e5f60718","sender":"Alice","name":os.path.basename(fp),"path":fp,
          "size":os.path.getsize(fp),"mime":"text/plain","kind":"text","ts":int(time.time())})
if os.environ.get("FAKE_IMAGE"):
    # Alice (a mutual favourite) sends us an image privately; Bob (not a contact) is ignored
    fp = os.environ["FAKE_IMAGE"]
    emit({"ev":"file","peer_id":"a1b2c3d4e5f60718","sender":"Alice","name":os.path.basename(fp),"path":fp,
          "size":os.path.getsize(fp),"mime":"image/png","kind":"image","private":True,"ts":int(time.time())})
    emit({"ev":"file_ignored","peer_id":"b2c3d4e5f6071829","sender":"Bob","name":"junk.zip","size":999,
          "private":False,"ts":int(time.time())})
blocked = {}
recent = [{"channel":"#r3gx","lat":-33.8,"lon":151.3,"joined":False},{"channel":"#u4pr","lat":59.9,"lon":10.7,"joined":False}]
def blist():
    return [{"fingerprint":p["fingerprint"],"peer_id":p["peer_id"],"nickname":p["nickname"]} for p in blocked.values()]
for line in sys.stdin:
    log.write(line); log.flush()
    c = json.loads(line)
    if c.get("cmd") == "favorite":
        p = peers.get(c["peer_id"])
        p["favorite"] = c["on"]
        emit({"ev":"favorite","peer_id":p["peer_id"],"fingerprint":p["fingerprint"],"on":c["on"],"told_peer":p["encrypted"],"saved":True})
        emit({"ev":"peers","peers":plist()})
    elif c.get("cmd") == "send_file":
        size = os.path.getsize(c["path"])
        emit({"ev":"file_sending","req":c.get("req"),"name":os.path.basename(c["path"]),"size":size,"parts":3})
        if not c.get("peer_id"):
            emit({"ev":"sent","req":c.get("req"),"ok":False,"kind":"file","error":"broadcast off"})
        elif size > 1024 * 1024:
            emit({"ev":"sent","req":c.get("req"),"ok":False,"kind":"file","error":"limited to 1 MiB"})
        else:
            emit({"ev":"sent","req":c.get("req"),"ok":True,"kind":"file","name":os.path.basename(c["path"]),"size":size,"links":2})
    elif c.get("cmd") == "block":
        p = peers.pop(c["peer"], None)
        if p:
            blocked[p["peer_id"]] = p
            emit({"ev":"blocked","ok":True,"peer_id":p["peer_id"],"nickname":p["nickname"],"saved":True})
        else:
            emit({"ev":"blocked","ok":False,"peer":c["peer"],"error":"nobody here is called that"})
        emit({"ev":"blocks","blocked":blist()}); emit({"ev":"peers","peers":plist()})
    elif c.get("cmd") == "unblock":
        p = next((b for b in blocked.values() if b["fingerprint"] == c["peer"]), None)
        if p:
            del blocked[p["peer_id"]]; peers[p["peer_id"]] = p
            emit({"ev":"unblocked","ok":True,"peer_id":p["peer_id"],"nickname":p["nickname"],"saved":True})
            emit({"ev":"peers","peers":plist()})
        emit({"ev":"blocks","blocked":blist()})
    elif c.get("cmd") == "blocks":
        emit({"ev":"blocks","blocked":blist()})
    elif c.get("cmd") == "channels":
        emit({"ev":"channels","joined":[],"recent":recent})
    elif c.get("cmd") == "join":
        emit({"ev":"channel_joined","channel":"#"+c["channel"],"relays":5})
    elif c.get("cmd") == "send":
        emit({"ev":"sent","req":c.get("req"),"ok":True,"kind":"public","content":c["text"],"ts":int(time.time()),"links":2})
    elif c.get("cmd") == "wipe":
        if c.get("confirm") is True:
            emit({"ev":"wiped","ok":True,"existed":True,"state_file":"/tmp/x","files_removed":None}); emit({"ev":"bye"}); break
        emit({"ev":"wiped","ok":False,"error":"wipe needs confirm"})
    elif c.get("cmd") == "quit":
        emit({"ev":"bye"}); break
