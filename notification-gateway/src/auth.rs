//! 一次性票据消费与认证代次校验。
use redis::aio::MultiplexedConnection;
use serde::Deserialize;
use sha2::{Digest, Sha256};

const AUTH_PREFIX: &str = "forum:fluxer:auth:v1";
const TICKET_PREFIX: &str = "forum:notification:ticket:v1:";

#[derive(Clone, Debug, Deserialize)]
pub struct Claims {
    pub user_id: String,
    pub session_id_hash: String,
    pub generation: String,
    pub user_epoch: String,
    pub session_epoch: String,
}

fn valid_claims(claims: &Claims) -> bool {
    !claims.user_id.is_empty() && claims.user_id.len() <= 19
        && !claims.user_id.starts_with('0')
        && claims.user_id.bytes().all(|b| b.is_ascii_digit())
        && !claims.session_id_hash.is_empty() && claims.session_id_hash.len() <= 128
        && claims.session_id_hash.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
        && [&claims.generation, &claims.user_epoch, &claims.session_epoch]
            .iter().all(|s| !s.is_empty() && s.len() <= 20 && s.bytes().all(|b| b.is_ascii_digit()))
}

/// 即使校验失败也立即消费票据，避免重放。
pub async fn consume(conn: &mut MultiplexedConnection, ticket: &str) -> Result<Option<Claims>, redis::RedisError> {
    if ticket.len() < 32 || ticket.len() > 128 || !ticket.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-') {
        return Ok(None);
    }
    let digest = format!("{:x}", Sha256::digest(ticket.as_bytes()));
    let key = format!("{TICKET_PREFIX}{digest}");
    let raw: Option<String> = redis::cmd("GETDEL").arg(key).query_async(conn).await?;
    let Some(raw) = raw else { return Ok(None) };
    let Ok(claims) = serde_json::from_str::<Claims>(&raw) else { return Ok(None) };
    if !valid_claims(&claims) || !still_valid(conn, &claims).await? { return Ok(None); }
    Ok(Some(claims))
}

/// 每次校验使用单次 Redis 命令；认证总线断线时网关应立即关闭所有连接。
pub async fn still_valid(conn: &mut MultiplexedConnection, claims: &Claims) -> Result<bool, redis::RedisError> {
    if !valid_claims(claims) { return Ok(false); }
    let keys = [
        format!("{AUTH_PREFIX}:generation"),
        format!("{AUTH_PREFIX}:user_epoch:{}", claims.user_id),
        format!("{AUTH_PREFIX}:session_epoch:{}", claims.session_id_hash),
    ];
    let values: Vec<Option<String>> = redis::cmd("MGET").arg(keys.as_slice()).query_async(conn).await?;
    if values.len() != 3 { return Ok(false); }
    Ok(values[0].as_deref().unwrap_or("0") == claims.generation
        && values[1].as_deref().unwrap_or("0") == claims.user_epoch
        && values[2].as_deref().unwrap_or("0") == claims.session_epoch)
}

#[cfg(test)]
mod tests {
    use super::{valid_claims, Claims};

    fn claims() -> Claims {
        Claims { user_id: "123".into(), session_id_hash: "session_hash-1".into(),
            generation: "4".into(), user_epoch: "2".into(), session_epoch: "3".into() }
    }

    #[test]
    fn rejects_untrusted_redis_claim_values() {
        let mut value = claims();
        assert!(valid_claims(&value));
        value.user_id = "../other".into();
        assert!(!valid_claims(&value));
        value = claims();
        value.session_id_hash = "a:b".into();
        assert!(!valid_claims(&value));
        value = claims();
        value.generation = "-1".into();
        assert!(!valid_claims(&value));
    }
}
