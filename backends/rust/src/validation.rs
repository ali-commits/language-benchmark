use axum::{
    Json,
    http::StatusCode,
    response::{IntoResponse, Response},
};
use serde_json::{Value, json};
use std::collections::HashMap;
pub struct ApiError(pub StatusCode, pub Value);
impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        let mut r = (self.0, Json(json!({"detail":self.1}))).into_response();
        if self.0 == StatusCode::UNAUTHORIZED {
            r.headers_mut()
                .insert("www-authenticate", "Bearer".parse().unwrap());
        }
        r
    }
}
impl From<sqlx::Error> for ApiError {
    fn from(e: sqlx::Error) -> Self {
        eprintln!("database operation failed: {e}");
        Self(
            StatusCode::INTERNAL_SERVER_ERROR,
            json!("internal server error"),
        )
    }
}
pub fn error(status: u16, message: &str) -> ApiError {
    ApiError(StatusCode::from_u16(status).unwrap(), json!(message))
}
pub fn invalid(
    location: &str,
    field: &str,
    kind: &str,
    msg: &str,
    input: Value,
    ctx: Option<Value>,
) -> ApiError {
    let loc = if field.is_empty() {
        json!([location])
    } else {
        json!([location, field])
    };
    let mut detail = json!({"loc":loc,"type":kind,"msg":msg,"input":input});
    if let Some(c) = ctx {
        detail["ctx"] = c;
    }
    ApiError(StatusCode::UNPROCESSABLE_ENTITY, json!([detail]))
}
pub fn validate_input(value: Value, kind: &str) -> Result<HashMap<String, String>, ApiError> {
    if value.is_null() {
        return Err(invalid(
            "body",
            "",
            "missing",
            "Field required",
            Value::Null,
            None,
        ));
    };
    let object = value.as_object().ok_or_else(|| {
        invalid(
            "body",
            "",
            "model_attributes_type",
            "Input should be a valid dictionary or object to extract fields from",
            value.clone(),
            None,
        )
    })?;
    let fields = if kind == "login" || kind == "register" {
        vec!["username", "password"]
    } else {
        vec!["content"]
    };
    let mut result = HashMap::new();
    let mut errors = Vec::new();
    for f in &fields {
        let outcome: Result<String, ApiError> = (|| {
            let v = object.get(*f).ok_or_else(|| {
                invalid("body", f, "missing", "Field required", value.clone(), None)
            })?;
            let s = v.as_str().ok_or_else(|| {
                invalid(
                    "body",
                    f,
                    "string_type",
                    "Input should be a valid string",
                    v.clone(),
                    None,
                )
            })?;
            let (min, max) = match *f {
                "username" => (3, 32),
                "password" => (if kind == "register" { 12 } else { 1 }, 128),
                _ => (1, if kind == "comment" { 1000 } else { 280 }),
            };
            let count = s.chars().count();
            if count < min {
                return Err(invalid(
                    "body",
                    f,
                    "string_too_short",
                    &format!(
                        "String should have at least {min} {}",
                        if min == 1 { "character" } else { "characters" }
                    ),
                    v.clone(),
                    Some(json!({"min_length":min})),
                ));
            };
            if count > max {
                return Err(invalid(
                    "body",
                    f,
                    "string_too_long",
                    &format!(
                        "String should have at most {max} {}",
                        if max == 1 { "character" } else { "characters" }
                    ),
                    v.clone(),
                    Some(json!({"max_length":max})),
                ));
            };
            if *f == "username" && !s.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'_') {
                return Err(invalid(
                    "body",
                    f,
                    "string_pattern_mismatch",
                    "String should match pattern '^[A-Za-z0-9_]+$'",
                    v.clone(),
                    Some(json!({"pattern":"^[A-Za-z0-9_]+$"})),
                ));
            };
            if *f == "content"
                && s.trim_matches(|c: char| c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c))
                    .is_empty()
            {
                return Err(invalid(
                    "body",
                    f,
                    "value_error",
                    "Value error, content must not be blank",
                    v.clone(),
                    Some(json!({"error":{}})),
                ));
            };
            Ok(if *f == "username" {
                s.to_lowercase()
            } else {
                s.into()
            })
        })();
        match outcome {
            Ok(v) => {
                result.insert(f.to_string(), v);
            }
            Err(e) => {
                errors.extend(e.1.as_array().unwrap().iter().cloned());
            }
        }
    }
    for (f, v) in object {
        if !fields.contains(&f.as_str()) {
            let e = invalid(
                "body",
                f,
                "extra_forbidden",
                "Extra inputs are not permitted",
                v.clone(),
                None,
            );
            errors.extend(e.1.as_array().unwrap().iter().cloned());
        }
    }
    if errors.is_empty() {
        Ok(result)
    } else {
        Err(ApiError(StatusCode::UNPROCESSABLE_ENTITY, json!(errors)))
    }
}
