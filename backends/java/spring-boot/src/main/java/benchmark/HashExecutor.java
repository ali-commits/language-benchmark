package benchmark;

import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;

/** CPU work stays on bounded platform threads, independent of virtual request carriers. */
final class HashExecutor implements AutoCloseable {
  private final Semaphore slots;
  private final ThreadPoolExecutor executor;

  HashExecutor(Semaphore slots, int threads) {
    this.slots = slots;
    var sequence = new AtomicInteger();
    executor = new ThreadPoolExecutor(threads, threads, 0L, TimeUnit.MILLISECONDS,
        new ArrayBlockingQueue<>(threads), task -> new Thread(task, "argon-worker-" + sequence.incrementAndGet()),
        new ThreadPoolExecutor.AbortPolicy());
  }

  byte[] run(Callable<byte[]> computation) {
    try { slots.acquire(); }
    catch (InterruptedException e) { Thread.currentThread().interrupt(); throw new IllegalStateException(e); }
    var result = new CompletableFuture<byte[]>();
    try {
      executor.execute(() -> {
        try { result.complete(computation.call()); }
        catch (Throwable e) { result.completeExceptionally(e); }
        finally { slots.release(); }
      });
    } catch (RuntimeException e) { slots.release(); throw e; }
    try { return result.get(); }
    catch (InterruptedException e) {
      // Do not cancel queued/running work: its finally owns the permit until computation ends.
      Thread.currentThread().interrupt(); throw new IllegalStateException(e);
    } catch (ExecutionException e) { throw new IllegalStateException(e.getCause()); }
  }

  @Override public void close() {
    executor.shutdown();
    try { executor.awaitTermination(30, TimeUnit.SECONDS); }
    catch (InterruptedException e) { Thread.currentThread().interrupt(); }
  }
}
