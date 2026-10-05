package benchmark;

import java.nio.charset.StandardCharsets;
import java.security.*;
import java.time.*;
import java.util.*;
import java.util.concurrent.Semaphore;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import org.bouncycastle.crypto.generators.Argon2BytesGenerator;
import org.bouncycastle.crypto.params.Argon2Parameters;
import tools.jackson.databind.ObjectMapper;

public final class Protocol {
  static final ObjectMapper JSON = new ObjectMapper();
  static final Base64.Encoder B64 = Base64.getUrlEncoder().withoutPadding();
  static final Semaphore HASH = new Semaphore(2, true);
  final String secret;
  final int ttl;

  public Protocol(String secret, int ttl) {
    if (secret.getBytes(StandardCharsets.UTF_8).length < 32 || ttl < 600 || ttl > 86400)
      throw new IllegalArgumentException("invalid JWT configuration");
    this.secret = secret;
    this.ttl = ttl;
  }

  static byte[] signature(String input, String secret) {
    try {
      var mac = Mac.getInstance("HmacSHA256");
      mac.init(new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
      return mac.doFinal(input.getBytes(StandardCharsets.US_ASCII));
    } catch (Exception e) {
      throw new IllegalStateException(e);
    }
  }

  public String issue(long id, long now) {
    var h =
        B64.encodeToString("{\"alg\":\"HS256\",\"typ\":\"JWT\"}".getBytes(StandardCharsets.UTF_8));
    var b =
        B64.encodeToString(
            JSON.writeValueAsBytes(
                Map.of(
                    "sub",
                    Long.toString(id),
                    "iat",
                    now,
                    "exp",
                    now + ttl,
                    "iss",
                    "language-benchmark",
                    "aud",
                    "social-api")));
    return h + "." + b + "." + B64.encodeToString(signature(h + "." + b, secret));
  }

  public long identity(String token, long now) {
    try {
      var parts = token.split("\\.", -1);
      if (parts.length != 3) throw new IllegalArgumentException();
      var head = JSON.readTree(Base64.getUrlDecoder().decode(parts[0]));
      var data = JSON.readTree(Base64.getUrlDecoder().decode(parts[1]));
      if (!"HS256".equals(head.path("alg").asString())
          || !MessageDigest.isEqual(
              signature(parts[0] + "." + parts[1], secret), Base64.getUrlDecoder().decode(parts[2]))
          || !"language-benchmark".equals(data.path("iss").asString())
          || !audience(data.path("aud"))
          || !data.path("iat").isIntegralNumber()
          || !data.path("iat").canConvertToLong()
          || !data.path("exp").isIntegralNumber()
          || !data.path("exp").canConvertToLong()
          || data.path("exp").asLong() <= now
          || data.path("iat").asLong() > now + 30
          || !data.path("sub").isString()) throw new IllegalArgumentException();
      if (data.has("nbf")) {
        var nbf = data.get("nbf");
        long start;
        if (nbf.isIntegralNumber() && nbf.canConvertToLong()) start = nbf.asLong();
        else if (nbf.isString()) start = Long.parseLong(nbf.asString());
        else throw new IllegalArgumentException();
        if (start > now) throw new IllegalArgumentException();
      }
      var id = Long.parseLong(data.path("sub").asString());
      if (id <= 0) throw new IllegalArgumentException();
      return id;
    } catch (Exception e) {
      throw new IllegalArgumentException("invalid token", e);
    }
  }

  private static boolean audience(tools.jackson.databind.JsonNode value) {
    if (value.isString()) return "social-api".equals(value.asString());
    if (!value.isArray()) return false;
    boolean found = false;
    for (var entry : value) {
      if (!entry.isString()) return false;
      if ("social-api".equals(entry.asString())) found = true;
    }
    return found;
  }

  public record Cursor(String stamp, long id) {}

  public static String cursor(String stamp, long id) {
    return B64.encodeToString(JSON.writeValueAsBytes(List.of(stamp, id)));
  }

  public static Cursor decodeCursor(String cursor) {
    if (cursor == null) return new Cursor(null, 0);
    try {
      if (cursor.isEmpty() || cursor.length() > 256) throw new IllegalArgumentException();
      var d = JSON.readTree(Base64.getUrlDecoder().decode(cursor));
      if (!d.isArray()
          || d.size() != 2
          || !d.get(0).isString()
          || !d.get(1).isIntegralNumber()
          || !d.get(1).canConvertToLong()
          || d.get(1).asLong() <= 0) throw new IllegalArgumentException();
      var stamp = OffsetDateTime.parse(d.get(0).asString());
      var utc = stamp.withOffsetSameInstant(ZoneOffset.UTC);
      if (stamp.getYear() < 1
          || stamp.getYear() > 9999
          || utc.getYear() < 1
          || utc.getYear() > 9999) throw new IllegalArgumentException();
      return new Cursor(utc.toInstant().toString(), d.get(1).asLong());
    } catch (Exception e) {
      throw new IllegalArgumentException("invalid cursor", e);
    }
  }

  static byte[] argon(String password, byte[] salt) {
    try {
      HASH.acquire();
      try {
        var generator = new Argon2BytesGenerator();
        generator.init(
            new Argon2Parameters.Builder(Argon2Parameters.ARGON2_id)
                .withVersion(19)
                .withIterations(3)
                .withMemoryAsKB(65536)
                .withParallelism(1)
                .withSalt(salt)
                .build());
        byte[] output = new byte[32];
        generator.generateBytes(password.toCharArray(), output);
        return output;
      } finally {
        HASH.release();
      }
    } catch (InterruptedException e) {
      Thread.currentThread().interrupt();
      throw new IllegalStateException(e);
    }
  }

  public static String hash(String password) {
    byte[] salt = new byte[16];
    new SecureRandom().nextBytes(salt);
    var b = Base64.getEncoder().withoutPadding();
    return "$argon2id$v=19$m=65536,t=3,p=1$"
        + b.encodeToString(salt)
        + "$"
        + b.encodeToString(argon(password, salt));
  }

  public static boolean verify(String password, String hash) {
    try {
      var parts = hash.split("\\$", -1);
      if (parts.length != 6
          || !parts[1].equals("argon2id")
          || !parts[2].equals("v=19")
          || !parts[3].equals("m=65536,t=3,p=1")) return false;
      var decoder = Base64.getDecoder();
      return MessageDigest.isEqual(
          decoder.decode(parts[5]), argon(password, decoder.decode(parts[4])));
    } catch (IllegalArgumentException e) {
      return false;
    }
  }
}
