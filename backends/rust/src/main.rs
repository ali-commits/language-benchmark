mod security;
mod tuning;
mod validation;
use axum::{
    Json, Router,
    body::{Body, to_bytes},
    extract::{Request, State},
    http::StatusCode,
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::{get, post, put},
};
#[cfg(test)]
use base64::Engine;
use chrono::{DateTime, Utc};
use security::*;
use serde_json::{Value, json};
use sqlx::{
    Connection, PgConnection, PgPool, Postgres, Row,
    pool::PoolConnection,
    postgres::{PgConnectOptions, PgPoolOptions, PgRow},
};
use std::{
    collections::HashMap,
    sync::{
        Arc,
        atomic::{AtomicU64, Ordering},
    },
    time::Instant,
};
use tokio::sync::Semaphore;
use tuning::*;
use validation::*;
#[derive(Clone)]
struct App {
    pool: PgPool,
    secret: Arc<String>,
    ttl: i64,
    hashes: Arc<Semaphore>,
    dummy: Arc<String>,
    acquires: Arc<AtomicU64>,
    acquire_ns: Arc<AtomicU64>,
}
impl App {
    async fn checkout(&self) -> Result<PoolConnection<Postgres>, ApiError> {
        let start = Instant::now();
        let c = self.pool.acquire().await?;
        self.acquires.fetch_add(1, Ordering::Relaxed);
        self.acquire_ns
            .fetch_add(start.elapsed().as_nanos() as u64, Ordering::Relaxed);
        Ok(c)
    }
    async fn password_hash(&self, password: String) -> Result<String, ApiError> {
        let permit = self
            .hashes
            .clone()
            .acquire_owned()
            .await
            .map_err(|_| error(500, "internal server error"))?;
        tokio::task::spawn_blocking(move || {
            let _permit = permit;
            hash_password(&password)
        })
        .await
        .map_err(|_| error(500, "internal server error"))?
        .map_err(|_| error(500, "internal server error"))
    }
    async fn password_verify(&self, password: String, hash: String) -> Result<bool, ApiError> {
        let permit = self
            .hashes
            .clone()
            .acquire_owned()
            .await
            .map_err(|_| error(500, "internal server error"))?;
        tokio::task::spawn_blocking(move || {
            let _permit = permit;
            verify_password(&password, &hash)
        })
        .await
        .map_err(|_| error(500, "internal server error"))
    }
}
fn env(key: &str, default: &str) -> String {
    std::env::var(key).unwrap_or_else(|_| default.into())
}
fn valid_ttl(ttl: i64) -> bool {
    (600..=86400).contains(&ttl)
}
fn database_options(dsn: &str, mode: &str) -> Result<PgConnectOptions, Box<dyn std::error::Error>> {
    let dsn = dsn.replacen("postgresql+psycopg://", "postgresql://", 1);
    let u = url::Url::parse(&dsn)?;
    if u.scheme() != "postgresql" {
        return Err("PostgreSQL required".into());
    };
    if mode == "aws" {
        let query: Vec<_> = u.query_pairs().collect();
        let ssl: Vec<_> = query
            .iter()
            .filter(|(k, _)| k == "sslmode")
            .map(|(_, v)| v)
            .collect();
        let root: Vec<_> = query
            .iter()
            .filter(|(k, _)| k == "sslrootcert")
            .map(|(_, v)| v)
            .collect();
        if ssl.len() != 1 || ssl[0] != "verify-full" || root.len() != 1 || root[0].trim().is_empty()
        {
            return Err("AWS requires sslmode=verify-full and sslrootcert".into());
        }
    };
    Ok(dsn
        .parse::<PgConnectOptions>()?
        .application_name("lb-backend")
        .options([("statement_timeout", "10000")]))
}
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let cpu = setting("CPU_PARALLELISM")?;
    let hashes = setting("HASH_CONCURRENCY")?;
    tokio::runtime::Builder::new_multi_thread()
        .worker_threads(cpu)
        .enable_all()
        .build()?
        .block_on(serve(hashes))
}
async fn serve(hashes: usize) -> Result<(), Box<dyn std::error::Error>> {
    let secret = env("JWT_SECRET", "");
    if secret.len() < 32 {
        return Err("JWT_SECRET must have at least 32 bytes".into());
    };
    let ttl = env("JWT_TTL_SECONDS", "3600").parse::<i64>()?;
    if !valid_ttl(ttl) {
        return Err("JWT_TTL_SECONDS must be between 600 and 86400".into());
    };
    let (max_pool, min_pool) = parse_pool_bounds(
        std::env::var("DB_POOL_MAX_SIZE").ok().as_deref(),
        std::env::var("DB_POOL_MIN_SIZE").ok().as_deref(),
    )?;
    let pool = PgPoolOptions::new()
        .max_connections(max_pool)
        .min_connections(min_pool)
        .idle_timeout(std::time::Duration::from_secs(300))
        .max_lifetime(std::time::Duration::from_secs(3600))
        .connect_lazy_with(database_options(
            &env("DATABASE_URL", ""),
            &env("MODE", "local"),
        )?);
    let state = App {
        pool,
        secret: Arc::new(secret),
        ttl,
        hashes: Arc::new(Semaphore::new(hashes)),
        dummy: Arc::new(hash_password("dummy-only-not-a-valid-user-password")?),
        acquires: Arc::new(AtomicU64::new(0)),
        acquire_ns: Arc::new(AtomicU64::new(0)),
    };
    let app = routes(state.clone());
    let address = format!("{}:{}", env("BIND_IP", "0.0.0.0"), env("PORT", "8000"));
    let listener = tokio::net::TcpListener::bind(&address).await?;
    write_receipt(
        std::env::var("RUN_DIR").ok().as_deref(),
        &env("RUN_ID", ""),
        state.hashes.available_permits(),
        state.pool.options().get_max_connections(),
        state.pool.options().get_min_connections(),
    )?;
    eprintln!("Rust social API listening on {address}");
    axum::serve(listener, app)
        .with_graceful_shutdown(shutdown())
        .await?;
    state.pool.close().await;
    Ok(())
}
async fn shutdown() {
    let mut term = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
        .expect("signal setup");
    tokio::select! {_=tokio::signal::ctrl_c()=>{},_=term.recv()=>{}}
}
fn routes(state: App) -> Router {
    Router::new()
        .route("/health/live", get(live))
        .route("/health/ready", get(ready))
        .route("/health/metrics", get(metrics))
        .route("/api/v1/auth/register", post(api))
        .route("/api/v1/auth/login", post(api))
        .route("/api/v1/users/me", get(api))
        .route("/api/v1/posts", post(api))
        .route("/api/v1/posts/{post_id}", get(api))
        .route("/api/v1/feed", get(api))
        .route("/api/v1/users/{target_id}/follow", put(api).delete(api))
        .route("/api/v1/posts/{post_id}/like", put(api).delete(api))
        .route("/api/v1/posts/{post_id}/comments", post(api).get(api))
        .layer(middleware::from_fn(body_limit))
        .with_state(state)
}
async fn body_limit(request: Request, next: Next) -> Response {
    if !matches!(request.method().as_str(), "POST" | "PUT" | "PATCH") {
        return next.run(request).await;
    };
    let (parts, body) = request.into_parts();
    match to_bytes(body, 16384).await {
        Ok(data) => next.run(Request::from_parts(parts, Body::from(data))).await,
        Err(_) => error(413, "request body too large").into_response(),
    }
}
async fn live() -> Json<Value> {
    Json(json!({"status":"ok"}))
}
async fn ready(State(s): State<App>) -> Result<Json<Value>, ApiError> {
    let mut c = s
        .checkout()
        .await
        .map_err(|_| error(503, "database unavailable"))?;
    sqlx::query("SELECT 1")
        .execute(&mut *c)
        .await
        .map_err(|_| error(503, "database unavailable"))?;
    Ok(Json(json!({"status":"ok"})))
}
async fn metrics(State(s): State<App>) -> Json<Value> {
    Json(
        json!({"connection_acquires":s.acquires.load(Ordering::Relaxed),"connection_acquire_ms":s.acquire_ns.load(Ordering::Relaxed) as f64/1e6}),
    )
}
fn identity(request: &Request, s: &App) -> Result<i64, ApiError> {
    let header = request
        .headers()
        .get("authorization")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    let p: Vec<&str> = header.split_whitespace().collect();
    if p.len() != 2 || !p[0].eq_ignore_ascii_case("Bearer") {
        return Err(error(401, "invalid credentials"));
    };
    decode_token(p[1], &s.secret).map_err(|_| error(401, "invalid credentials"))
}
fn id_param(value: &str, name: &str) -> Result<i64, ApiError> {
    value.parse::<i64>().map_err(|_| {
        invalid(
            "path",
            name,
            "int_parsing",
            "Input should be a valid integer, unable to parse string as an integer",
            json!(value),
            None,
        )
    })
}
fn pagination(
    query: &HashMap<String, String>,
) -> Result<(i64, Option<DateTime<Utc>>, i64), ApiError> {
    let limit = if let Some(v) = query.get("limit") {
        let n = v.parse::<i64>().map_err(|_| {
            invalid(
                "query",
                "limit",
                "int_parsing",
                "Input should be a valid integer, unable to parse string as an integer",
                json!(v),
                None,
            )
        })?;
        if n < 1 {
            return Err(invalid(
                "query",
                "limit",
                "greater_than_equal",
                "Input should be greater than or equal to 1",
                json!(v),
                Some(json!({"ge":1})),
            ));
        };
        if n > 100 {
            return Err(invalid(
                "query",
                "limit",
                "less_than_equal",
                "Input should be less than or equal to 100",
                json!(v),
                Some(json!({"le":100})),
            ));
        };
        n
    } else {
        20
    };
    if let Some(v) = query.get("cursor") {
        let (t, id) = decode_cursor(v).map_err(|_| error(422, "invalid cursor"))?;
        Ok((limit, Some(t), id))
    } else {
        Ok((limit, None, 0))
    }
}
fn comment_value(row: &PgRow) -> Value {
    let time: DateTime<Utc> = row.get("created_at");
    json!({"id":row.get::<i64,_>("id"),"content":row.get::<String,_>("content"),"created_at":time.to_rfc3339_opts(chrono::SecondsFormat::Micros,true),"author":{"id":row.get::<i64,_>("author_id"),"username":row.get::<String,_>("username")}})
}
fn post_value(row: &PgRow) -> Value {
    let mut p = comment_value(row);
    p["like_count"] = json!(row.get::<i64, _>("like_count"));
    p["comment_count"] = json!(row.get::<i64, _>("comment_count"));
    p["liked"] = json!(row.get::<bool, _>("liked"));
    p
}
const POST_SELECT: &str = "SELECT p.id,p.content,p.created_at,p.author_id,u.username,(SELECT count(*) FROM likes l WHERE l.post_id=p.id) AS like_count,(SELECT count(*) FROM comments c WHERE c.post_id=p.id) AS comment_count,EXISTS(SELECT 1 FROM likes l WHERE l.post_id=p.id AND l.user_id=$1) AS liked FROM posts p JOIN users u ON u.id=p.author_id";
fn page(rows: Vec<PgRow>, limit: i64, posts: bool) -> Value {
    let more = rows.len() as i64 > limit;
    let chosen: Vec<_> = rows.into_iter().take(limit as usize).collect();
    let cursor = if more {
        chosen
            .last()
            .map(|r| encode_cursor(r.get("created_at"), r.get("id")))
    } else {
        None
    };
    json!({"items":chosen.iter().map(if posts{post_value}else{comment_value}).collect::<Vec<_>>(),"next_cursor":cursor})
}
async fn exists(c: &mut PgConnection, table: &str, id: i64) -> Result<(), ApiError> {
    // table is an internal literal; all request values are bound parameters.
    let sql = format!("SELECT EXISTS(SELECT 1 FROM {table} WHERE id=$1)");
    let found: bool = sqlx::query_scalar(sqlx::AssertSqlSafe(sql.as_str()))
        .bind(id)
        .fetch_one(&mut *c)
        .await?;
    if !found {
        return Err(error(404, "not found"));
    };
    Ok(())
}
async fn api(State(s): State<App>, request: Request) -> Result<Response, ApiError> {
    let path = request.uri().path().to_owned();
    let method = request.method().as_str().to_owned();
    let query: HashMap<String, String> =
        url::form_urlencoded::parse(request.uri().query().unwrap_or("").as_bytes())
            .into_owned()
            .collect();
    let public = path == "/api/v1/auth/register" || path == "/api/v1/auth/login";
    let viewer = if public { 0 } else { identity(&request, &s)? };
    let parts: Vec<&str> = path.split('/').collect();
    let pid = if parts.get(3) == Some(&"posts") && parts.len() > 4 {
        Some(id_param(parts[4], "post_id")?)
    } else {
        None
    };
    let target = if parts.get(3) == Some(&"users") && parts.len() > 5 {
        Some(id_param(parts[4], "target_id")?)
    } else {
        None
    };
    let input = if method == "POST" {
        let b = to_bytes(request.into_body(), 16384)
            .await
            .map_err(|_| error(413, "request body too large"))?;
        if std::str::from_utf8(&b).is_err() {
            return Err(error(400, "There was an error parsing the body"));
        };
        if b.is_empty() {
            return Err(invalid(
                "body",
                "",
                "missing",
                "Field required",
                Value::Null,
                None,
            ));
        };
        let value = serde_json::from_slice(&b).map_err(|_| {
            invalid(
                "body",
                "",
                "json_invalid",
                "JSON decode error",
                Value::Null,
                None,
            )
        })?;
        validate_input(
            value,
            if path.ends_with("register") {
                "register"
            } else if path.ends_with("login") {
                "login"
            } else if path.ends_with("comments") {
                "comment"
            } else {
                "post"
            },
        )?
    } else {
        HashMap::new()
    };
    if path.ends_with("/register") {
        let h = s.password_hash(input["password"].clone()).await?;
        let mut c = s.checkout().await?;
        let row = sqlx::query(
            "INSERT INTO users(username,password_hash) VALUES ($1,$2) RETURNING id,username",
        )
        .bind(&input["username"])
        .bind(h)
        .fetch_one(&mut *c)
        .await
        .map_err(|e| {
            if e.as_database_error()
                .is_some_and(|d| d.is_unique_violation())
            {
                error(409, "username already exists")
            } else {
                e.into()
            }
        })?;
        return Ok((
            StatusCode::CREATED,
            Json(json!({"id":row.get::<i64,_>("id"),"username":row.get::<String,_>("username")})),
        )
            .into_response());
    }
    if path.ends_with("/login") {
        let mut c = s.checkout().await?;
        let row = sqlx::query("SELECT id,password_hash FROM users WHERE username=$1")
            .bind(&input["username"])
            .fetch_optional(&mut *c)
            .await?;
        drop(c);
        let h = row
            .as_ref()
            .map(|r| r.get::<String, _>("password_hash"))
            .unwrap_or_else(|| (*s.dummy).clone());
        let valid = s.password_verify(input["password"].clone(), h).await?;
        if !valid || row.is_none() {
            return Err(error(401, "invalid credentials"));
        };
        let token = issue_token(row.unwrap().get("id"), &s.secret, s.ttl)
            .map_err(|_| error(500, "internal server error"))?;
        return Ok(
            Json(json!({"access_token":token,"token_type":"bearer","expires_in":s.ttl}))
                .into_response(),
        );
    }
    let pagination = if path.ends_with("/feed") || method == "GET" && path.ends_with("/comments") {
        Some(pagination(&query)?)
    } else {
        None
    };
    let mut c = s.checkout().await?;
    let mut tx = c.begin().await?;
    let mut status = StatusCode::OK;
    let result: Value;
    if path == "/api/v1/users/me" {
        let row = sqlx::query("SELECT id,username FROM users WHERE id=$1")
            .bind(viewer)
            .fetch_optional(&mut *tx)
            .await?
            .ok_or_else(|| error(401, "invalid credentials"))?;
        result = json!({"id":row.get::<i64,_>("id"),"username":row.get::<String,_>("username")});
    } else if path == "/api/v1/posts" {
        let id: i64 =
            sqlx::query_scalar("INSERT INTO posts(author_id,content) VALUES ($1,$2) RETURNING id")
                .bind(viewer)
                .bind(&input["content"])
                .fetch_one(&mut *tx)
                .await?;
        // Both SQL fragments are internal constants; request values are bound.
        let sql = format!("{POST_SELECT} WHERE p.id=$2");
        result = post_value(
            &sqlx::query(sqlx::AssertSqlSafe(sql.as_str()))
                .bind(viewer)
                .bind(id)
                .fetch_one(&mut *tx)
                .await?,
        );
        status = StatusCode::CREATED;
    } else if path == "/api/v1/feed" {
        let (limit, time, id) = pagination.unwrap();
        let sql = format!(
            "{POST_SELECT} WHERE p.author_id IN (SELECT followed_id FROM follows WHERE follower_id=$1 UNION ALL SELECT $1) AND ($2::timestamptz IS NULL OR (p.created_at,p.id)<($2::timestamptz,$3)) ORDER BY p.created_at DESC,p.id DESC LIMIT $4"
        );
        result = page(
            sqlx::query(sqlx::AssertSqlSafe(sql.as_str()))
                .bind(viewer)
                .bind(time)
                .bind(id)
                .bind(limit + 1)
                .fetch_all(&mut *tx)
                .await?,
            limit,
            true,
        );
    } else if let Some(target) = target {
        if viewer == target {
            return Err(error(422, "cannot follow yourself"));
        };
        exists(&mut tx, "users", target).await?;
        let sql = if method == "PUT" {
            "INSERT INTO follows(follower_id,followed_id) VALUES ($1,$2) ON CONFLICT DO NOTHING"
        } else {
            "DELETE FROM follows WHERE follower_id=$1 AND followed_id=$2"
        };
        sqlx::query(sql)
            .bind(viewer)
            .bind(target)
            .execute(&mut *tx)
            .await?;
        result = Value::Null;
        status = StatusCode::NO_CONTENT;
    } else if path.ends_with("/like") {
        let id = pid.unwrap();
        exists(&mut tx, "posts", id).await?;
        let sql = if method == "PUT" {
            "INSERT INTO likes(post_id,user_id) VALUES ($1,$2) ON CONFLICT DO NOTHING"
        } else {
            "DELETE FROM likes WHERE post_id=$1 AND user_id=$2"
        };
        sqlx::query(sql)
            .bind(id)
            .bind(viewer)
            .execute(&mut *tx)
            .await?;
        result = Value::Null;
        status = StatusCode::NO_CONTENT;
    } else if path.ends_with("/comments") {
        let id = pid.unwrap();
        exists(&mut tx, "posts", id).await?;
        if method == "POST" {
            result=comment_value(&sqlx::query("WITH inserted AS (INSERT INTO comments(post_id,author_id,content) VALUES ($1,$2,$3) RETURNING *) SELECT c.id,c.content,c.created_at,c.author_id,u.username FROM inserted c JOIN users u ON u.id=c.author_id").bind(id).bind(viewer).bind(&input["content"]).fetch_one(&mut *tx).await?);
            status = StatusCode::CREATED;
        } else {
            let (limit, time, cid) = pagination.unwrap();
            result=page(sqlx::query("SELECT c.id,c.content,c.created_at,c.author_id,u.username FROM comments c JOIN users u ON u.id=c.author_id WHERE c.post_id=$1 AND ($2::timestamptz IS NULL OR (c.created_at,c.id)<($2::timestamptz,$3)) ORDER BY c.created_at DESC,c.id DESC LIMIT $4").bind(id).bind(time).bind(cid).bind(limit+1).fetch_all(&mut *tx).await?,limit,false);
        }
    } else {
        // Both SQL fragments are internal constants; request values are bound.
        let sql = format!("{POST_SELECT} WHERE p.id=$2");
        let row = sqlx::query(sqlx::AssertSqlSafe(sql.as_str()))
            .bind(viewer)
            .bind(pid.unwrap())
            .fetch_optional(&mut *tx)
            .await?
            .ok_or_else(|| error(404, "not found"))?;
        result = post_value(&row);
    }
    tx.commit().await?;
    Ok(if status == StatusCode::NO_CONTENT {
        status.into_response()
    } else {
        (status, Json(result)).into_response()
    })
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn cursor_precision() {
        let t = DateTime::parse_from_rfc3339("2026-10-05T01:02:03.456789+00:00")
            .unwrap()
            .to_utc();
        let c = encode_cursor(t, i64::MAX - 1);
        assert_eq!(decode_cursor(&c).unwrap(), (t, i64::MAX - 1));
        assert!(decode_cursor("").is_err());
        assert!(
            decode_cursor(
                &base64::engine::general_purpose::URL_SAFE_NO_PAD
                    .encode(br#"["2026-10-05T00:00:00Z",true]"#)
            )
            .is_err()
        );
    }
    #[test]
    fn password_policy() {
        let h = hash_password("correct-long-password").unwrap();
        assert!(h.starts_with("$argon2id$v=19$m=65536,t=3,p=1$"));
        assert!(verify_password("correct-long-password", &h));
        assert!(!verify_password("wrong", &h));
    }
    #[test]
    fn input_validation() {
        for v in [
            json!({"content":" "}),
            json!({"content":12}),
            json!({"content":"ok","extra":true}),
            json!({}),
        ] {
            assert!(validate_input(v, "post").is_err());
        }
    }
    #[test]
    fn jwt_validation() {
        let s = "s".repeat(32);
        let token = issue_token(42, &s, 3600).unwrap();
        assert_eq!(decode_token(&token, &s).unwrap(), 42);
        assert!(decode_token(&token, &"x".repeat(32)).is_err());
    }
    #[test]
    fn invalid_jwt_claims() {
        use jsonwebtoken::{EncodingKey, Header, encode};
        let secret = "s".repeat(32);
        let now = Utc::now().timestamp();
        for change in [
            json!({"exp":now-1}),
            json!({"exp":true}),
            json!({"iat":now+3600}),
            json!({"iat":"1"}),
            json!({"sub":42}),
            json!({"sub":"0"}),
            json!({"iss":"bad"}),
            json!({"aud":"bad"}),
            json!({"iat":null}),
        ] {
            let mut claims = json!({"sub":"42","iat":now,"exp":now+3600,"iss":"language-benchmark","aud":"social-api"});
            for (key, value) in change.as_object().unwrap() {
                if value.is_null() {
                    claims.as_object_mut().unwrap().remove(key);
                } else {
                    claims[key] = value.clone();
                }
            }
            let token = encode(
                &Header::default(),
                &claims,
                &EncodingKey::from_secret(secret.as_bytes()),
            )
            .unwrap();
            assert!(decode_token(&token, &secret).is_err(), "accepted {change}");
        }
    }
    #[tokio::test]
    async fn chunked_body_limit() {
        use tower::ServiceExt;
        let state = App {
            pool: PgPoolOptions::new()
                .connect_lazy("postgresql://postgres:test@localhost:9/language_benchmark_rust_test")
                .unwrap(),
            secret: Arc::new("s".repeat(32)),
            ttl: 3600,
            hashes: Arc::new(Semaphore::new(2)),
            dummy: Arc::new(String::new()),
            acquires: Arc::new(AtomicU64::new(0)),
            acquire_ns: Arc::new(AtomicU64::new(0)),
        };
        let request = Request::builder()
            .method("PUT")
            .uri("/api/v1/posts/1/like")
            .body(Body::from("x".repeat(16385)))
            .unwrap();
        let response = routes(state).oneshot(request).await.unwrap();
        assert_eq!(response.status(), StatusCode::PAYLOAD_TOO_LARGE);
        let body = to_bytes(response.into_body(), 65536).await.unwrap();
        assert_eq!(
            serde_json::from_slice::<Value>(&body).unwrap(),
            json!({"detail":"request body too large"})
        );
    }
    #[test]
    fn validation_aggregates_fields() {
        let err = validate_input(json!({"username":12,"password":true,"extra":1}), "register")
            .err()
            .unwrap();
        assert_eq!(err.1.as_array().unwrap().len(), 3);
    }
    #[test]
    fn root_validation_parity() {
        for (value, typ, msg) in [
            (
                json!([]),
                "model_attributes_type",
                "Input should be a valid dictionary or object to extract fields from",
            ),
            (Value::Null, "missing", "Field required"),
            (
                json!({"username":"alice","password":""}),
                "string_too_short",
                "String should have at least 1 character",
            ),
        ] {
            let err = validate_input(value.clone(), "login").err().unwrap();
            let d = &err.1[0];
            assert_eq!(d["type"], typ);
            assert_eq!(d["msg"], msg);
            if value.is_null() || value.is_array() {
                assert_eq!(d["loc"], json!(["body"]));
            }
        }
    }
    #[test]
    fn not_before_and_expiry_boundary() {
        let secret = "s".repeat(32);
        let now = Utc::now().timestamp();
        for extra in [json!({"nbf":now+3600}), json!({"exp":now})] {
            let mut claims = json!({"sub":"1","iat":now,"exp":now+3600,"iss":"language-benchmark","aud":"social-api"});
            for (k, v) in extra.as_object().unwrap() {
                claims[k] = v.clone()
            }
            let t = jsonwebtoken::encode(
                &jsonwebtoken::Header::default(),
                &claims,
                &jsonwebtoken::EncodingKey::from_secret(secret.as_bytes()),
            )
            .unwrap();
            assert!(decode_token(&t, &secret).is_err());
        }
    }
    #[test]
    fn extra_keys_preserve_order() {
        let value:Value=serde_json::from_str(r#"{"username":"alice","password":"long-enough-password","z_extra":true,"a_extra":false}"#).unwrap();
        let e = validate_input(value, "register").err().unwrap();
        assert_eq!(e.1[0]["loc"], json!(["body", "z_extra"]));
        assert_eq!(e.1[1]["loc"], json!(["body", "a_extra"]));
    }
    #[test]
    fn aws_database_settings() {
        for ds in [
            "postgresql://u:p@localhost/test?sslmode=disable",
            "postgresql://u:p@localhost/test?sslmode=disable&note=sslmode=verify-full&sslrootcert=x",
            "postgresql://u:p@localhost/test?sslmode=verify-full&sslmode=disable&sslrootcert=x",
            "postgresql://u:p@localhost/test?sslmode=verify-full&sslrootcert=",
        ] {
            assert!(database_options(ds, "aws").is_err());
        }
        let o =
            database_options("postgresql://u:p@localhost/test?sslmode=disable", "local").unwrap();
        assert_eq!(o.get_application_name(), Some("lb-backend"));
        assert!(o.get_options().unwrap().contains("statement_timeout=10000"));
        for ttl in [1, 599, 86401] {
            assert!(!valid_ttl(ttl));
        }
    }
    #[tokio::test]
    async fn postgres_session_guards() {
        let Ok(dsn) = std::env::var("RUST_TEST_DATABASE_URL") else {
            return;
        };
        let u = url::Url::parse(&dsn).unwrap();
        assert!(u.path().ends_with("_test"));
        let pool = PgPoolOptions::new()
            .max_connections(1)
            .connect_with(database_options(&dsn, "local").unwrap())
            .await
            .unwrap();
        let row=sqlx::query("SELECT current_setting('application_name') AS name,current_setting('statement_timeout') AS timeout").fetch_one(&pool).await.unwrap();
        assert_eq!(row.get::<String, _>("name"), "lb-backend");
        assert_eq!(row.get::<String, _>("timeout"), "10s");
        pool.close().await;
    }
    #[test]
    fn audience_array_and_year_zero() {
        let secret = "s".repeat(32);
        let now = Utc::now().timestamp();
        let claims = json!({"sub":"1","iat":now,"exp":now+3600,"iss":"language-benchmark","aud":["other","social-api"]});
        let t = jsonwebtoken::encode(
            &jsonwebtoken::Header::default(),
            &claims,
            &jsonwebtoken::EncodingKey::from_secret(secret.as_bytes()),
        )
        .unwrap();
        assert_eq!(decode_token(&t, &secret).unwrap(), 1);
        let cursor = base64::engine::general_purpose::URL_SAFE_NO_PAD
            .encode(br#"["0000-01-01T00:00:00Z",1]"#);
        assert!(decode_cursor(&cursor).is_err());
    }
}

#[cfg(test)]
#[path = "tuning_test.rs"]
mod tuning_tests;
