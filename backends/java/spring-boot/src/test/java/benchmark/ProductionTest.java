package benchmark;
import static org.junit.jupiter.api.Assertions.*;
import java.util.concurrent.*;
import org.junit.jupiter.api.Test;
class ProductionTest {
 @Test void boundsAreStrictAndLegacyDefaultsRemain() {
  assertArrayEquals(new int[]{20,2},Tuning.poolBounds(null,null));
  assertArrayEquals(new int[]{16,16},Tuning.poolBounds("16","16"));
  for(var v:new String[]{"","-1","+16"," 16","0","257","01"}) assertThrows(IllegalArgumentException.class,()->Tuning.poolBounds(v,null));
  assertThrows(IllegalArgumentException.class,()->Tuning.poolBounds("16","17"));
 }
 @Test void receiptUsesActualProductionSettings(@org.junit.jupiter.api.io.TempDir java.nio.file.Path dir) throws Exception {
  Tuning.writeReceipt(dir.toString(),"production",4,16,16);
  var v=Protocol.JSON.readTree(java.nio.file.Files.readString(dir.resolve("tuning-"+ProcessHandle.current().pid()+".json")));
  assertEquals(16,v.get("pool_budget").asInt());
  if("production4".equals(System.getenv("BENCH_PROFILE"))) {assertEquals(2,v.get("schema_version").asInt());assertEquals(16,v.get("pool_min_size").asInt());assertEquals("platform",v.get("hash_executor_mode").asString());}
  else {assertEquals(1,v.get("schema_version").asInt());assertNull(v.get("pool_min_size"));}
 }
 @Test void cancelledCallerDoesNotReleaseRunningHash() throws Exception {
  var slots=new Semaphore(1);var entered=new CountDownLatch(1);var finish=new CountDownLatch(1);
  try(var worker=new HashExecutor(slots,1)) {
   var caller=new Thread(()->assertThrows(IllegalStateException.class,()->worker.run(()->{assertFalse(Thread.currentThread().isVirtual()); entered.countDown();finish.await();return new byte[1];})));
   caller.start();assertTrue(entered.await(2,TimeUnit.SECONDS));caller.interrupt();caller.join(2000);assertFalse(caller.isAlive());assertEquals(0,slots.availablePermits());finish.countDown();
   assertTrue(slots.tryAcquire(2,TimeUnit.SECONDS));slots.release();
  } finally {finish.countDown();}
 }
 @Test void rejectedSubmissionReturnsPermit() {
  var slots=new Semaphore(1);var worker=new HashExecutor(slots,1);worker.close();
  assertThrows(RejectedExecutionException.class,()->worker.run(()->new byte[1]));assertEquals(1,slots.availablePermits());
 }
}
