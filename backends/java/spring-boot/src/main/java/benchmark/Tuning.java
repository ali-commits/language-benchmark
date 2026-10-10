package benchmark;

import java.nio.file.*;
import java.util.Map;
import java.util.concurrent.Semaphore;

final class Tuning {
  static int parse(String value) {
    if (value == null) return 2;
    if (value.length() != 1 || value.charAt(0) < '1' || value.charAt(0) > '4')
      throw new IllegalArgumentException("HASH_CONCURRENCY must be an integer between 1 and 4");
    return value.charAt(0) - '0';
  }
  static Semaphore hashSlots(String value) { return new Semaphore(parse(value), true); }
  static void writeReceipt(String directory, String runId, int hashes, int poolSize) throws Exception {
    if (directory == null || directory.isEmpty()) return;
    long pid = ProcessHandle.current().pid();
    var payload = Map.of("schema_version", 1, "run_id", runId == null ? "" : runId, "pid", pid,
        "language", "java", "workers_total", 1, "connections_per_worker", poolSize, "pool_budget", 20,
        "cpu_parallelism", Runtime.getRuntime().availableProcessors(), "hash_concurrency", hashes,
        "hash_scope", "process");
    var dir = Path.of(directory);
    var temporary = Files.createTempFile(dir, ".tuning-", ".tmp");
    try {
      Files.writeString(temporary, Protocol.JSON.writeValueAsString(payload));
      Files.setPosixFilePermissions(temporary, java.nio.file.attribute.PosixFilePermissions.fromString("rw-r--r--"));
      Files.move(temporary, dir.resolve("tuning-" + pid + ".json"), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
    } finally { Files.deleteIfExists(temporary); }
  }
}
