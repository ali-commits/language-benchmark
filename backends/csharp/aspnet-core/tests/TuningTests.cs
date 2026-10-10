using BenchmarkApi;
using System.Text.Json;
using Xunit;
public class TuningTests
{
 [Fact] public void ValidatesHashConcurrency() {
  Assert.Equal(2,Settings.HashConcurrency(null)); Assert.Equal(4,Settings.HashConcurrency("4"));
  foreach(var value in new[]{"", "bad", "0", "5", "-1", "+4", " 4", "999999999999999999"}) Assert.Throws<ArgumentException>(()=>Settings.HashConcurrency(value));
 }
 [Fact] public async Task AdmissionAndCancellation() {
  var service=new PasswordService(4);
  var field=typeof(PasswordService).GetField("slots",System.Reflection.BindingFlags.NonPublic|System.Reflection.BindingFlags.Instance)!;
  var slots=(SemaphoreSlim)field.GetValue(service)!;
  for(int i=0;i<4;i++) await slots.WaitAsync(TestContext.Current.CancellationToken);
  using var cancellation=new CancellationTokenSource(); var waiting=service.Hash("queued",cancellation.Token); Assert.False(waiting.IsCompleted);
  cancellation.Cancel(); await Assert.ThrowsAnyAsync<OperationCanceledException>(()=>waiting);
  Assert.Equal(0,slots.CurrentCount); slots.Release(4); Assert.Equal(4,slots.CurrentCount);
  var hash=await service.Hash("twelve-chars-password",TestContext.Current.CancellationToken); Assert.StartsWith("$argon2id$v=19$m=65536,t=3,p=1$",hash); Assert.Equal(4,slots.CurrentCount);
 }
 [Fact] public void Receipt() {
  var dir=Path.Combine(Path.GetTempPath(),Guid.NewGuid().ToString()); Directory.CreateDirectory(dir);
  try {
   Settings.WriteTuningReceipt(dir,"test-run",4,20);
   if (!OperatingSystem.IsWindows()) Assert.Equal(UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.GroupRead | UnixFileMode.OtherRead, File.GetUnixFileMode(Path.Combine(dir,$"tuning-{Environment.ProcessId}.json")));
   using var doc=JsonDocument.Parse(File.ReadAllText(Path.Combine(dir,$"tuning-{Environment.ProcessId}.json"))); var v=doc.RootElement;
   Assert.Equal(Environment.ProcessId,v.GetProperty("pid").GetInt32()); Assert.Equal(Environment.ProcessorCount,v.GetProperty("cpu_parallelism").GetInt32()); Assert.Equal(4,v.GetProperty("hash_concurrency").GetInt32()); Assert.Equal(20,v.GetProperty("pool_budget").GetInt32()); Assert.Equal("test-run",v.GetProperty("run_id").GetString()); Assert.Single(Directory.GetFiles(dir));
   Settings.WriteTuningReceipt(null,"unused",4,20);
  } finally {Directory.Delete(dir,true);}
 }
}
