package benchmark;

import java.net.*;
import java.nio.charset.StandardCharsets;
import java.util.*;

/** Parse credentials independently from form-encoded URI query parameters. */
record DatabaseConfig(String jdbcUrl, String username, String password) {
  static DatabaseConfig parse(String value, String mode) {
    try {
      var uri = new URI(value.replace("postgresql+psycopg://", "postgresql://"));
      if (!"postgresql".equals(uri.getScheme())
          || uri.getHost() == null
          || uri.getRawPath() == null
          || uri.getRawPath().length() < 2
          || uri.getRawUserInfo() == null)
        throw new IllegalArgumentException("PostgreSQL URL required");
      var info = uri.getRawUserInfo().split(":", 2);
      var query = new LinkedHashMap<String, List<String>>();
      var preserved = new ArrayList<String>();
      if (uri.getRawQuery() != null)
        for (var part : uri.getRawQuery().split("&")) {
          if (part.isEmpty()) continue;
          var kv = part.split("=", 2);
          var key = URLDecoder.decode(kv[0], StandardCharsets.UTF_8);
          var val = URLDecoder.decode(kv.length > 1 ? kv[1] : "", StandardCharsets.UTF_8);
          query.computeIfAbsent(key, ignored -> new ArrayList<>()).add(val);
          if (!Set.of("application_name", "applicationname", "options", "statement_timeout")
              .contains(key.toLowerCase(Locale.ROOT))) preserved.add(part);
        }
      for (var key : List.of("sslmode", "sslrootcert"))
        if (query.containsKey(key) && query.get(key).size() != 1)
          throw new IllegalArgumentException("ambiguous TLS configuration");
      if ("aws".equals(mode)
          && (!query.getOrDefault("sslmode", List.of()).equals(List.of("verify-full"))
              || query.getOrDefault("sslrootcert", List.of()).size() != 1
              || query.get("sslrootcert").getFirst().isBlank()))
        throw new IllegalArgumentException("AWS requires verify-full and sslrootcert");
      preserved.add("ApplicationName=lb-backend");
      preserved.add("options=-c%20statement_timeout%3D10000");
      return new DatabaseConfig(
          "jdbc:postgresql://"
              + uri.getHost()
              + ":"
              + (uri.getPort() < 0 ? 5432 : uri.getPort())
              + uri.getRawPath()
              + "?"
              + String.join("&", preserved),
          percentDecode(info[0]),
          info.length > 1 ? percentDecode(info[1]) : "");
    } catch (java.net.URISyntaxException e) {
      throw new IllegalArgumentException("invalid PostgreSQL URL", e);
    }
  }

  private static String percentDecode(String value) {
    return URLDecoder.decode(value.replace("+", "%2B"), StandardCharsets.UTF_8);
  }
}
