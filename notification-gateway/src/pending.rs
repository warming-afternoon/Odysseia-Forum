//! 有界、按用户合并的通知失效提示。
//!
//! 队列满时必须通知调用者执行全量重新校准，不能悄悄丢弃状态。
use std::collections::{HashSet, VecDeque};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InsertResult {
    Added,
    AlreadyPending,
    Overflow,
}

#[derive(Debug)]
pub struct PendingUsers {
    capacity: usize,
    ordered: VecDeque<String>,
    present: HashSet<String>,
}

impl PendingUsers {
    pub fn new(capacity: usize) -> Self {
        Self { capacity, ordered: VecDeque::new(), present: HashSet::new() }
    }

    pub fn insert(&mut self, user_id: &str) -> InsertResult {
        if self.present.contains(user_id) {
            return InsertResult::AlreadyPending;
        }
        if self.present.len() >= self.capacity {
            return InsertResult::Overflow;
        }
        let user_id = user_id.to_owned();
        self.present.insert(user_id.clone());
        self.ordered.push_back(user_id);
        InsertResult::Added
    }

    pub fn pop(&mut self) -> Option<String> {
        let user_id = self.ordered.pop_front()?;
        self.present.remove(&user_id);
        Some(user_id)
    }

    pub fn len(&self) -> usize { self.present.len() }
    pub fn is_empty(&self) -> bool { self.present.is_empty() }
}

#[cfg(test)]
mod tests {
    use super::{InsertResult, PendingUsers};

    #[test]
    fn coalesces_and_preserves_order() {
        let mut q = PendingUsers::new(2);
        assert_eq!(q.insert("1"), InsertResult::Added);
        assert_eq!(q.insert("1"), InsertResult::AlreadyPending);
        assert_eq!(q.insert("2"), InsertResult::Added);
        assert_eq!(q.len(), 2);
        assert_eq!(q.pop().as_deref(), Some("1"));
        assert_eq!(q.pop().as_deref(), Some("2"));
        assert!(q.is_empty());
    }

    #[test]
    fn signals_overflow_without_eviction() {
        let mut q = PendingUsers::new(1);
        assert_eq!(q.insert("1"), InsertResult::Added);
        assert_eq!(q.insert("2"), InsertResult::Overflow);
        assert_eq!(q.pop().as_deref(), Some("1"));
        assert_eq!(q.insert("2"), InsertResult::Added);
    }

    #[test]
    fn zero_capacity_always_overflows() {
        let mut q = PendingUsers::new(0);
        assert_eq!(q.insert("1"), InsertResult::Overflow);
        assert!(q.is_empty());
    }
}
