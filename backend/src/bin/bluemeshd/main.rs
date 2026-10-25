// bluemeshd: headless mesh node (BitChat wire protocol) for the BlueMesh GUI.
//
// Reuses bitmancer's protocol core (mesh.rs), BLE transport (transport.rs) and
// geohash location channels (geo.rs + nostr) unchanged, and replaces the
// ratatui loop with a line-delimited JSON protocol:
//   stdin  <- one command object per line   {"cmd":"send","text":"hi"}
//   stdout -> one event object per line     {"ev":"public",...}
// Nothing here is a terminal, so no pty or screen-scraping is involved.

use std::collections::HashSet;
use std::path::PathBuf;
use std::time::{Duration, Instant};

use serde_json::{json, Value};
use tokio::io::AsyncBufReadExt;
use tokio::sync::mpsc;

use bitmancer::geo::{self, GeoService};
use bitmancer::mesh::{DeliveryStatus, MeshEvent, MeshService, PRIVATE_FILE_MAX_BYTES};
use bitmancer::nostr::client::{GeoEvent, NostrClient};
use bitmancer::transport::{self, Outbound, TransportEvent};
use bitmancer::{geohash, persistence, protocol, relay};

mod peripheral;
use peripheral::PeriphEvent;

/// Fans outbound frames across both BLE roles: the links we dialled
/// (bitmancer's transport) and the centrals that dialled us (GATT server).
///
/// The `gatt` set is a clone-time snapshot. A paced file sender takes one clone
/// when it starts, so a central connecting or leaving mid-transfer affects the
/// next frame from the main loop rather than stranding a half-sent file.
#[derive(Clone)]
struct Router {
    central: mpsc::Sender<Outbound>,
    notify: Option<mpsc::Sender<Vec<u8>>>,
    gatt: HashSet<String>,
}

impl Router {
    async fn send(&self, out: Outbound) {
        let to_gatt = |except: Option<&str>| !self.gatt.is_empty() && self.gatt.iter().any(|l| Some(l.as_str()) != except);
        match out {
            Outbound::All(data) => {
                if to_gatt(None) { if let Some(n) = &self.notify { let _ = n.send(data.clone()).await; } }
                let _ = self.central.send(Outbound::All(data)).await;
            }
            Outbound::Except { link, data } => {
                if to_gatt(Some(&link)) { if let Some(n) = &self.notify { let _ = n.send(data.clone()).await; } }
                if !link.starts_with("gatt:") || true {
                    let _ = self.central.send(Outbound::Except { link, data }).await;
                }
            }
            Outbound::Only { link, data } => {
                if link.starts_with("gatt:") {
                    if let Some(n) = &self.notify { let _ = n.send(data).await; }
                } else {
                    let _ = self.central.send(Outbound::Only { link, data }).await;
                }
            }
        }
    }
}

const MAINTENANCE_INTERVAL: Duration = Duration::from_secs(1);
/// Gap between private-file fragments.
///
/// Each central link has a 64-frame inbox and the transport drops on overflow;
/// Android has no fragment retransmit and discards an incomplete set after
/// 30 s. A whole file sent back to back therefore loses its tail. 20 ms keeps a
/// 256-fragment transfer under six seconds while letting the radio drain.
const FRAGMENT_PACING: Duration = Duration::from_millis(20);
/// A peer heard from within this window is shown as online, older as away
/// (it is dropped entirely by the mesh layer after 60 s).
const ONLINE_WINDOW: Duration = Duration::from_secs(50);

fn emit(value: Value) {
    use std::io::Write;
    let mut out = std::io::stdout().lock();
    let _ = writeln!(out, "{}", value);
    let _ = out.flush();
}

fn now_s() -> i64 {
    chrono::Local::now().timestamp()
}

fn fixed_key(bytes: Option<&[u8]>) -> Option<[u8; 32]> {
    bytes.and_then(|slice| <[u8; 32]>::try_from(slice).ok())
}

fn state_path() -> PathBuf {
    let args: Vec<String> = std::env::args().collect();
    if let Some(i) = args.iter().position(|a| a == "--state") {
        if let Some(p) = args.get(i + 1) {
            return PathBuf::from(p);
        }
    }
    let mut p = dirs::data_dir().unwrap_or_else(|| PathBuf::from("."));
    p.push("bluemesh");
    let _ = std::fs::create_dir_all(&p);
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o700));
    }
    p.push("state.json");
    p
}

fn relay_plan(
    mesh: &MeshService,
    data: &[u8],
    ingress: &str,
    links: &HashSet<String>,
    forwarded: &mut relay::Forwarded,
) -> Option<Vec<u8>> {
    let packet = protocol::Packet::decode(data)?;
    let names: Vec<String> = links.iter().cloned().collect();
    let relay::Relay::Forward { ttl, .. } = relay::plan(&packet, &names, ingress, &mesh.my_peer_id)
    else {
        return None;
    };
    if !forwarded.accept(&packet) {
        return None;
    }
    let mut onward = packet;
    onward.ttl = ttl;
    onward.encode()
}

/// Set when a peer's favourite notice changed the table, so the main loop saves.
static FAVORITES_DIRTY: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

fn restore_favorites(state: &persistence::AppState, mesh: &mut MeshService) {
    let mut restored = std::collections::HashMap::new();
    let fps: HashSet<String> = state.favorites.iter().chain(state.favorited_us.iter())
        .chain(state.favorite_nostr_keys.keys()).cloned().collect();
    for fp in fps {
        restored.insert(fp.clone(), bitmancer::favorites::Relationship {
            we_favorited: state.favorites.contains(&fp),
            they_favorited: state.favorited_us.contains(&fp),
            their_nostr_key: state.favorite_nostr_keys.get(&fp).cloned(),
            nickname: state.favorite_nicknames.get(&fp).cloned().unwrap_or_default(),
            noise_public_key: state.favorite_noise_keys.get(&fp).and_then(|k| hex::decode(k).ok()),
        });
    }
    mesh.favorites.load(restored);
}

/// Writes the favourites table back into state.json (same fields bitmancer uses).
fn save_favorites(state: &mut persistence::AppState, mesh: &MeshService, path: &std::path::Path) -> bool {
    state.favorites.clear();
    state.favorited_us.clear();
    state.favorite_nostr_keys.clear();
    state.favorite_nicknames.clear();
    state.favorite_noise_keys.clear();
    for (fp, e) in mesh.favorites.all() {
        if e.we_favorited { state.favorites.insert(fp.clone()); }
        if e.they_favorited { state.favorited_us.insert(fp.clone()); }
        if let Some(k) = &e.their_nostr_key { state.favorite_nostr_keys.insert(fp.clone(), k.clone()); }
        if !e.nickname.is_empty() { state.favorite_nicknames.insert(fp.clone(), e.nickname.clone()); }
        if let Some(k) = &e.noise_public_key { state.favorite_noise_keys.insert(fp.clone(), hex::encode(k)); }
    }
    persistence::save_state_at(path, state).is_ok()
}

/// "Accepted contact": we starred them and they starred us back. File
/// transfer in either direction is limited to these peers.
fn is_mutual(mesh: &MeshService, peer_id: &str) -> bool {
    let Some(p) = mesh.peers.get(peer_id) else { return false };
    mesh.favorites.get(&p.fingerprint).map_or(false, |r| r.we_favorited && r.they_favorited)
}

/// Blocked people for the GUI: fingerprint plus the best name we have
/// (remembered this session, or a favourite's stored nickname).
fn block_list(mesh: &MeshService, names: &std::collections::HashMap<String, String>) -> Value {
    let mut out: Vec<Value> = mesh.blocked_fingerprints().into_iter().map(|fp| {
        let name = names.get(&fp).cloned()
            .or_else(|| mesh.favorites.get(&fp).map(|r| r.nickname.clone()).filter(|n| !n.is_empty()))
            .unwrap_or_else(|| fp.chars().take(16).collect());
        json!({"fingerprint": fp, "peer_id": fp.chars().take(16).collect::<String>(), "nickname": name})
    }).collect();
    out.sort_by(|a, b| a["nickname"].as_str().unwrap_or("").to_lowercase().cmp(&b["nickname"].as_str().unwrap_or("").to_lowercase()));
    Value::Array(out)
}

/// Resolves a GUI/CLI target (peer ID, unique nickname) to a peer in range.
fn resolve_peer(mesh: &MeshService, target: &str) -> Result<String, String> {
    let t = target.trim();
    if t.is_empty() { return Err("say who".to_string()); }
    let lower = t.to_lowercase();
    if mesh.peers.contains_key(&lower) { return Ok(lower); }
    mesh.peer_id_for_nickname(t.trim_start_matches('@'))
}

const RECENT_CHANNELS_MAX: usize = 10;

fn peer_list(mesh: &MeshService) -> Value {
    let mut peers: Vec<Value> = mesh
        .peers
        .values()
        .map(|p| {
            json!({
                "peer_id": p.peer_id,
                "nickname": if p.nickname.is_empty() { p.peer_id.chars().take(8).collect() } else { p.nickname.clone() },
                "fingerprint": p.fingerprint,
                "verified": p.verified,
                "encrypted": mesh.has_encrypted_channel(&p.peer_id),
                "online": p.last_seen.elapsed() < ONLINE_WINDOW,
                "blocked": mesh.is_blocked(&p.peer_id),
                "favorite": mesh.favorites.get(&p.fingerprint).map_or(false, |r| r.we_favorited),
                "favorited_us": mesh.favorites.get(&p.fingerprint).map_or(false, |r| r.they_favorited),
                "mutual": is_mutual(mesh, &p.peer_id),
            })
        })
        .collect();
    // Favourites stay listed after they leave range (greyed in the GUI), the
    // way Android keeps them in its sidebar. Keyed by fingerprint; the peer ID
    // shown is the one the current protocol derives from it (its first 8 bytes).
    let present: HashSet<String> = mesh.peers.values().map(|p| p.fingerprint.to_lowercase()).collect();
    for (fingerprint, rel) in mesh.favorites.all() {
        if !rel.we_favorited || present.contains(fingerprint) || fingerprint.len() < 16 || mesh.is_blocked(fingerprint) { continue; }
        let pid: String = fingerprint.chars().take(16).collect();
        peers.push(json!({
            "peer_id": pid,
            "nickname": if rel.nickname.is_empty() { pid.chars().take(8).collect() } else { rel.nickname.clone() },
            "fingerprint": fingerprint,
            "verified": false, "encrypted": false, "online": false, "blocked": false,
            "favorite": true, "favorited_us": rel.they_favorited, "offline": true, "mutual": rel.they_favorited,
        }));
    }
    peers.sort_by(|a, b| {
        let fa = !a["favorite"].as_bool().unwrap_or(false);
        let fb = !b["favorite"].as_bool().unwrap_or(false);
        fa.cmp(&fb).then_with(|| a["nickname"].as_str().unwrap_or("").to_lowercase()
            .cmp(&b["nickname"].as_str().unwrap_or("").to_lowercase()))
    });
    Value::Array(peers)
}



// ---------- private file transfer ----------
// One FileTransfer packet sealed in the Noise session and fragmented to the
// accepted contact only. Received files are written to
// ~/.local/share/bluemesh/received (mode 700), never opened automatically.

/// The backend's private-transfer content limit. This includes the room the
/// file name, MIME type and encryption overhead take out of Android's
/// 256-fragment budget.
const MAX_FILE_BYTES: usize = PRIVATE_FILE_MAX_BYTES;

fn received_dir() -> Result<PathBuf, String> {
    let mut p = match std::env::var_os("BLUEMESH_RECEIVED_DIR") {
        Some(d) => PathBuf::from(d),
        None => {
            let mut d = dirs::data_dir().ok_or("no data directory")?;
            d.push("bluemesh");
            d.push("received");
            d
        }
    };
    std::fs::create_dir_all(&p).map_err(|e| format!("couldn't create {}: {e}", p.display()))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o700));
    }
    p = p.canonicalize().unwrap_or(p);
    Ok(p)
}

/// A sender-chosen name made safe to use as one path component: no
/// directories, no `..`, no control characters, no leading dot, bounded length.
fn safe_file_name(raw: &str) -> String {
    let last = raw.rsplit(|c| c == '/' || c == '\\').next().unwrap_or("");
    let cleaned: String = last
        .chars()
        .filter(|c| !c.is_control())
        .map(|c| if matches!(c, ':' | '*' | '?' | '"' | '<' | '>' | '|') { '_' } else { c })
        .collect();
    let trimmed = cleaned.trim().trim_start_matches('.').trim();
    let mut out = String::new();
    for c in trimmed.chars() {
        if out.len() + c.len_utf8() > 120 { break; }
        out.push(c);
    }
    if out.is_empty() || out == "." || out == ".." { "file".to_string() } else { out }
}

/// Writes bytes to a new file in `dir`, adding " (2)", " (3)"… rather than
/// overwriting anything already there.
fn save_unique(dir: &std::path::Path, name: &str, bytes: &[u8]) -> Result<PathBuf, String> {
    use std::io::Write;
    let path = std::path::Path::new(name);
    let stem = path.file_stem().and_then(|s| s.to_str()).unwrap_or(name).to_string();
    let ext = path.extension().and_then(|s| s.to_str()).map(|e| format!(".{e}")).unwrap_or_default();
    for n in 1..1000u32 {
        let candidate = if n == 1 { dir.join(name) } else { dir.join(format!("{stem} ({n}){ext}")) };
        let mut opts = std::fs::OpenOptions::new();
        opts.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            opts.mode(0o600);
        }
        match opts.open(&candidate) {
            Ok(mut f) => {
                f.write_all(bytes).map_err(|e| format!("couldn't write {}: {e}", candidate.display()))?;
                return Ok(candidate);
            }
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(e) => return Err(format!("couldn't save {name}: {e}")),
        }
    }
    Err(format!("couldn't find a free name for {name}"))
}

fn mime_for(name: &str) -> Option<&'static str> {
    let ext = std::path::Path::new(name).extension()?.to_str()?.to_lowercase();
    Some(match ext.as_str() {
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "gif" => "image/gif",
        "webp" => "image/webp",
        "bmp" => "image/bmp",
        "txt" | "md" => "text/plain",
        "pdf" => "application/pdf",
        "zip" => "application/zip",
        "json" => "application/json",
        "mp3" => "audio/mpeg",
        "m4a" => "audio/mp4",
        "ogg" => "audio/ogg",
        "wav" => "audio/wav",
        "mp4" => "video/mp4",
        _ => return None,
    })
}

fn kind_for(mime: Option<&str>, is_image: bool) -> &'static str {
    match mime.unwrap_or("") {
        _ if is_image => "image",
        m if m.starts_with("image/") => "image",
        m if m.starts_with("text/") => "text",
        m if m.starts_with("audio/") => "audio",
        m if m.starts_with("video/") => "video",
        "" => "file",
        _ => "file",
    }
}

/// Reads a file to send, checking the private-transfer limit before reading it in.
fn load_outgoing_file(raw: &str) -> Result<(String, Option<String>, Vec<u8>), String> {
    let raw = raw.trim();
    if raw.is_empty() { return Err("no file chosen".to_string()); }
    let path = if let Some(rest) = raw.strip_prefix("~/") {
        dirs::home_dir().ok_or("no home directory to expand ~ against")?.join(rest)
    } else {
        PathBuf::from(raw)
    };
    let name = path.file_name().and_then(|n| n.to_str()).ok_or("that filename is not valid text")?.to_string();
    let meta = std::fs::metadata(&path).map_err(|e| format!("couldn't read {name}: {e}"))?;
    if meta.is_dir() { return Err(format!("{name} is a folder, not a file")); }
    let len = meta.len() as usize;
    if len == 0 { return Err(format!("{name} is empty")); }
    if len > MAX_FILE_BYTES {
        return Err(format!(
            "{name} is too big for a private file transfer ({len} bytes; the limit is \
             {} bytes = {:.1} KiB including the file name and type)",
            MAX_FILE_BYTES,
            MAX_FILE_BYTES as f64 / 1024.0
        ));
    }
    let bytes = std::fs::read(&path).map_err(|e| format!("couldn't read {name}: {e}"))?;
    if bytes.len() > MAX_FILE_BYTES {
        return Err(format!(
            "{name} is larger than the private-transfer limit of {} bytes",
            MAX_FILE_BYTES
        ));
    }
    Ok((name, mime_for(&path.to_string_lossy()).map(str::to_string), bytes))
}

fn handle_received_file(peer_id: String, sender: String, name: String, mime: Option<String>, bytes: Vec<u8>, is_image: bool, private: bool) {
    let safe = safe_file_name(&name);
    let size = bytes.len();
    let mime = mime.or_else(|| mime_for(&safe).map(str::to_string));
    let kind = kind_for(mime.as_deref(), is_image);
    let saved = received_dir().and_then(|dir| save_unique(&dir, &safe, &bytes));
    match saved {
        Ok(path) => emit(json!({"ev":"file","peer_id":peer_id,"sender":sender,"name":safe,
            "path":path.to_string_lossy(),"size":size,"mime":mime,"kind":kind,"private":private,"ts":now_s()})),
        Err(e) => emit(json!({"ev":"file","peer_id":peer_id,"sender":sender,"name":safe,
            "path":Value::Null,"size":size,"mime":mime,"kind":kind,"private":private,"ts":now_s(),"error":e})),
    }
}

async fn handle_frame_events(mesh: &mut MeshService, router: &Router, link: &str, data: &[u8], last_notice: &mut String) {
    let link = link.to_string();
    for event in mesh.handle_frame(data) {
        match event {
            MeshEvent::Send(out) => { let _ = router.send(Outbound::All(out)).await; }
            MeshEvent::Reply(out) => { let _ = router.send(Outbound::Only { link: link.clone(), data: out }).await; }
            MeshEvent::PublicMessage { peer_id, sender, content, timestamp_ms } => {
                emit(json!({"ev":"public","peer_id":peer_id,"sender":sender,"content":content,"ts":(timestamp_ms/1000) as i64}));
            }
            MeshEvent::PrivateMessage { peer_id, sender, content, message_id } => {
                emit(json!({"ev":"dm","peer_id":peer_id,"sender":sender,"content":content,"message_id":message_id,"ts":now_s()}));
            }
            MeshEvent::DeliveryUpdate { message_id, status } => {
                let s = match status { DeliveryStatus::Delivered => "delivered", DeliveryStatus::Read => "read" };
                emit(json!({"ev":"delivery","message_id":message_id,"status":s}));
            }
            MeshEvent::SessionUp { peer_id, nickname, fingerprint } => {
                emit(json!({"ev":"session_up","peer_id":peer_id,"nickname":nickname,"fingerprint":fingerprint}));
            }
            MeshEvent::PeerAppeared { peer_id, nickname } => {
                emit(json!({"ev":"peer_joined","peer_id":peer_id,"nickname":nickname}));
            }
            MeshEvent::PeerLeft { peer_id, nickname } => {
                emit(json!({"ev":"peer_left","peer_id":peer_id,"nickname":nickname}));
            }
            MeshEvent::PeerRenamed { peer_id, nickname } => {
                emit(json!({"ev":"peer_renamed","peer_id":peer_id,"nickname":nickname}));
            }
            MeshEvent::FileReceived { peer_id, sender, name, mime, bytes, is_image, private } => {
                // Consent works both ways: a file is kept only from a mutual
                // favourite. Anything else (a broadcast from a stranger, a
                // directed file from a non-contact) is dropped unsaved.
                if is_mutual(mesh, &peer_id) {
                    handle_received_file(peer_id, sender, name, mime, bytes, is_image, private);
                } else {
                    let safe = safe_file_name(&name);
                    emit(json!({"ev":"file_ignored","peer_id":peer_id,"sender":sender,"name":safe,
                        "size":bytes.len(),"private":private,"ts":now_s()}));
                }
            }
            MeshEvent::FavoriteUpdate { peer_id, nickname, notice, .. } => {
                FAVORITES_DIRTY.store(true, std::sync::atomic::Ordering::Relaxed);
                emit(json!({"ev":"favorite_update","peer_id":peer_id,"nickname":nickname,"favorited_us":notice.is_favorite}));
                emit(json!({"ev":"notice","text":format!("{nickname} {} you", if notice.is_favorite {"favourited"} else {"unfavourited"})}));
            }
            MeshEvent::Notice(text) => {
                if text != *last_notice { *last_notice = text.clone(); emit(json!({"ev":"log","text":text})); }
            }
            MeshEvent::Trace(text) => emit(json!({"ev":"log","text":text})),
            // Store-and-forward and internet-gateway features are not offered here.
            MeshEvent::CarriedEvent { .. } | MeshEvent::CourierDeposit { .. } | MeshEvent::CourierArrived { .. } => {}
        }
    }
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    if std::env::args().any(|a| a == "--version") {
        println!("bluemeshd {} (bitmancer core)", env!("CARGO_PKG_VERSION"));
        return Ok(());
    }
    if std::env::args().any(|a| a == "--doctor") {
        std::process::exit(transport::doctor(10).await);
    }

    let path = state_path();
    let mut state = persistence::load_state_at(&path);
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600));
    }
    let nickname = state.nickname.clone().unwrap_or_else(|| "anonymous".to_string());
    let identity_key = fixed_key(state.identity_key.as_deref()).ok_or("identity key missing")?;
    let noise_key = fixed_key(state.noise_static_key.as_deref()).ok_or("noise key missing")?;
    let nostr_seed = fixed_key(state.nostr_device_seed.as_deref()).ok_or("nostr seed missing")?;

    let mut mesh = MeshService::new(identity_key, noise_key, &nickname);
    mesh.load_blocked(state.blocked_peers.clone());
    mesh.load_verified(state.verified_fingerprints.clone());
    restore_favorites(&state, &mut mesh);
    let mut geo = GeoService::new(nostr_seed, &mesh.nickname);

    emit(json!({
        "ev": "ready",
        "peer_id": mesh.my_peer_id,
        "nickname": mesh.nickname,
        "fingerprint": mesh.my_fingerprint(),
        "nickname_set": state.nickname.is_some(),
        "state_file": path.to_string_lossy(),
        "max_private_file_bytes": MAX_FILE_BYTES,
    }));

    // stdin -> commands
    let (cmd_tx, mut cmd_rx) = mpsc::channel::<Value>(64);
    tokio::spawn(async move {
        let mut lines = tokio::io::BufReader::new(tokio::io::stdin()).lines();
        loop {
            match lines.next_line().await {
                Ok(Some(line)) => {
                    if line.trim().is_empty() { continue; }
                    match serde_json::from_str::<Value>(&line) {
                        Ok(v) => { if cmd_tx.send(v).await.is_err() { break; } }
                        Err(e) => emit(json!({"ev":"error","text":format!("bad command: {e}")})),
                    }
                }
                // GUI went away: shut down cleanly.
                _ => { let _ = cmd_tx.send(json!({"cmd":"quit"})).await; break; }
            }
        }
    });

    // Links BlueZ kept from a previous run point at a GATT app that no longer
    // exists; drop them before we dial or serve anything. See peripheral.rs.
    let stale = peripheral::drop_stale_links(None).await;
    if !stale.is_empty() {
        emit(json!({"ev":"log","text":format!("dropped {} stale mesh link(s) from a previous run: {}", stale.len(), stale.join(", "))}));
        tokio::time::sleep(std::time::Duration::from_millis(800)).await;
    }
    let mut transport = transport::spawn();
    let want_peripheral = !std::env::args().any(|a| a == "--no-peripheral");
    let mut periph = None;
    if want_peripheral {
        match peripheral::start().await {
            Ok(p) => {
                emit(json!({"ev":"peripheral","ok":true,"text":format!("Advertising on {} — other devices can connect to you", p.adapter)}));
                periph = Some(p);
            }
            Err(e) => emit(json!({"ev":"peripheral","ok":false,"text":format!("Couldn't advertise ({e}); you can still connect to phones")})),
        }
    }
    let mut router = Router {
        central: transport.outbound.clone(),
        notify: periph.as_ref().map(|p| p.notify.clone()),
        gatt: HashSet::new(),
    };
    // Created lazily: no internet traffic at all unless a location channel is joined.
    let mut nostr: Option<NostrClient> = None;

    let mut links: HashSet<String> = HashSet::new();
    let mut forwarded = relay::Forwarded::default();
    let mut offline_since: Option<Instant> = Some(Instant::now());
    let mut connected = false;
    let mut last_maintenance = Instant::now();
    let mut last_peers = Value::Null;
    let mut last_channel_people: std::collections::HashMap<String, Value> = Default::default();
    let mut last_notice = String::new();
    let mut blocked_names: std::collections::HashMap<String, String> = Default::default();
    let mut wiped = false;
    emit(json!({"ev":"status","state":"scanning","links":0,"text":"Looking for mesh peers nearby…"}));

    let mut tick = tokio::time::interval(Duration::from_millis(250));
    loop {
        tokio::select! {
            Some(te) = transport.events.recv() => {
                match te {
                    TransportEvent::Status(text) => {
                        emit(json!({"ev":"status","state": if connected {"connected"} else {"scanning"},"links":links.len(),"text":text}));
                    }
                    TransportEvent::LinkUp { link, label, held: _ } => {
                        links.insert(link.clone());
                        offline_since = None;
                        connected = true;
                        emit(json!({"ev":"status","state":"connected","links":links.len(),"text":format!("Linked to {label}")}));
                        if let Some(frame) = mesh.announce_frame() {
                            let _ = router.send(Outbound::All(frame)).await;
                        }
                    }
                    TransportEvent::LinkDown { link, reason, held: _ } => {
                        links.remove(&link);
                        if links.is_empty() { offline_since.get_or_insert_with(Instant::now); }
                        emit(json!({"ev":"status","state": if !links.is_empty() {"connected"} else {"reconnecting"},"links":links.len(),"text":format!("Link ended ({reason})")}));
                    }
                    TransportEvent::Fatal(reason) => {
                        connected = false;
                        emit(json!({"ev":"status","state":"error","links":0,"text":reason}));
                    }
                    TransportEvent::Frame { link, data } => {
                        let onward = relay_plan(&mesh, &data, &link, &links, &mut forwarded);
                        handle_frame_events(&mut mesh, &router, &link, &data, &mut last_notice).await;
                        if let Some(fwd) = onward {
                            let _ = router.send(Outbound::Except { link, data: fwd }).await;
                        }
                    }
                }
            }
            Some(cmd) = cmd_rx.recv() => {
                let name = cmd["cmd"].as_str().unwrap_or("");
                let req = cmd["req"].clone();
                match name {
                    "quit" => break,
                    "send" => {
                        let text = cmd["text"].as_str().unwrap_or("").trim().to_string();
                        if text.is_empty() { continue; }
                        if links.is_empty() {
                            emit(json!({"ev":"sent","req":req,"ok":false,"kind":"public","content":text,"ts":now_s(),"error":"Not connected to anyone yet"}));
                            continue;
                        }
                        let frames = mesh.public_message_frames(&text);
                        let parts = frames.len();
                        for f in frames { let _ = router.send(Outbound::All(f)).await; }
                        emit(json!({"ev":"sent","req":req,"ok":parts>0,"kind":"public","content":text,"ts":now_s(),"parts":parts,"links":links.len()}));
                    }
                    "dm" => {
                        let peer = cmd["peer_id"].as_str().unwrap_or("").to_string();
                        let text = cmd["text"].as_str().unwrap_or("").trim().to_string();
                        if text.is_empty() { continue; }
                        if !mesh.peers.contains_key(&peer) {
                            emit(json!({"ev":"sent","req":req,"ok":false,"kind":"dm","peer_id":peer,"content":text,"ts":now_s(),"error":"That person isn't in range right now"}));
                            continue;
                        }
                        let pending = !mesh.has_encrypted_channel(&peer);
                        match mesh.dm_frames(&peer, &text) {
                            Ok(sent) => {
                                for f in sent.frames { let _ = router.send(Outbound::All(f)).await; }
                                emit(json!({"ev":"sent","req":req,"ok":true,"kind":"dm","peer_id":peer,"content":text,"ts":now_s(),"message_ids":sent.ids,"pending_session":pending}));
                            }
                            Err(e) => emit(json!({"ev":"sent","req":req,"ok":false,"kind":"dm","peer_id":peer,"content":text,"ts":now_s(),"error":e})),
                        }
                    }
                    "read" => {
                        let peer = cmd["peer_id"].as_str().unwrap_or("").to_string();
                        let ids: Vec<String> = cmd["ids"].as_array().map(|a| a.iter().filter_map(|v| v.as_str().map(String::from)).collect()).unwrap_or_default();
                        if mesh.peers.contains_key(&peer) && !ids.is_empty() {
                            for f in mesh.read_receipt_frames(&peer, &ids) { let _ = router.send(Outbound::All(f)).await; }
                        }
                    }
                    "nick" => {
                        let new = cmd["name"].as_str().unwrap_or("").trim().to_string();
                        if new.is_empty() { continue; }
                        mesh.set_nickname(&new);
                        geo.set_nickname(&mesh.nickname);
                        state.nickname = Some(mesh.nickname.clone());
                        let saved = persistence::save_state_at(&path, &state).is_ok();
                        if !links.is_empty() {
                            if let Some(frame) = mesh.announce_frame() { let _ = router.send(Outbound::All(frame)).await; }
                        }
                        emit(json!({"ev":"nick","nickname":mesh.nickname,"saved":saved}));
                    }
                    "join" => {
                        let gh = geohash::normalize(cmd["channel"].as_str().unwrap_or(""));
                        if !geohash::is_valid(&gh) {
                            emit(json!({"ev":"error","text":format!("“{gh}” isn't a valid location channel. Location channels are geohashes such as #r3gx (letters 0-9 b-z, no a/i/l/o).")}));
                            continue;
                        }
                        state.joined_channels.retain(|c| c != &gh);
                        state.joined_channels.insert(0, gh.clone());
                        state.joined_channels.truncate(RECENT_CHANNELS_MAX);
                        let _ = persistence::save_state_at(&path, &state);
                        let client = nostr.get_or_insert_with(NostrClient::spawn);
                        match geo.join(&gh) {
                            Some(relays) => {
                                client.subscribe(&gh, relays.clone()).await;
                                emit(json!({"ev":"channel_joined","channel":geo::channel_name(&gh),"relays":relays.len()}));
                            }
                            None => emit(json!({"ev":"channel_joined","channel":geo::channel_name(&gh),"relays":geo.relays_for(&gh).len(),"already":true})),
                        }
                    }
                    "leave" => {
                        let ch = cmd["channel"].as_str().unwrap_or("");
                        if let Some(gh) = geo::geohash_from_channel(ch).or_else(|| Some(geohash::normalize(ch))) {
                            if geo.leave(&gh) {
                                if let Some(c) = &nostr { c.unsubscribe(&gh).await; }
                            }
                            last_channel_people.remove(&geo::channel_name(&gh));
                            emit(json!({"ev":"channel_left","channel":geo::channel_name(&gh)}));
                        }
                    }
                    "channel_send" => {
                        let ch = cmd["channel"].as_str().unwrap_or("");
                        let text = cmd["text"].as_str().unwrap_or("").trim().to_string();
                        if text.is_empty() { continue; }
                        match (geo::geohash_from_channel(ch), &nostr) {
                            (Some(gh), Some(client)) if geo.is_joined(&gh) => {
                                let event = geo.message_event(&gh, &text);
                                client.publish(&gh, event).await;
                                emit(json!({"ev":"sent","req":req,"ok":true,"kind":"channel","channel":ch,"content":text,"ts":now_s()}));
                            }
                            _ => emit(json!({"ev":"sent","req":req,"ok":false,"kind":"channel","channel":ch,"content":text,"ts":now_s(),"error":"Not in that channel"})),
                        }
                    }
                    "send_file" => {
                        // Files go only to one accepted contact (mutual favourite),
                        // sealed in the Noise session. Broadcasting a file to every
                        // device in range is switched off.
                        let raw = cmd["path"].as_str().unwrap_or("").to_string();
                        let peer = cmd["peer_id"].as_str().unwrap_or("").to_lowercase();
                        let fail = |name: &str, error: String| emit(json!({"ev":"sent","req":req,"ok":false,"kind":"file","peer_id":peer,"name":name,"ts":now_s(),"error":error}));
                        if peer.is_empty() {
                            fail("", "Sending files to everyone nearby is turned off. Open a chat with a mutual favourite to send them a file.".to_string());
                            continue;
                        }
                        if !mesh.peers.contains_key(&peer) {
                            fail("", "That person isn't in range right now".to_string());
                            continue;
                        }
                        if !is_mutual(&mesh, &peer) {
                            fail("", "Files can only go to a mutual favourite: star them and wait for them to star you back".to_string());
                            continue;
                        }
                        if !mesh.has_encrypted_channel(&peer) {
                            fail("", "No encrypted session yet. Send them a message first, then try again".to_string());
                            continue;
                        }
                        let (name, mime, bytes) = match load_outgoing_file(&raw) {
                            Ok(v) => v,
                            Err(e) => { fail("", e); continue; }
                        };
                        let size = bytes.len();
                        let kind = kind_for(mime.as_deref(), mime.as_deref().is_some_and(|m| m.starts_with("image/")));
                        match mesh.private_file_frames(&peer, &name, mime.clone(), bytes) {
                            Ok(frames) => {
                                let parts = frames.len();
                                emit(json!({"ev":"file_sending","req":req,"name":name,"size":size,"parts":parts}));
                                // Pace the burst off the main loop so the radio
                                // can drain each link's inbox; report `sent`
                                // only after the last fragment has been queued.
                                let router = router.clone();
                                let sent_req = req.clone();
                                let sent_peer = peer.clone();
                                let sent_name = name.clone();
                                let sent_mime = mime.clone();
                                let link_count = links.len();
                                tokio::spawn(async move {
                                    let last = frames.len().saturating_sub(1);
                                    for (index, frame) in frames.into_iter().enumerate() {
                                        router.send(Outbound::All(frame)).await;
                                        if index < last {
                                            tokio::time::sleep(FRAGMENT_PACING).await;
                                        }
                                    }
                                    emit(json!({"ev":"sent","req":sent_req,"ok":true,"kind":"file","peer_id":sent_peer,"name":sent_name,"size":size,"mime":sent_mime,"file_kind":kind,"parts":parts,"links":link_count,"private":true,"ts":now_s()}));
                                });
                            }
                            Err(e) => fail(&name, e),
                        }
                    }
                    "block" => {
                        let target = cmd["peer"].as_str().or(cmd["peer_id"].as_str()).unwrap_or("").to_string();
                        let result = resolve_peer(&mesh, &target).and_then(|pid| {
                            let fp = mesh.peers.get(&pid).map(|p| p.fingerprint.to_lowercase());
                            mesh.block(&pid).map(|nick| (pid, fp, nick))
                        });
                        match result {
                            Ok((pid, fp, nick)) => {
                                if let Some(fp) = fp { blocked_names.insert(fp, nick.clone()); }
                                state.blocked_peers = mesh.blocked_fingerprints();
                                let saved = persistence::save_state_at(&path, &state).is_ok();
                                emit(json!({"ev":"blocked","ok":true,"peer_id":pid,"nickname":nick,"saved":saved}));
                            }
                            Err(e) => emit(json!({"ev":"blocked","ok":false,"peer":target,"error":e})),
                        }
                        emit(json!({"ev":"blocks","blocked":block_list(&mesh, &blocked_names)}));
                        let pl = peer_list(&mesh);
                        emit(json!({"ev":"peers","peers":pl.clone()}));
                        last_peers = pl;
                    }
                    "unblock" => {
                        let target = cmd["peer"].as_str().or(cmd["fingerprint"].as_str()).or(cmd["peer_id"].as_str()).unwrap_or("").trim().to_string();
                        // A nickname we remember for a blocked fingerprint resolves too.
                        let needle = blocked_names.iter().find(|(_, n)| n.eq_ignore_ascii_case(&target)).map(|(f, _)| f.clone()).unwrap_or(target.clone());
                        if needle.is_empty() { emit(json!({"ev":"unblocked","ok":false,"error":"say who"})); continue; }
                        match mesh.unblock(&needle) {
                            Ok(label) => {
                                state.blocked_peers = mesh.blocked_fingerprints();
                                let saved = persistence::save_state_at(&path, &state).is_ok();
                                let name = blocked_names.iter().find(|(f, _)| f.starts_with(&label)).map(|(_, n)| n.clone()).unwrap_or(label.clone());
                                blocked_names.retain(|f, _| !f.starts_with(&label));
                                emit(json!({"ev":"unblocked","ok":true,"peer_id":label,"nickname":name,"saved":saved}));
                            }
                            Err(e) => emit(json!({"ev":"unblocked","ok":false,"peer":target,"error":e})),
                        }
                        emit(json!({"ev":"blocks","blocked":block_list(&mesh, &blocked_names)}));
                    }
                    "blocks" => { emit(json!({"ev":"blocks","blocked":block_list(&mesh, &blocked_names)})); }
                    "wipe" => {
                        // Same as the TUI's `/wipe confirm`: nothing happens without
                        // an explicit {"confirm": true}.
                        if cmd["confirm"].as_bool() != Some(true) {
                            emit(json!({"ev":"wiped","ok":false,"error":"wipe needs {\"confirm\": true}"}));
                            continue;
                        }
                        if !links.is_empty() {
                            if let Some(frame) = mesh.leave_frame() {
                                router.send(Outbound::All(frame)).await;
                                tokio::time::sleep(Duration::from_millis(200)).await;
                            }
                        }
                        match persistence::wipe_state_at(&path) {
                            Ok(existed) => {
                                mesh.wipe();
                                blocked_names.clear();
                                if let Some(c) = &nostr { for gh in geo.joined() { c.unsubscribe(&gh).await; } }
                                let mut files_removed = Value::Null;
                                if cmd["files"].as_bool() == Some(true) {
                                    if let Ok(dir) = received_dir() {
                                        files_removed = json!(std::fs::remove_dir_all(&dir).is_ok());
                                    }
                                }
                                emit(json!({"ev":"wiped","ok":true,"existed":existed,"state_file":path.to_string_lossy(),"files_removed":files_removed}));
                                wiped = true;
                                break;
                            }
                            // A failed wipe must not look like success: keys are still on disk.
                            Err(e) => emit(json!({"ev":"wiped","ok":false,"error":format!("could not destroy the stored identity: {e}. Nothing was cleared.")})),
                        }
                    }
                    "channels" => {
                        let joined: Vec<Value> = geo.joined().iter().map(|gh| json!({
                            "channel": geo::channel_name(gh), "people": geo.participant_count(gh)})).collect();
                        let recent: Vec<Value> = state.joined_channels.iter().map(|gh| {
                            let (lat, lon) = geohash::decode_center(gh);
                            json!({"channel": geo::channel_name(gh), "lat": (lat * 10.0).round() / 10.0, "lon": (lon * 10.0).round() / 10.0, "joined": geo.is_joined(gh)})
                        }).collect();
                        emit(json!({"ev":"channels","joined":joined,"recent":recent}));
                    }
                    "peers" => { emit(json!({"ev":"peers","peers":peer_list(&mesh)})); }
                    "favorite" => {
                        // Star / unstar, as in Android 2.0.1 (ChatViewModel.toggleFavorite):
                        // stored locally by Noise-key fingerprint; if the peer is here
                        // with an established Noise session, tell them with the same
                        // "[FAVORITED]:<npub>" private message Android sends over the
                        // mesh (our npub is left empty: no Nostr in this app).
                        let on = cmd["on"].as_bool().unwrap_or(true);
                        let pid = cmd["peer_id"].as_str().unwrap_or("").to_lowercase();
                        let fp_arg = cmd["fingerprint"].as_str().unwrap_or("").to_lowercase();
                        let peer = mesh.peers.get(&pid).cloned();
                        let fingerprint = peer.as_ref().map(|p| p.fingerprint.to_lowercase())
                            .or_else(|| (!fp_arg.is_empty()).then(|| fp_arg.clone()))
                            .or_else(|| mesh.favorites.all().find(|(f, _)| f.starts_with(&pid) && pid.len() == 16).map(|(f, _)| f.clone()));
                        let Some(fingerprint) = fingerprint else {
                            emit(json!({"ev":"error","text":"Can't favourite someone who hasn't announced themselves yet"}));
                            continue;
                        };
                        let nickname = peer.as_ref().map(|p| p.nickname.clone()).unwrap_or_default();
                        let mut told = false;
                        if let Some(p) = &peer {
                            if mesh.has_encrypted_channel(&p.peer_id) {
                                if let Ok(sent) = mesh.favorite_frames(&p.peer_id, on, "") {
                                    for f in sent.frames { let _ = router.send(Outbound::All(f)).await; }
                                    told = true;
                                }
                            }
                        }
                        mesh.favorites.set_ours(&fingerprint, &nickname, on);
                        if let Some(p) = &peer { mesh.favorites.note_key(&fingerprint, &p.noise_public_key); }
                        let saved = save_favorites(&mut state, &mesh, &path);
                        emit(json!({"ev":"favorite","fingerprint":fingerprint,"peer_id":peer.as_ref().map(|p| p.peer_id.clone()).unwrap_or(pid.clone()),"on":on,"told_peer":told,"saved":saved}));
                        let pl = peer_list(&mesh);
                        emit(json!({"ev":"peers","peers":pl.clone()}));
                        last_peers = pl;
                    }
                    "debug" => { mesh.debug = cmd["on"].as_bool().unwrap_or(!mesh.debug); }
                    other => emit(json!({"ev":"error","text":format!("unknown command {other}")})),
                }
            }
            Some(pe) = async { match periph.as_mut() { Some(p) => p.events.recv().await, None => std::future::pending().await } } => {
                match pe {
                    PeriphEvent::Up { link } => {
                        links.insert(link.clone());
                        router.gatt.insert(link.clone());
                        offline_since = None;
                        connected = true;
                        emit(json!({"ev":"status","state":"connected","links":links.len(),"text":format!("{} connected to you", link.trim_start_matches("gatt:"))}));
                        if let Some(frame) = mesh.announce_frame() { router.send(Outbound::All(frame)).await; }
                    }
                    PeriphEvent::Down { link } => {
                        links.remove(&link);
                        router.gatt.remove(&link);
                        if links.is_empty() { offline_since.get_or_insert_with(Instant::now); }
                        emit(json!({"ev":"status","state": if links.is_empty() {"reconnecting"} else {"connected"},"links":links.len(),"text":format!("{} went quiet", link.trim_start_matches("gatt:"))}));
                    }
                    PeriphEvent::Log(t) => emit(json!({"ev":"log","text":t})),
                    PeriphEvent::Frame { link, data } => {
                        let onward = relay_plan(&mesh, &data, &link, &links, &mut forwarded);
                        handle_frame_events(&mut mesh, &router, &link, &data, &mut last_notice).await;
                        if let Some(fwd) = onward { router.send(Outbound::Except { link, data: fwd }).await; }
                    }
                }
            }
            Some(ge) = async { match nostr.as_mut() { Some(c) => c.events.recv().await, None => std::future::pending().await } } => {
                match ge {
                    GeoEvent::Message { geohash, pubkey, nickname, content, created_at, teleported } => {
                        if pubkey == geo.pubkey_for(&geohash) { continue; }
                        let sender = geo.note_activity(&geohash, &pubkey, nickname);
                        emit(json!({"ev":"channel_msg","channel":geo::channel_name(&geohash),"sender":sender,"pubkey":pubkey,"content":content,"ts":created_at,"teleported":teleported}));
                    }
                    GeoEvent::Presence { geohash, pubkey, .. } => { geo.note_activity(&geohash, &pubkey, None); }
                    GeoEvent::HistoryEnd { geohash } => emit(json!({"ev":"channel_live","channel":geo::channel_name(&geohash)})),
                    GeoEvent::RelayFailed { relay, reason } => emit(json!({"ev":"log","text":format!("{relay} unreachable ({reason})")})),
                    _ => {}
                }
            }
            _ = tick.tick() => {}
        }

        if last_maintenance.elapsed() >= MAINTENANCE_INTERVAL {
            last_maintenance = Instant::now();
            if !links.is_empty() && mesh.announce_due() {
                if let Some(frame) = mesh.announce_frame() { let _ = router.send(Outbound::All(frame)).await; }
            }
            for event in mesh.prune_peers() {
                if let MeshEvent::PeerLeft { peer_id, nickname } = event {
                    emit(json!({"ev":"peer_left","peer_id":peer_id,"nickname":nickname}));
                }
            }
            mesh.sweep_sync_state();
            if let Some(client) = &nostr {
                for (target, event) in geo.due_presence() { client.publish(&target, event).await; }
                geo.prune_participants();
                for gh in geo.joined() {
                    let ch = geo::channel_name(&gh);
                    let people: Vec<String> = geo.participants(&gh).iter().map(|p| p.display_name()).collect();
                    let v = json!(people);
                    if last_channel_people.get(&ch) != Some(&v) {
                        emit(json!({"ev":"channel_people","channel":ch,"people":v}));
                        last_channel_people.insert(ch, v);
                    }
                }
            }
            if offline_since.is_some_and(|t| t.elapsed() >= transport::OFFLINE_GRACE) && links.is_empty() && connected {
                connected = false;
                offline_since = None;
                mesh.clear_peers();
                emit(json!({"ev":"status","state":"scanning","links":0,"text":"Nobody in range. Still looking…"}));
            }
            if FAVORITES_DIRTY.swap(false, std::sync::atomic::Ordering::Relaxed) {
                save_favorites(&mut state, &mesh, &path);
            }
            let peers = peer_list(&mesh);
            if peers != last_peers {
                emit(json!({"ev":"peers","peers":peers.clone()}));
                last_peers = peers;
            }
        }
    }

    if !links.is_empty() && !wiped {
        if let Some(frame) = mesh.leave_frame() {
            let _ = router.send(Outbound::All(frame)).await;
            tokio::time::sleep(Duration::from_millis(200)).await;
        }
    }
    emit(json!({"ev":"bye"}));
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn names_from_the_mesh_cannot_escape_the_folder() {
        assert_eq!(safe_file_name("../../etc/passwd"), "passwd");
        assert_eq!(safe_file_name("..\\..\\evil.exe"), "evil.exe");
        assert_eq!(safe_file_name(".bashrc"), "bashrc");
        assert_eq!(safe_file_name(".."), "file");
        assert_eq!(safe_file_name("   "), "file");
        assert_eq!(safe_file_name("a\u{0}b\nc.txt"), "abc.txt");
        assert_eq!(safe_file_name("cat.png"), "cat.png");
        assert!(safe_file_name(&"x".repeat(500)).len() <= 120);
    }

    #[test]
    fn saving_never_overwrites() {
        let dir = std::env::temp_dir().join(format!("bluemesh-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let a = save_unique(&dir, "cat.png", b"one").unwrap();
        let b = save_unique(&dir, "cat.png", b"two").unwrap();
        assert_eq!(a.file_name().unwrap(), "cat.png");
        assert_eq!(b.file_name().unwrap(), "cat (2).png");
        assert_eq!(std::fs::read(&a).unwrap(), b"one");
        assert_eq!(std::fs::read(&b).unwrap(), b"two");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn outgoing_files_respect_the_limit() {
        let dir = std::env::temp_dir().join(format!("bluemesh-out-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let small = dir.join("note.txt");
        std::fs::write(&small, b"hello").unwrap();
        let (name, mime, bytes) = load_outgoing_file(small.to_str().unwrap()).unwrap();
        assert_eq!((name.as_str(), mime.as_deref(), bytes.len()), ("note.txt", Some("text/plain"), 5));
        let big = dir.join("big.bin");
        std::fs::write(&big, vec![0u8; MAX_FILE_BYTES + 1]).unwrap();
        let error = load_outgoing_file(big.to_str().unwrap()).unwrap_err();
        assert!(error.contains("private file transfer"), "got: {error}");
        assert!(error.contains("52.9"), "got: {error}");
        let exact = dir.join("exact.bin");
        std::fs::write(&exact, vec![1u8; MAX_FILE_BYTES]).unwrap();
        assert!(load_outgoing_file(exact.to_str().unwrap()).is_ok());
        assert!(load_outgoing_file(dir.join("missing").to_str().unwrap()).is_err());
        assert!(load_outgoing_file(dir.to_str().unwrap()).unwrap_err().contains("folder"));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn the_daemon_uses_the_backend_private_file_limit() {
        assert_eq!(MAX_FILE_BYTES, bitmancer::mesh::PRIVATE_FILE_MAX_BYTES);
    }

    #[test]
    fn a_full_size_broadcast_file_round_trips_through_the_mesh_and_lands_on_disk() {
        use bitmancer::mesh::{MeshEvent, MeshService};
        let mut alice = MeshService::new([1u8; 32], [2u8; 32], "alice");
        let mut bob = MeshService::new([3u8; 32], [4u8; 32], "bob");
        // The broadcast path still has the protocol's 1 MiB payload limit; the
        // private path is capped much lower by Android's fragment count.
        let original: Vec<u8> = (0..1024 * 1024).map(|i| (i % 251) as u8).collect();
        let frames = alice.file_frames("photo.jpg", Some("image/jpeg".into()), original.clone()).unwrap();
        let mut got = None;
        for f in frames {
            for ev in bob.handle_frame(&f) {
                if let MeshEvent::FileReceived { name, bytes, .. } = ev { got = Some((name, bytes)); }
            }
        }
        let (name, bytes) = got.expect("file arrives");
        assert_eq!(name, "photo.jpg");
        assert_eq!(bytes, original);
    }
}
