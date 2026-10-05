package benchmark;

import static org.junit.jupiter.api.Assertions.*;

import org.junit.jupiter.api.Test;

class DatabaseConfigTest {
  @Test
  void passwordPreservesLiteralPlusAndDecodesEscapes() {
    var c =
        DatabaseConfig.parse(
            "postgresql://u%2Bser:literal+and%2Bencoded@localhost/example", "local");
    assertEquals("u+ser", c.username());
    assertEquals("literal+and+encoded", c.password());
  }

  @Test
  void awsRequiresExactUnambiguousTLSParameters() {
    for (var query :
        new String[] {
          "sslmode=disable&note=sslmode=verify-full&sslrootcert=/ca",
          "sslmode=verify-full&sslmode=disable&sslrootcert=/ca",
          "sslmode=verify-full&sslrootcert=",
          "sslmode=verify-full&sslrootcert=/ca&sslrootcert="
        })
      assertThrows(
          IllegalArgumentException.class,
          () -> DatabaseConfig.parse("postgresql://u:p@localhost/db?" + query, "aws"));
    assertDoesNotThrow(
        () ->
            DatabaseConfig.parse(
                "postgresql://u:p@localhost/db?sslmode=verify-full&sslrootcert=/ca", "aws"));
  }

  @Test
  void requiredSessionSettingsCannotBeOverridden() {
    var c =
        DatabaseConfig.parse(
            "postgresql://u:p@localhost/db?options=bad&ApplicationName=wrong", "local");
    assertTrue(c.jdbcUrl().contains("ApplicationName=lb-backend"));
    assertTrue(c.jdbcUrl().contains("statement_timeout%3D10000"));
    assertFalse(c.jdbcUrl().contains("bad"));
    assertFalse(c.jdbcUrl().contains("wrong"));
  }
}
