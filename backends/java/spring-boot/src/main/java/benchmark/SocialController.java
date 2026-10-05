package benchmark;

import jakarta.servlet.http.HttpServletRequest;
import java.sql.Timestamp;
import java.time.*;
import java.util.*;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.http.*;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.bind.annotation.*;
import tools.jackson.databind.JsonNode;

@RestController
public class SocialController {
  private final NamedParameterJdbcTemplate db;
  private final TransactionTemplate tx;
  private final Protocol security;
  private static final String DUMMY =
      "$argon2id$v=19$m=65536,t=3,p=1$MDEyMzQ1Njc4OWFiY2RlZg$6LZzQxzV/D4kfEE2uvT2MhHRKaCWGjBE36GO9JsDk3k";
  private static final String POST =
      "SELECT p.id,p.content,p.created_at,p.author_id,u.username,(SELECT count(*) FROM likes l"
          + " WHERE l.post_id=p.id) AS like_count,(SELECT count(*) FROM comments c WHERE"
          + " c.post_id=p.id) AS comment_count,EXISTS(SELECT 1 FROM likes l WHERE l.post_id=p.id"
          + " AND l.user_id=:viewer) AS liked FROM posts p JOIN users u ON u.id=p.author_id";

  public SocialController(
      NamedParameterJdbcTemplate db, TransactionTemplate tx, Protocol security) {
    this.db = db;
    this.tx = tx;
    this.security = security;
  }

  static class Failure extends RuntimeException {
    final int status;
    final Object detail;
    final boolean bearer;

    Failure(int s, Object d, boolean b) {
      status = s;
      detail = d;
      bearer = b;
    }
  }

  static Failure error(int status, String message) {
    return new Failure(status, message, false);
  }

  @ExceptionHandler(Failure.class)
  ResponseEntity<?> failure(Failure e) {
    var builder = ResponseEntity.status(e.status);
    if (e.bearer) builder.header("WWW-Authenticate", "Bearer");
    return builder.body(Map.of("detail", e.detail));
  }

  long identity(HttpServletRequest req) {
    try {
      var auth = req.getHeader("Authorization");
      if (auth == null || !auth.regionMatches(true, 0, "Bearer ", 0, 7))
        throw new IllegalArgumentException();
      return security.identity(auth.substring(7).strip(), Instant.now().getEpochSecond());
    } catch (IllegalArgumentException e) {
      throw new Failure(401, "invalid credentials", true);
    }
  }

  Map<String, Object> params(Object... kv) {
    var m = new HashMap<String, Object>();
    for (int i = 0; i < kv.length; i += 2) m.put((String) kv[i], kv[i + 1]);
    return m;
  }

  List<Map<String, Object>> query(String sql, Map<String, Object> values) {
    return db.queryForList(sql, values);
  }

  Map<String, Object> first(String sql, Map<String, Object> values, int missing) {
    var rows = query(sql, values);
    if (rows.isEmpty()) throw error(missing, missing == 401 ? "invalid credentials" : "not found");
    return rows.getFirst();
  }

  void exists(String table, long id) {
    first("SELECT 1 FROM " + table + " WHERE id=:id", params("id", id), 404);
  }

  long id(String value, String field) {
    try {
      return Long.parseLong(value);
    } catch (NumberFormatException e) {
      throw new Failure(
          422,
          List.of(
              Map.of(
                  "type",
                  "int_parsing",
                  "loc",
                  List.of("path", field),
                  "msg",
                  "Input should be a valid integer, unable to parse string as an integer",
                  "input",
                  value)),
          false);
    }
  }

  Map<String, Object> item(Map<String, Object> row) {
    var r = new LinkedHashMap<>(row);
    r.put("author", Map.of("id", r.remove("author_id"), "username", r.remove("username")));
    var stamp = r.get("created_at");
    r.put(
        "created_at",
        stamp instanceof Timestamp t
            ? t.toInstant().toString()
            : ((OffsetDateTime) stamp).toInstant().toString());
    return r;
  }

  Map<String, Object> page(List<Map<String, Object>> rows, int limit) {
    var selected = rows.stream().limit(limit).map(this::item).toList();
    var output = new LinkedHashMap<String, Object>();
    output.put("items", selected);
    output.put(
        "next_cursor",
        rows.size() > limit
            ? Protocol.cursor(
                (String) selected.getLast().get("created_at"),
                ((Number) selected.getLast().get("id")).longValue())
            : null);
    return output;
  }

  static String lastQuery(HttpServletRequest req, String key, String fallback) {
    var values = req.getParameterValues(key);
    return values == null ? fallback : values[values.length - 1];
  }

  Map<String, Object> pagination(String cursor, String raw) {
    int limit;
    try {
      limit = Integer.parseInt(raw);
    } catch (NumberFormatException e) {
      throw new Failure(
          422,
          List.of(
              Map.of(
                  "type",
                  "int_parsing",
                  "loc",
                  List.of("query", "limit"),
                  "msg",
                  "Input should be a valid integer, unable to parse string as an integer",
                  "input",
                  raw)),
          false);
    }
    if (limit < 1 || limit > 100)
      throw new Failure(
          422,
          List.of(
              Map.of(
                  "type",
                  limit < 1 ? "greater_than_equal" : "less_than_equal",
                  "loc",
                  List.of("query", "limit"),
                  "msg",
                  limit < 1
                      ? "Input should be greater than or equal to 1"
                      : "Input should be less than or equal to 100",
                  "input",
                  raw,
                  "ctx",
                  Map.of(limit < 1 ? "ge" : "le", limit < 1 ? 1 : 100))),
          false);
    try {
      var c = Protocol.decodeCursor(cursor);
      return params("cursor_time", c.stamp(), "cursor_id", c.id(), "limit", limit + 1);
    } catch (IllegalArgumentException e) {
      throw error(422, "invalid cursor");
    }
  }

  Map<String, String> body(String raw, String kind) {
    if (raw == null || raw.isEmpty())
      throw new Failure(
          422,
          List.of(
              params(
                  "type",
                  "missing",
                  "loc",
                  List.of("body"),
                  "msg",
                  "Field required",
                  "input",
                  null)),
          false);
    JsonNode input;
    try {
      input = Protocol.JSON.readTree(raw);
    } catch (Exception e) {
      throw new Failure(
          422,
          List.of(
              Map.of(
                  "type",
                  "json_invalid",
                  "loc",
                  List.of("body", 0),
                  "msg",
                  "JSON decode error",
                  "input",
                  Map.of(),
                  "ctx",
                  Map.of("error", "Expecting value"))),
          false);
    }
    if (input == null || input.isNull())
      throw new Failure(
          422,
          List.of(
              params(
                  "type",
                  "missing",
                  "loc",
                  List.of("body"),
                  "msg",
                  "Field required",
                  "input",
                  null)),
          false);
    if (input == null || !input.isObject())
      throw new Failure(
          422,
          List.of(
              Map.of(
                  "type",
                  "model_attributes_type",
                  "loc",
                  List.of("body"),
                  "msg",
                  "Input should be a valid dictionary or object to extract fields from",
                  "input",
                  input == null ? "" : input)),
          false);
    var result = new LinkedHashMap<String, String>();
    var errors = new ArrayList<Map<String, Object>>();
    var fields =
        Set.of("login", "register").contains(kind)
            ? List.of("username", "password")
            : List.of("content");
    for (var field : fields) {
      var v = input.get(field);
      var base = params("loc", List.of("body", field), "input", v == null ? input : v);
      String type = null, msg = null;
      Map<String, Object> ctx = null;
      int min =
          field.equals("username")
              ? 3
              : field.equals("password") && kind.equals("register") ? 12 : 1;
      int max =
          field.equals("username")
              ? 32
              : field.equals("password") ? 128 : kind.equals("comment") ? 1000 : 280;
      if (v == null) {
        type = "missing";
        msg = "Field required";
      } else if (!v.isString()) {
        type = "string_type";
        msg = "Input should be a valid string";
      } else {
        var text = v.asString();
        int len = text.codePointCount(0, text.length());
        if (len < min) {
          type = "string_too_short";
          msg = "String should have at least " + min + (min == 1 ? " character" : " characters");
          ctx = Map.of("min_length", min);
        } else if (len > max) {
          type = "string_too_long";
          msg = "String should have at most " + max + " characters";
          ctx = Map.of("max_length", max);
        } else if (field.equals("username") && !text.matches("[A-Za-z0-9_]+")) {
          type = "string_pattern_mismatch";
          msg = "String should match pattern '^[A-Za-z0-9_]+$'";
          ctx = Map.of("pattern", "^[A-Za-z0-9_]+$");
        } else if (field.equals("content")
            && text.codePoints()
                .allMatch(c -> Character.isWhitespace(c) || Character.isSpaceChar(c))) {
          type = "value_error";
          msg = "Value error, content must not be blank";
          ctx = Map.of("error", Map.of());
        } else result.put(field, field.equals("username") ? text.toLowerCase(Locale.ROOT) : text);
      }
      if (type != null) {
        base.put("type", type);
        base.put("msg", msg);
        if (ctx != null) base.put("ctx", ctx);
        errors.add(base);
      }
    }
    input
        .propertyNames()
        .forEach(
            field -> {
              if (!fields.contains(field))
                errors.add(
                    params(
                        "type",
                        "extra_forbidden",
                        "loc",
                        List.of("body", field),
                        "msg",
                        "Extra inputs are not permitted",
                        "input",
                        input.get(field)));
            });
    if (!errors.isEmpty()) throw new Failure(422, errors, false);
    return result;
  }

  @GetMapping("/health/live")
  Map<String, String> live() {
    return Map.of("status", "ok");
  }

  @GetMapping("/health/ready")
  Map<String, String> ready() {
    db.getJdbcTemplate().queryForObject("SELECT 1", Integer.class);
    return live();
  }

  @GetMapping("/health/metrics")
  Map<String, Object> metrics() {
    return params(
        "connection_acquires",
        Application.ACQUIRES.sum(),
        "connection_acquire_ms",
        Application.NANOS.sum() / 1e6);
  }

  @PostMapping("/api/v1/auth/register")
  ResponseEntity<?> register(@RequestBody(required = false) String raw) {
    var v = body(raw == null ? "" : raw, "register");
    var hash = Protocol.hash(v.get("password"));
    try {
      return ResponseEntity.status(201)
          .body(
              first(
                  "INSERT INTO users(username,password_hash) VALUES (:username,:hash) RETURNING"
                      + " id,username",
                  params("username", v.get("username"), "hash", hash),
                  404));
    } catch (DuplicateKeyException e) {
      throw error(409, "username already exists");
    }
  }

  @PostMapping("/api/v1/auth/login")
  Map<String, Object> login(@RequestBody(required = false) String raw) {
    var v = body(raw == null ? "" : raw, "login");
    var rows =
        query(
            "SELECT id,password_hash FROM users WHERE username=:name",
            params("name", v.get("username")));
    var row = rows.isEmpty() ? null : rows.getFirst();
    boolean valid =
        Protocol.verify(v.get("password"), row == null ? DUMMY : (String) row.get("password_hash"));
    if (!valid || row == null) throw new Failure(401, "invalid credentials", true);
    return params(
        "access_token",
        security.issue(((Number) row.get("id")).longValue(), Instant.now().getEpochSecond()),
        "token_type",
        "bearer",
        "expires_in",
        security.ttl);
  }

  @GetMapping("/api/v1/users/me")
  Map<String, Object> me(HttpServletRequest req) {
    return first("SELECT id,username FROM users WHERE id=:id", params("id", identity(req)), 401);
  }

  @PostMapping("/api/v1/posts")
  ResponseEntity<?> create(HttpServletRequest req, @RequestBody(required = false) String raw) {
    long viewer = identity(req);
    var v = body(raw == null ? "" : raw, "post");
    return ResponseEntity.status(201)
        .body(
            tx.execute(
                status -> {
                  var p = params("viewer", viewer, "content", v.get("content"));
                  p.put(
                      "id",
                      first(
                              "INSERT INTO posts(author_id,content) VALUES (:viewer,:content)"
                                  + " RETURNING id",
                              p,
                              404)
                          .get("id"));
                  return item(first(POST + " WHERE p.id=:id", p, 404));
                }));
  }

  @GetMapping("/api/v1/posts/{postId}")
  Map<String, Object> detail(HttpServletRequest req, @PathVariable String postId) {
    long viewer = identity(req);
    return item(
        first(
            POST + " WHERE p.id=:id", params("viewer", viewer, "id", id(postId, "post_id")), 404));
  }

  @GetMapping("/api/v1/feed")
  Map<String, Object> feed(HttpServletRequest req) {
    long viewer = identity(req);
    var p = pagination(lastQuery(req, "cursor", null), lastQuery(req, "limit", "20"));
    p.put("viewer", viewer);
    return page(
        query(
            POST
                + " WHERE p.author_id IN (SELECT followed_id FROM follows WHERE follower_id=:viewer"
                + " UNION ALL SELECT CAST(:viewer AS bigint)) AND (CAST(:cursor_time AS"
                + " timestamptz) IS NULL OR (p.created_at,p.id)<(CAST(:cursor_time AS"
                + " timestamptz),:cursor_id)) ORDER BY p.created_at DESC,p.id DESC LIMIT :limit",
            p),
        ((Integer) p.get("limit")) - 1);
  }

  @RequestMapping(
      value = "/api/v1/users/{targetId}/follow",
      method = {RequestMethod.PUT, RequestMethod.DELETE})
  ResponseEntity<?> follow(HttpServletRequest req, @PathVariable String targetId) {
    long viewer = identity(req), target = id(targetId, "target_id");
    if (viewer == target) throw error(422, "cannot follow yourself");
    tx.executeWithoutResult(
        status -> {
          exists("users", target);
          db.update(
              req.getMethod().equals("PUT")
                  ? "INSERT INTO follows(follower_id,followed_id) VALUES (:viewer,:target) ON"
                      + " CONFLICT DO NOTHING"
                  : "DELETE FROM follows WHERE follower_id=:viewer AND followed_id=:target",
              params("viewer", viewer, "target", target));
        });
    return ResponseEntity.noContent().build();
  }

  @RequestMapping(
      value = "/api/v1/posts/{postId}/like",
      method = {RequestMethod.PUT, RequestMethod.DELETE})
  ResponseEntity<?> like(HttpServletRequest req, @PathVariable String postId) {
    long viewer = identity(req), post = id(postId, "post_id");
    tx.executeWithoutResult(
        status -> {
          exists("posts", post);
          db.update(
              req.getMethod().equals("PUT")
                  ? "INSERT INTO likes(post_id,user_id) VALUES (:post,:viewer) ON CONFLICT DO"
                      + " NOTHING"
                  : "DELETE FROM likes WHERE post_id=:post AND user_id=:viewer",
              params("post", post, "viewer", viewer));
        });
    return ResponseEntity.noContent().build();
  }

  @PostMapping("/api/v1/posts/{postId}/comments")
  ResponseEntity<?> comment(
      HttpServletRequest req,
      @PathVariable String postId,
      @RequestBody(required = false) String raw) {
    long viewer = identity(req), post = id(postId, "post_id");
    var v = body(raw == null ? "" : raw, "comment");
    return ResponseEntity.status(201)
        .body(
            tx.execute(
                status -> {
                  exists("posts", post);
                  return item(
                      first(
                          "WITH inserted AS (INSERT INTO comments(post_id,author_id,content) VALUES"
                              + " (:post,:viewer,:content) RETURNING *) SELECT"
                              + " c.id,c.content,c.created_at,c.author_id,u.username FROM inserted"
                              + " c JOIN users u ON u.id=c.author_id",
                          params("post", post, "viewer", viewer, "content", v.get("content")),
                          404));
                }));
  }

  @GetMapping("/api/v1/posts/{postId}/comments")
  Map<String, Object> comments(HttpServletRequest req, @PathVariable String postId) {
    identity(req);
    long post = id(postId, "post_id");
    var p = pagination(lastQuery(req, "cursor", null), lastQuery(req, "limit", "20"));
    exists("posts", post);
    p.put("post", post);
    return page(
        query(
            "SELECT c.id,c.content,c.created_at,c.author_id,u.username FROM comments c JOIN users u"
                + " ON u.id=c.author_id WHERE c.post_id=:post AND (CAST(:cursor_time AS"
                + " timestamptz) IS NULL OR (c.created_at,c.id)<(CAST(:cursor_time AS"
                + " timestamptz),:cursor_id)) ORDER BY c.created_at DESC,c.id DESC LIMIT :limit",
            p),
        ((Integer) p.get("limit")) - 1);
  }
}
