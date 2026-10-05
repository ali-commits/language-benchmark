package benchmark;

import jakarta.servlet.*;
import jakarta.servlet.http.*;
import java.io.*;
import java.util.*;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

@Component
@Order(1)
public class BodyLimit extends OncePerRequestFilter {
  protected void doFilterInternal(
      HttpServletRequest req, HttpServletResponse res, FilterChain chain)
      throws ServletException, IOException {
    if (!Set.of("POST", "PUT", "PATCH").contains(req.getMethod())) {
      chain.doFilter(req, res);
      return;
    }
    byte[] data = req.getInputStream().readNBytes(16385);
    if (data.length > 16384) {
      res.setStatus(413);
      res.setContentType("application/json");
      res.getWriter().write("{\"detail\":\"request body too large\"}");
      return;
    }
    if (req.getMethod().equals("POST")) {
      try {
        java.nio.charset.StandardCharsets.UTF_8
            .newDecoder()
            .onMalformedInput(java.nio.charset.CodingErrorAction.REPORT)
            .onUnmappableCharacter(java.nio.charset.CodingErrorAction.REPORT)
            .decode(java.nio.ByteBuffer.wrap(data));
      } catch (java.nio.charset.CharacterCodingException e) {
        res.setStatus(400);
        res.setContentType("application/json");
        res.getWriter().write("{\"detail\":\"There was an error parsing the body\"}");
        return;
      }
    }
    chain.doFilter(
        new HttpServletRequestWrapper(req) {
          @Override
          public ServletInputStream getInputStream() {
            var in = new ByteArrayInputStream(data);
            return new ServletInputStream() {
              public int read() {
                return in.read();
              }

              public boolean isFinished() {
                return in.available() == 0;
              }

              public boolean isReady() {
                return true;
              }

              public void setReadListener(ReadListener listener) {
                throw new UnsupportedOperationException();
              }
            };
          }

          @Override
          public BufferedReader getReader() {
            return new BufferedReader(
                new InputStreamReader(getInputStream(), java.nio.charset.StandardCharsets.UTF_8));
          }
        },
        res);
  }
}
