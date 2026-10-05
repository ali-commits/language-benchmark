package benchmark;

import static org.junit.jupiter.api.Assertions.*;

import org.junit.jupiter.api.Test;

class ValidationTest {
  final SocialController api = new SocialController(null, null, new Protocol("s".repeat(32), 3600));

  @Test
  void namesNormalizeButContentDoesNotTrim() {
    assertEquals(
        "test_user",
        api.body("{\"username\":\"Test_User\",\"password\":\"twelve-chars-password\"}", "register")
            .get("username"));
    assertEquals(" hello ", api.body("{\"content\":\" hello \"}", "post").get("content"));
  }

  @Test
  void rejectsTypesAndExtrasAndBlankContent() {
    for (var input :
        new String[] {
          "{\"content\":true}",
          "{\"content\":\"   \"}",
          "{\"content\":\"hello\",\"extra\":1}",
          "[]",
          "null"
        }) assertThrows(SocialController.Failure.class, () -> api.body(input, "post"));
  }

  @Test
  void invalidCursorAndLimitGive422() {
    assertEquals(
        422,
        assertThrows(SocialController.Failure.class, () -> api.pagination("bad", "20")).status);
    assertEquals(
        422,
        assertThrows(SocialController.Failure.class, () -> api.pagination(null, "101")).status);
  }
}
