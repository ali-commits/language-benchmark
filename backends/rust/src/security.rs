use argon2::{
    Algorithm, Argon2, Params, Version,
    password_hash::{PasswordHasher, PasswordVerifier, phc::PasswordHash},
};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use chrono::{DateTime, Datelike, SecondsFormat, Utc};
use jsonwebtoken::{
    Algorithm as JwtAlgorithm, DecodingKey, EncodingKey, Header, Validation, decode, encode,
};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

pub fn hash_password(password: &str) -> Result<String, String> {
    let params = Params::new(65536, 3, 1, Some(32)).map_err(|e| e.to_string())?;
    let a = Argon2::new(Algorithm::Argon2id, Version::V0x13, params);
    let h: PasswordHash = a
        .hash_password(password.as_bytes())
        .map_err(|e| e.to_string())?;
    Ok(h.to_string())
}
pub fn verify_password(password: &str, hash: &str) -> bool {
    if !hash.starts_with("$argon2id$v=19$m=65536,t=3,p=1$") {
        return false;
    }
    Argon2::default()
        .verify_password(password.as_bytes(), hash)
        .is_ok()
}
#[derive(Serialize, Deserialize)]
struct Claims {
    sub: String,
    iat: i64,
    exp: i64,
    iss: String,
    aud: Value,
}
pub fn issue_token(id: i64, secret: &str, ttl: i64) -> Result<String, String> {
    let now = Utc::now().timestamp();
    encode(
        &Header::new(JwtAlgorithm::HS256),
        &Claims {
            sub: id.to_string(),
            iat: now,
            exp: now + ttl,
            iss: "language-benchmark".into(),
            aud: json!("social-api"),
        },
        &EncodingKey::from_secret(secret.as_bytes()),
    )
    .map_err(|e| e.to_string())
}
pub fn decode_token(value: &str, secret: &str) -> Result<i64, String> {
    let mut v = Validation::new(JwtAlgorithm::HS256);
    v.leeway = 0;
    v.validate_nbf = true;
    v.set_issuer(&["language-benchmark"]);
    v.set_audience(&["social-api"]);
    v.set_required_spec_claims(&["sub", "iat", "exp", "iss", "aud"]);
    let c = decode::<Claims>(value, &DecodingKey::from_secret(secret.as_bytes()), &v)
        .map_err(|e| e.to_string())?
        .claims;
    if c.exp <= Utc::now().timestamp() {
        return Err("expired token".into());
    };
    if c.iat > Utc::now().timestamp() + 30 {
        return Err("invalid issued time".into());
    };
    let id = c.sub.parse::<i64>().map_err(|e| e.to_string())?;
    if id <= 0 {
        return Err("invalid subject".into());
    }
    Ok(id)
}
pub fn encode_cursor(stamp: DateTime<Utc>, id: i64) -> String {
    URL_SAFE_NO_PAD.encode(
        serde_json::to_vec(&json!([
            stamp.to_rfc3339_opts(SecondsFormat::Micros, false),
            id
        ]))
        .unwrap(),
    )
}
pub fn decode_cursor(value: &str) -> Result<(DateTime<Utc>, i64), String> {
    let invalid = || "invalid cursor".to_string();
    if value.is_empty() || value.len() > 256 {
        return Err(invalid());
    };
    let data = URL_SAFE_NO_PAD
        .decode(value.trim_end_matches('='))
        .map_err(|_| invalid())?;
    let pair: Value = serde_json::from_slice(&data).map_err(|_| invalid())?;
    let values = pair
        .as_array()
        .filter(|v| v.len() == 2)
        .ok_or_else(invalid)?;
    let stamp = DateTime::parse_from_rfc3339(values[0].as_str().ok_or_else(invalid)?)
        .map_err(|_| invalid())?;
    if stamp.year() < 1 || stamp.year() > 9999 {
        return Err(invalid());
    };
    let id = values[1].as_i64().filter(|v| *v > 0).ok_or_else(invalid)?;
    Ok((stamp.to_utc(), id))
}
