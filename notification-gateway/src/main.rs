//! 仅传递通知状态变化。服务尚须经过隔离安全、压力与故障测试。
use std::{env, net::SocketAddr, sync::{Arc, atomic::{AtomicBool, Ordering}}, time::Duration};
use axum::{extract::{ws::{Message, WebSocket, WebSocketUpgrade}, State}, http::{HeaderMap, StatusCode}, response::IntoResponse, routing::get, Router};
use futures_util::StreamExt;
use serde::Deserialize;
use sha2::{Digest, Sha256};
use tokio::{net::TcpListener, sync::{Mutex, Semaphore, watch}, time::{timeout, Instant}};
use forum_notification_gateway::{auth, registry::Registry};

#[derive(Clone)]
struct AppState {
    redis: redis::aio::MultiplexedConnection,
    registry: Arc<Mutex<Registry>>,
    ready: Arc<AtomicBool>,
    capacity: Arc<Semaphore>,
    capacity_limit: usize,
    pending_auth: Arc<Semaphore>,
    origin: Arc<str>,
    shutdown: watch::Receiver<bool>,
}

async fn health(State(state): State<AppState>) -> impl IntoResponse {
    let ready = state.ready.load(Ordering::Acquire) && !*state.shutdown.borrow();
    let status = if ready { StatusCode::OK } else { StatusCode::SERVICE_UNAVAILABLE };
    let active = state.capacity_limit.saturating_sub(state.capacity.available_permits());
    (status, axum::Json(serde_json::json!({"ready": ready, "connections": active, "limit": state.capacity_limit})))
}

#[derive(Deserialize)]
struct AuthFrame { r#type: String, ticket: String }

async fn upgrade(State(state): State<AppState>, headers: HeaderMap, ws: WebSocketUpgrade) -> impl IntoResponse {
    if !state.ready.load(Ordering::Acquire) || *state.shutdown.borrow()
        || headers.get("origin").and_then(|v| v.to_str().ok()) != Some(state.origin.as_ref()) {
        return StatusCode::FORBIDDEN.into_response();
    }
    let Ok(permit) = state.capacity.clone().try_acquire_owned() else {
        return StatusCode::SERVICE_UNAVAILABLE.into_response();
    };
    let Ok(pending) = state.pending_auth.clone().try_acquire_owned() else {
        return StatusCode::SERVICE_UNAVAILABLE.into_response();
    };
    ws.read_buffer_size(4096)
        .write_buffer_size(4096)
        .max_write_buffer_size(8192)
        .max_message_size(2048)
        .max_frame_size(2048)
        .on_upgrade(move |socket| async move {
            let _permit = permit;
            handle_socket(socket, state, pending).await;
        }).into_response()
}

async fn send(socket: &mut WebSocket, text: &'static str) -> bool {
    matches!(timeout(Duration::from_secs(5), socket.send(Message::Text(text.into()))).await, Ok(Ok(())))
}

async fn handle_socket(mut socket: WebSocket, state: AppState, pending: tokio::sync::OwnedSemaphorePermit) {
    let initial_sequence = state.registry.lock().await.sequence();
    let Ok(Some(Ok(Message::Text(raw)))) = timeout(Duration::from_secs(8), socket.recv()).await else { return };
    let Ok(frame) = serde_json::from_str::<AuthFrame>(&raw) else { return };
    if frame.r#type != "authenticate" || !state.ready.load(Ordering::Acquire) { return; }
    let mut redis = state.redis.clone();
    let Ok(Ok(Some(claims))) = timeout(Duration::from_secs(3), auth::consume(&mut redis, &frame.ticket)).await else { return };
    drop(pending);
    let (id, mut changes) = {
        let mut registry = state.registry.lock().await;
        if registry.sequence() != initial_sequence || !state.ready.load(Ordering::Acquire)
            || *state.shutdown.borrow() || !registry.can_register(&claims.user_id, &claims.session_id_hash) { return; }
        registry.register(&claims.user_id, &claims.session_id_hash)
    };
    // 注册之后再校验，避免消费票据和加入用户索引间的撤销竞态。
    let valid = matches!(timeout(Duration::from_secs(3), auth::still_valid(&mut redis, &claims)).await, Ok(Ok(true)));
    if valid && !changes.borrow().revoked && state.ready.load(Ordering::Acquire) && !*state.shutdown.borrow()
        && send(&mut socket, "{\"type\":\"ready\"}").await {
        let mut heartbeat = tokio::time::interval(Duration::from_secs(35));
        heartbeat.tick().await;
        let mut last_receive = Instant::now();
        let connected_at = Instant::now();
        // 限制连接寿命并错峰重认证，作为认证事件丢失的额外安全边界。
        let digest = Sha256::digest(claims.session_id_hash.as_bytes());
        let jitter = u16::from_be_bytes([digest[0], digest[1]]) as u64 % 600;
        let max_age = Duration::from_secs(1500 + jitter);
        loop {
            tokio::select! {
                result = changes.changed() => {
                    if result.is_err() || changes.borrow_and_update().revoked { break; }
                    if !send(&mut socket, "{\"type\":\"notification.changed\"}").await { break; }
                }
                incoming = socket.recv() => match incoming {
                    Some(Ok(Message::Close(_))) | None | Some(Err(_)) => break,
                    Some(Ok(_)) => last_receive = Instant::now(),
                },
                _ = heartbeat.tick() => {
                    if last_receive.elapsed() > Duration::from_secs(90) || connected_at.elapsed() > max_age { break; }
                    if !matches!(timeout(Duration::from_secs(5), socket.send(Message::Ping(Vec::new().into()))).await, Ok(Ok(()))) { break; }
                }
            }
        }
    }
    state.registry.lock().await.remove(&claims.user_id, id);
}

#[derive(Deserialize)]
struct ChangeEvent { recipient_id: String }
#[derive(Deserialize)]
struct AuthEvent {
    user_id: Option<String>,
    session_id_hash: Option<String>,
    affected_user_ids: Option<Vec<String>>,
    merged_user_id: Option<String>,
    discord_user_id: Option<String>,
    version: Option<u64>,
}

fn valid_user(value: &str) -> bool {
    !value.is_empty() && value.len() <= 19 && !value.starts_with('0') && value.bytes().all(|b| b.is_ascii_digit())
}
fn valid_session(value: &str) -> bool {
    !value.is_empty() && value.len() <= 128 && value.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
}

async fn listen(mut sub: async_nats::Subscriber, subject: &'static str, state: AppState, shutdown: watch::Sender<bool>) {
    while let Some(message) = sub.next().await {
        if subject == "events.forum.v1.notification.changed" {
            match serde_json::from_slice::<ChangeEvent>(&message.payload) {
                Ok(event) if valid_user(&event.recipient_id) => state.registry.lock().await.changed(&event.recipient_id),
                // 无法定位受影响用户时关闭连接，促使所有用户重新校准。
                _ => state.registry.lock().await.invalidate_all(),
            }
            continue;
        }
        let Ok(event) = serde_json::from_slice::<AuthEvent>(&message.payload) else {
            state.registry.lock().await.invalidate_all();
            continue;
        };
        let mut registry = state.registry.lock().await;
        match subject {
            "events.auth.v1.session_invalidated" => match event.session_id_hash.as_deref() {
                Some(hash) if valid_session(hash) => registry.invalidate_session(hash),
                _ => registry.invalidate_all(),
            },
            "events.auth.v1.user_invalidated" => match event.user_id.as_deref() {
                Some(user) if valid_user(user) => registry.invalidate_user(user),
                _ => registry.invalidate_all(),
            },
            "events.identity.v1.binding_changed" => {
                let valid_primary = event.user_id.as_deref().is_some_and(valid_user);
                let valid_discord = event.discord_user_id.as_deref().is_some_and(valid_user);
                let valid_merged = event.merged_user_id.as_deref().map_or(true, valid_user);
                let valid_version = event.version.is_some_and(|version| version > 0);
                let mut expected = std::collections::HashSet::new();
                if let Some(user) = event.user_id.as_ref() { expected.insert(user.as_str()); }
                if let Some(user) = event.merged_user_id.as_ref() { expected.insert(user.as_str()); }
                match event.affected_user_ids {
                    Some(users) if valid_primary && valid_discord && valid_merged && valid_version
                        && !users.is_empty() && users.len() <= 2
                        && users.iter().all(|user| valid_user(user))
                        && users.iter().map(String::as_str).collect::<std::collections::HashSet<_>>() == expected
                        && users.len() == expected.len() => {
                            for user in users { registry.invalidate_user(&user); }
                        },
                    _ => registry.invalidate_all(),
                }
            },
            _ => registry.invalidate_all(),
        }
    }
    state.ready.store(false, Ordering::Release);
    state.registry.lock().await.invalidate_all();
    let _ = shutdown.send(true);
}

async fn monitor_generation(state: AppState, initial: String, shutdown: watch::Sender<bool>) {
    let mut redis = state.redis.clone();
    loop {
        tokio::time::sleep(Duration::from_secs(5)).await;
        let value = timeout(Duration::from_secs(3), async {
            redis::cmd("GET").arg("forum:fluxer:auth:v1:generation")
                .query_async::<Option<String>>(&mut redis).await
        }).await;
        if !matches!(value, Ok(Ok(ref value)) if value.as_deref().unwrap_or("0") == initial) {
            state.ready.store(false, Ordering::Release);
            state.registry.lock().await.invalidate_all();
            let _ = shutdown.send(true);
            return;
        }
    }
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    tracing_subscriber::fmt().with_env_filter(tracing_subscriber::EnvFilter::from_default_env()).init();
    let origin = env::var("FORUM_NOTIFICATION_ORIGIN")?;
    if !origin.starts_with("https://") || origin.contains(',') { return Err("invalid origin".into()); }
    let address: SocketAddr = env::var("FORUM_NOTIFICATION_BIND").unwrap_or_else(|_| "127.0.0.1:10920".into()).parse()?;
    if !address.ip().is_loopback() { return Err("gateway must bind to loopback".into()); }
    let max_connections: usize = env::var("FORUM_NOTIFICATION_MAX_CONNECTIONS").unwrap_or_else(|_| "10000".into()).parse()?;
    if !(1..=100_000).contains(&max_connections) { return Err("invalid connection limit".into()); }
    let max_pending: usize = env::var("FORUM_NOTIFICATION_MAX_PENDING_AUTH").unwrap_or_else(|_| "256".into()).parse()?;
    if !(1..=4096).contains(&max_pending) { return Err("invalid pending authentication limit".into()); }
    let redis = redis::Client::open(env::var("FORUM_NOTIFICATION_REDIS_URL")?)
        .map_err(|_| "invalid Redis configuration")?
        .get_multiplexed_async_connection().await.map_err(|_| "Redis unavailable")?;
    let registry = Arc::new(Mutex::new(Registry::default()));
    let ready = Arc::new(AtomicBool::new(false));
    let (shutdown, mut stop) = watch::channel(false);
    let callback_registry = registry.clone();
    let callback_ready = ready.clone();
    let callback_shutdown = shutdown.clone();
    let nats_url = env::var("FORUM_NOTIFICATION_NATS_URL")
        .map_err(|_| "invalid NATS server address")?;
    let (nats_options, nats_server) =
        forum_notification_gateway::nats_config::connection_options(&nats_url)?;
    let nats = nats_options.event_callback(move |event| {
        let registry = callback_registry.clone();
        let ready = callback_ready.clone();
        let shutdown = callback_shutdown.clone();
        async move {
            if !matches!(event, async_nats::Event::Connected | async_nats::Event::LameDuckMode) {
                ready.store(false, Ordering::Release);
                registry.lock().await.invalidate_all();
                let _ = shutdown.send(true);
            }
        }
    }).connect(nats_server).await.map_err(|_| "NATS unavailable")?;
    let state = AppState { redis, registry, ready, capacity: Arc::new(Semaphore::new(max_connections)), capacity_limit: max_connections, pending_auth: Arc::new(Semaphore::new(max_pending)), origin: origin.into(), shutdown: stop.clone() };
    for subject in ["events.forum.v1.notification.changed", "events.auth.v1.session_invalidated", "events.auth.v1.user_invalidated", "events.identity.v1.binding_changed"] {
        let sub = nats.subscribe(subject).await?;
        tokio::spawn(listen(sub, subject, state.clone(), shutdown.clone()));
    }
    nats.flush().await?;
    let mut generation_redis = state.redis.clone();
    let generation: Option<String> = redis::cmd("GET")
        .arg("forum:fluxer:auth:v1:generation")
        .query_async(&mut generation_redis).await?;
    if *stop.borrow() { return Err("event bus unavailable".into()); }
    tokio::spawn(monitor_generation(state.clone(), generation.unwrap_or_else(|| "0".into()), shutdown.clone()));
    state.ready.store(true, Ordering::Release);
    let listener = TcpListener::bind(address).await?;
    axum::serve(listener, Router::new().route("/health", get(health)).route("/ws/notifications", get(upgrade)).with_state(state))
        .with_graceful_shutdown(async move { let _ = stop.changed().await; }).await?;
    Ok(())
}
