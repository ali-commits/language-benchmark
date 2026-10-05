package benchmark;

import static org.junit.jupiter.api.Assertions.*;

import org.junit.jupiter.api.Test;
import org.springframework.mock.web.*;

class BodyLimitTest {
  @Test
  void malformedUtf8NeverReachesApplication() throws Exception {
    var request = new MockHttpServletRequest("POST", "/api/v1/posts");
    request.setContentType("application/json");
    request.setContent(new byte[] {'{', '\"', 'c', '\"', ':', '\"', (byte) 255, '\"', '}'});
    var response = new MockHttpServletResponse();
    var called = new java.util.concurrent.atomic.AtomicBoolean();
    new BodyLimit().doFilter(request, response, (a, b) -> called.set(true));
    assertEquals(400, response.getStatus());
    assertFalse(called.get());
  }
}
