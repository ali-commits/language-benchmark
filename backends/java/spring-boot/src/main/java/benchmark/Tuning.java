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
  static int[] poolBounds(String max, String min) {
    int maximum = poolSize(max,20,1), minimum = poolSize(min,2,0);
    if (minimum > maximum) throw new IllegalArgumentException("DB pool minimum exceeds maximum");
    return new int[]{maximum,minimum};
  }
  private static int poolSize(String value,int fallback,int minimum) {
    if(value==null) return fallback;
    if(!value.matches("0|[1-9][0-9]{0,2}")) throw new IllegalArgumentException("invalid DB pool size");
    int n=Integer.parseInt(value);
    if(n<minimum || n>256) throw new IllegalArgumentException("invalid DB pool size");
    return n;
  }
  static Semaphore hashSlots(String value) { return new Semaphore(parse(value), true); }
  static void writeReceipt(String directory,String runId,int hashes,int poolSize) throws Exception {writeReceipt(directory,runId,hashes,poolSize,2);}
  static void writeReceipt(String directory, String runId, int hashes, int poolSize,int poolMin) throws Exception {
    if (directory == null || directory.isEmpty()) return;
    long pid = ProcessHandle.current().pid();
    var payload = new java.util.LinkedHashMap<String,Object>(Map.of("schema_version", 1, "run_id", runId == null ? "" : runId, "pid", pid,
        "language", "java", "workers_total", 1, "connections_per_worker", poolSize, "pool_budget", poolSize,
        "cpu_parallelism", Runtime.getRuntime().availableProcessors(), "hash_concurrency", hashes,
        "hash_scope", "process"));
    if("production4".equals(System.getenv("BENCH_PROFILE"))) {
      payload.put("schema_version",2);payload.put("pool_min_size",poolMin);payload.put("hash_executor_mode",Protocol.HASH_EXECUTOR==null?"direct":"platform");
    }
    var dir = Path.of(directory);
    var temporary = Files.createTempFile(dir, ".tuning-", ".tmp");
    try {
      Files.writeString(temporary, Protocol.JSON.writeValueAsString(payload));
      Files.setPosixFilePermissions(temporary, java.nio.file.attribute.PosixFilePermissions.fromString("rw-r--r--"));
      Files.move(temporary, dir.resolve("tuning-" + pid + ".json"), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
    } finally { Files.deleteIfExists(temporary); }
  }
}
