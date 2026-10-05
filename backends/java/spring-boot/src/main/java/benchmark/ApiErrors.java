package benchmark;

import java.util.*;
import org.springframework.http.*;
import org.springframework.web.HttpRequestMethodNotSupportedException;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.servlet.resource.NoResourceFoundException;

@RestControllerAdvice
public class ApiErrors {
  @ExceptionHandler(NoResourceFoundException.class)
  ResponseEntity<?> notFound() {
    return ResponseEntity.status(404).body(Map.of("detail", "Not Found"));
  }

  @ExceptionHandler(HttpRequestMethodNotSupportedException.class)
  ResponseEntity<?> method(HttpRequestMethodNotSupportedException e) {
    var builder = ResponseEntity.status(405);
    var allowed = e.getSupportedMethods();
    if (allowed != null) builder.header("Allow", String.join(", ", allowed));
    return builder.body(Map.of("detail", "Method Not Allowed"));
  }
}
