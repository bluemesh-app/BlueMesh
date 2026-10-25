// GATT peripheral role for bluemeshd.
//
// bitmancer (like bitchat-tui) is BLE-central only: it can dial phones, which
// advertise, but two desktops can never find each other because neither
// advertises. The official apps run both roles at once, so this adds the
// other half: the BitChat service and characteristic are published through
// BlueZ's D-Bus GATT server and the service UUID is advertised.
//
// BlueZ aggregates notification subscribers, so outbound frames are notified
// to every subscribed central at once. Inbound writes carry the writer's
// address, which names the link ("gatt:<addr>") for the relay rules.

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::sync::Arc;
use std::time::{Duration, Instant};

use bluer::adv::{Advertisement, Type as AdvType};
use bluer::gatt::local::{
    Application, Characteristic, CharacteristicNotifier, CharacteristicNotify,
    CharacteristicNotifyMethod, CharacteristicRead, CharacteristicWrite, CharacteristicWriteMethod,
    Service,
};
use tokio::sync::{mpsc, Mutex};
use uuid::Uuid;

pub const SERVICE_UUID: Uuid = Uuid::from_u128(0xF47B5E2D_4A9E_4C5A_9B3F_8E1D2C3A4B5C);
pub const CHARACTERISTIC_UUID: Uuid = Uuid::from_u128(0xA1B2C3D4_E5F6_4A5B_8C9D_0E1F2A3B4C5D);
/// A central that has written nothing for this long is treated as gone. Live
/// nodes announce every 10-30 s.
const SILENCE: Duration = Duration::from_secs(90);

#[derive(Debug)]
pub enum PeriphEvent {
    Up { link: String },
    Down { link: String },
    Frame { link: String, data: Vec<u8> },
    Log(String),
}

pub struct Peripheral {
    pub events: mpsc::Receiver<PeriphEvent>,
    pub notify: mpsc::Sender<Vec<u8>>,
    pub adapter: String,
}

/// Drops BLE connections to BitChat devices that BlueZ still holds from an
/// earlier run (or that went silent), so they reconnect and rediscover the
/// GATT service.
///
/// Why: BlueZ keeps the radio link to a phone alive across a backend restart,
/// but our GATT application is unregistered and registered again. An unbonded
/// Android client gets no Service Changed indication, keeps its cached
/// handles and goes on writing into nothing, and it still counts the link as
/// a "direct route" to us. Its broadcasts reach us through relays, but
/// everything addressed to us (Noise handshakes, DMs) goes down the dead link.
/// Seen with an Android phone during testing. Only devices advertising the BitChat
/// service UUID are touched; headphones and the like are left alone.
pub async fn drop_stale_links(only: Option<&str>) -> Vec<String> {
    let mut dropped = Vec::new();
    let Ok(session) = bluer::Session::new().await else { return dropped };
    let Ok(adapter) = session.default_adapter().await else { return dropped };
    let Ok(addrs) = adapter.device_addresses().await else { return dropped };
    for addr in addrs {
        if let Some(o) = only {
            if !addr.to_string().eq_ignore_ascii_case(o) { continue; }
        }
        let Ok(dev) = adapter.device(addr) else { continue };
        if !dev.is_connected().await.unwrap_or(false) { continue; }
        let bitchat = dev.uuids().await.ok().flatten().map_or(false, |u| u.contains(&SERVICE_UUID));
        if !bitchat { continue; }
        if dev.disconnect().await.is_ok() {
            dropped.push(addr.to_string());
        }
    }
    dropped
}

pub async fn start() -> Result<Peripheral, String> {
    let session = bluer::Session::new().await.map_err(|e| format!("BlueZ session: {e}"))?;
    let adapter = session.default_adapter().await.map_err(|e| format!("adapter: {e}"))?;
    let adapter_name = adapter.name().to_string();
    let (ev_tx, ev_rx) = mpsc::channel::<PeriphEvent>(256);
    let (notify_tx, mut notify_rx) = mpsc::channel::<Vec<u8>>(256);

    let notifier: Arc<Mutex<Option<CharacteristicNotifier>>> = Arc::new(Mutex::new(None));
    let seen: Arc<std::sync::Mutex<HashMap<String, Instant>>> = Default::default();

    let write_tx = ev_tx.clone();
    let write_seen = seen.clone();
    let notif_slot = notifier.clone();
    let log_tx = ev_tx.clone();

    let app = Application {
        services: vec![Service {
            uuid: SERVICE_UUID,
            primary: true,
            characteristics: vec![Characteristic {
                uuid: CHARACTERISTIC_UUID,
                read: Some(CharacteristicRead {
                    read: true,
                    fun: Box::new(|_req| Box::pin(async { Ok(Vec::new()) })),
                    ..Default::default()
                }),
                write: Some(CharacteristicWrite {
                    write: true,
                    write_without_response: true,
                    method: CharacteristicWriteMethod::Fun(Box::new(move |value, req| {
                        let tx = write_tx.clone();
                        let link = format!("gatt:{}", req.device_address);
                        let fresh = {
                            let mut s = write_seen.lock().unwrap();
                            s.insert(link.clone(), Instant::now()).is_none()
                        };
                        Box::pin(async move {
                            if fresh {
                                let _ = tx.send(PeriphEvent::Up { link: link.clone() }).await;
                            }
                            let _ = tx.send(PeriphEvent::Frame { link, data: value }).await;
                            Ok(())
                        })
                    })),
                    ..Default::default()
                }),
                notify: Some(CharacteristicNotify {
                    notify: true,
                    method: CharacteristicNotifyMethod::Fun(Box::new(move |n| {
                        let slot = notif_slot.clone();
                        let log = log_tx.clone();
                        Box::pin(async move {
                            let _ = log.send(PeriphEvent::Log("a central subscribed to notifications".into())).await;
                            *slot.lock().await = Some(n);
                        })
                    })),
                    ..Default::default()
                }),
                ..Default::default()
            }],
            ..Default::default()
        }],
        ..Default::default()
    };
    let app_handle = adapter
        .serve_gatt_application(app)
        .await
        .map_err(|e| format!("GATT server: {e}"))?;

    let adv = Advertisement {
        advertisement_type: AdvType::Peripheral,
        service_uuids: BTreeSet::from([SERVICE_UUID]),
        service_data: BTreeMap::new(),
        discoverable: None,
        local_name: None,
        ..Default::default()
    };
    let adv_handle = adapter.advertise(adv).await.map_err(|e| format!("advertising: {e}"))?;

    // Owner task: keeps the session, application and advertisement alive and
    // pushes outbound frames to whoever is subscribed.
    let owner_tx = ev_tx.clone();
    tokio::spawn(async move {
        let _session = session;
        let _app = app_handle;
        let _adv = adv_handle;
        let mut sweep = tokio::time::interval(Duration::from_secs(5));
        loop {
            tokio::select! {
                frame = notify_rx.recv() => {
                    let Some(frame) = frame else { break };
                    let mut slot = notifier.lock().await;
                    if let Some(n) = slot.as_mut() {
                        if n.is_stopped() {
                            *slot = None;
                        } else if let Err(e) = n.notify(frame).await {
                            let _ = owner_tx.send(PeriphEvent::Log(format!("notify failed: {e}"))).await;
                        }
                    }
                }
                _ = sweep.tick() => {
                    let gone: Vec<String> = {
                        let mut s = seen.lock().unwrap();
                        let gone: Vec<String> = s.iter().filter(|(_, t)| t.elapsed() > SILENCE).map(|(k, _)| k.clone()).collect();
                        for k in &gone { s.remove(k); }
                        gone
                    };
                    for link in gone {
                        // Silent for 90 s while it may still hold the radio
                        // link: drop that too so it reconnects cleanly.
                        if let Some(addr) = link.strip_prefix("gatt:") {
                            for a in drop_stale_links(Some(addr)).await {
                                let _ = owner_tx.send(PeriphEvent::Log(format!("disconnected silent mesh link {a} so it can reconnect"))).await;
                            }
                        }
                        let _ = owner_tx.send(PeriphEvent::Down { link }).await;
                    }
                }
            }
        }
    });

    Ok(Peripheral { events: ev_rx, notify: notify_tx, adapter: adapter_name })
}
