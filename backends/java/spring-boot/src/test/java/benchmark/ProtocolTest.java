package benchmark;

import static org.junit.jupiter.api.Assertions.*;

import org.junit.jupiter.api.Test;

class ProtocolTest {
  @Test
  void jwtExpiryAndBigint() {
    var p = new Protocol("s".repeat(32), 3600);
    var t = p.issue(Long.MAX_VALUE - 1, 1000);
    assertEquals(Long.MAX_VALUE - 1, p.identity(t, 1001));
    assertThrows(IllegalArgumentException.class, () -> p.identity(t, 5000));
  }

  @Test
  void cursorMicroseconds() {
    var c = Protocol.cursor("2026-10-05T01:02:03.123456+00:00", Long.MAX_VALUE - 1);
    assertEquals("2026-10-05T01:02:03.123456Z", Protocol.decodeCursor(c).stamp());
    assertEquals(Long.MAX_VALUE - 1, Protocol.decodeCursor(c).id());
  }

  @Test
  void argonRoundtrip() {
    var h = Protocol.hash("twelve-chars-password");
    assertTrue(h.startsWith("$argon2id$v=19$m=65536,t=3,p=1$"));
    assertTrue(Protocol.verify("twelve-chars-password", h));
    assertFalse(Protocol.verify("wrong", h));
  }

  @Test
  void optionalNotBeforeAndAudienceArray() {
    var p = new Protocol("s".repeat(32), 3600);
    var claims =
        new java.util.HashMap<String, Object>(
            java.util.Map.of(
                "sub",
                "1",
                "iat",
                1000,
                "exp",
                4600,
                "iss",
                "language-benchmark",
                "aud",
                java.util.List.of("other", "social-api"),
                "nbf",
                999));
    assertEquals(1, p.identity(signed(claims), 1001));
    claims.put("nbf", 1500);
    assertThrows(IllegalArgumentException.class, () -> p.identity(signed(claims), 1001));
  }

  String signed(java.util.Map<String, Object> claims) {
    var h =
        Protocol.B64.encodeToString(
            "{\"alg\":\"HS256\"}".getBytes(java.nio.charset.StandardCharsets.UTF_8));
    var b = Protocol.B64.encodeToString(Protocol.JSON.writeValueAsBytes(claims));
    return h
        + "."
        + b
        + "."
        + Protocol.B64.encodeToString(Protocol.signature(h + "." + b, "s".repeat(32)));
  }
}
