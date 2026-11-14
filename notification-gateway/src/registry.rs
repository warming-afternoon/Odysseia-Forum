//! 按用户定向发送有界状态提示，并可按会话撤销。
use std::{collections::{HashMap, HashSet}, time::{Duration, Instant}};
use tokio::sync::watch;

#[derive(Clone, Copy, Debug, Default)]
pub struct StateChange {
    pub revision: u64,
    pub revoked: bool,
}

struct Entry {
    session_hash: String,
    tx: watch::Sender<StateChange>,
}

#[derive(Default)]
pub struct Registry {
    next_id: u64,
    sequence: u64,
    by_user: HashMap<String, HashMap<u64, Entry>>,
    by_session: HashMap<String, HashSet<(String, u64)>>,
    recent_users: HashMap<String, Instant>,
    recent_sessions: HashMap<String, Instant>,
    blocked_until: Option<Instant>,
}

impl Registry {
    pub fn sequence(&self) -> u64 { self.sequence }

    /// 短期拒绝近期已撤销的主体，覆盖总线事件早于 Redis 失效投影的竞态。
    pub fn can_register(&self, user: &str, session: &str) -> bool {
        let now = Instant::now();
        !self.blocked_until.is_some_and(|deadline| deadline > now)
            && !self.recent_users.get(user).is_some_and(|deadline| *deadline > now)
            && !self.recent_sessions.get(session).is_some_and(|deadline| *deadline > now)
    }

    fn remember_invalidated(&mut self, value: &str, is_session: bool) {
        let now = Instant::now();
        let deadline = now + Duration::from_secs(35);
        let map = if is_session { &mut self.recent_sessions } else { &mut self.recent_users };
        // 有界保存；超限时暂时拒绝所有新连接，绝不静默放过撤销。
        if map.len() >= 100_000 {
            map.retain(|_, expiry| *expiry > now);
        }
        if map.len() >= 100_000 && !map.contains_key(value) {
            self.blocked_until = Some(deadline);
        } else {
            map.insert(value.to_owned(), deadline);
        }
    }

    pub fn register(&mut self, user: &str, session_hash: &str) -> (u64, watch::Receiver<StateChange>) {
        self.next_id = self.next_id.wrapping_add(1);
        let id = self.next_id;
        let (tx, rx) = watch::channel(StateChange::default());
        self.by_user.entry(user.to_owned()).or_default().insert(id, Entry {
            session_hash: session_hash.to_owned(), tx,
        });
        self.by_session.entry(session_hash.to_owned()).or_default().insert((user.to_owned(), id));
        (id, rx)
    }

    pub fn remove(&mut self, user: &str, id: u64) {
        if let Some(entries) = self.by_user.get_mut(user) {
            if let Some(entry) = entries.remove(&id) {
                if let Some(sessions) = self.by_session.get_mut(&entry.session_hash) {
                    sessions.remove(&(user.to_owned(), id));
                    if sessions.is_empty() { self.by_session.remove(&entry.session_hash); }
                }
            }
            if entries.is_empty() { self.by_user.remove(user); }
        }
    }

    pub fn changed(&self, user: &str) {
        if let Some(entries) = self.by_user.get(user) {
            for entry in entries.values() {
                entry.tx.send_modify(|state| state.revision = state.revision.wrapping_add(1));
            }
        }
    }

    pub fn invalidate_user(&mut self, user: &str) {
        self.sequence = self.sequence.wrapping_add(1);
        self.remember_invalidated(user, false);
        if let Some(entries) = self.by_user.get(user) {
            for entry in entries.values() { entry.tx.send_modify(|state| state.revoked = true); }
        }
    }

    pub fn invalidate_session(&mut self, hash: &str) {
        self.sequence = self.sequence.wrapping_add(1);
        self.remember_invalidated(hash, true);
        if let Some(sessions) = self.by_session.get(hash) {
            for (user, id) in sessions {
                if let Some(entry) = self.by_user.get(user).and_then(|entries| entries.get(id)) {
                    entry.tx.send_modify(|state| state.revoked = true);
                }
            }
        }
    }

    pub fn invalidate_all(&mut self) {
        self.sequence = self.sequence.wrapping_add(1);
        self.blocked_until = Some(Instant::now() + Duration::from_secs(35));
        for entries in self.by_user.values() {
            for entry in entries.values() { entry.tx.send_modify(|state| state.revoked = true); }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::Registry;

    #[test]
    fn rejects_recent_invalidation_before_registration() {
        let mut registry = Registry::default();
        registry.invalidate_session("session");
        assert!(!registry.can_register("123", "session"));
        assert!(registry.can_register("123", "other"));
        registry.invalidate_user("456");
        assert!(!registry.can_register("456", "other"));
        assert!(registry.can_register("123", "other"));
    }

    #[tokio::test]
    async fn notifies_all_devices_and_revokes_only_matching_session() {
        let mut registry = Registry::default();
        let (a, mut first) = registry.register("11", "one");
        let (b, mut second) = registry.register("11", "two");
        let (_, mut other) = registry.register("22", "one");
        registry.changed("11");
        first.changed().await.unwrap();
        second.changed().await.unwrap();
        assert_eq!(first.borrow_and_update().revision, 1);
        assert_eq!(second.borrow_and_update().revision, 1);
        assert!(!other.has_changed().unwrap());
        registry.invalidate_session("one");
        first.changed().await.unwrap();
        other.changed().await.unwrap();
        assert!(first.borrow().revoked && other.borrow().revoked);
        assert!(!second.borrow().revoked);
        registry.remove("11", a);
        registry.remove("11", b);
    }
}
