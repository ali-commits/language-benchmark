package benchmark;
import static org.junit.jupiter.api.Assertions.*;
import java.nio.file.*;
import java.util.concurrent.*;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
class TuningTest {
 @Test void settings() {
  assertEquals(2,Tuning.parse(null)); assertEquals(4,Tuning.parse("4"));
  for(var s:new String[]{"", "bad", "0", "5", "-1", "+4", " 4", "999999999999999999"}) assertThrows(IllegalArgumentException.class,()->Tuning.parse(s));
 }
 @Test void boundedAndInterruptedHashQueue() throws Exception {
  var hash=Tuning.hashSlots("4"); hash.acquire(4); assertFalse(hash.tryAcquire());
  var started=new CountDownLatch(1); var interrupted=new java.util.concurrent.atomic.AtomicBoolean();
  var thread=new Thread(()->{started.countDown();try {hash.acquire();fail("fifth admitted");} catch(InterruptedException e){interrupted.set(true);}});
  thread.start(); started.await(); thread.interrupt(); thread.join(1000); assertFalse(thread.isAlive()); assertTrue(interrupted.get()); assertEquals(0,hash.availablePermits());
  hash.release(4); assertEquals(4,hash.availablePermits());
 }
 @Test void receipt(@TempDir Path dir) throws Exception {
  Tuning.writeReceipt(dir.toString(),"test-run",4,20);
  var path=dir.resolve("tuning-"+ProcessHandle.current().pid()+".json");
  assertTrue(Files.getPosixFilePermissions(path).contains(java.nio.file.attribute.PosixFilePermission.OTHERS_READ));
  var value=Protocol.JSON.readTree(Files.readString(path));
  assertEquals("test-run",value.get("run_id").asString()); assertEquals(ProcessHandle.current().pid(),value.get("pid").asLong());
  assertEquals(Runtime.getRuntime().availableProcessors(),value.get("cpu_parallelism").asInt()); assertEquals(4,value.get("hash_concurrency").asInt()); assertEquals(20,value.get("pool_budget").asInt());
  try(var files=Files.list(dir)){assertEquals(1,files.count());} Tuning.writeReceipt(null,"ignored",4,20);
 }
}
