//! 解析 NATS 连接地址，显式传递认证并移除可被日志记录的凭据。
use async_nats::{ConnectOptions, ServerAddr};
use percent_encoding::percent_decode_str;

/// 将地址凭据交给 CONNECT，保留原有协议、主机和 TLS 要求。
pub fn connection_options(value: &str) -> Result<(ConnectOptions, ServerAddr), &'static str> {
    let server: ServerAddr = value.parse().map_err(|_| "invalid NATS server address")?;
    let mut address = server.into_inner();
    let credentials = match (address.username(), address.password()) {
        ("", None) => None,
        (user, Some(password)) if !user.is_empty() && !password.is_empty() => {
            Some((decode_credential(user)?, decode_credential(password)?))
        }
        _ => return Err("invalid NATS authentication configuration"),
    };

    // 凭据只保留在认证选项内，连接地址及其错误信息不再包含用户信息。
    address
        .set_password(None)
        .map_err(|_| "invalid NATS server address")?;
    address
        .set_username("")
        .map_err(|_| "invalid NATS server address")?;
    let sanitized = address
        .as_str()
        .parse()
        .map_err(|_| "invalid NATS server address")?;
    let options = match credentials {
        Some((user, password)) => ConnectOptions::new().user_and_password(user, password),
        None => ConnectOptions::new(),
    };
    Ok((options, sanitized))
}

/// 严格解码一次 URL 凭据，保留加号并拒绝无效转义及控制字符。
fn decode_credential(value: &str) -> Result<String, &'static str> {
    let bytes = value.as_bytes();
    let mut position = 0;
    while position < bytes.len() {
        if bytes[position] == b'%' {
            if position + 2 >= bytes.len()
                || !bytes[position + 1].is_ascii_hexdigit()
                || !bytes[position + 2].is_ascii_hexdigit()
            {
                return Err("invalid NATS authentication configuration");
            }
            position += 3;
        } else {
            position += 1;
        }
    }
    let decoded = percent_decode_str(value)
        .decode_utf8()
        .map_err(|_| "invalid NATS authentication configuration")?;
    if decoded.is_empty() || decoded.chars().any(char::is_control) {
        return Err("invalid NATS authentication configuration");
    }
    Ok(decoded.into_owned())
}
