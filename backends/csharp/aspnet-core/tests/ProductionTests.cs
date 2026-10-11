using BenchmarkApi;
using Xunit;
public class ProductionTests {
 [Fact] public void PoolConfigurationIsStrictAndDefaultsArePreserved() {
  Assert.Equal((20,0),Settings.PoolBounds(null,null));
  Assert.Equal((16,4),Settings.PoolBounds("16","4"));
  foreach(var value in new[]{"","-1","+16"," 16","0","257","01"}) Assert.Throws<ArgumentException>(()=>Settings.PoolBounds(value,null));
  Assert.Throws<ArgumentException>(()=>Settings.PoolBounds("16","17"));
 }
 [Fact] public void ReceiptContainsActualBuilderSettings() {
  var dir=Path.Combine(Path.GetTempPath(),Guid.NewGuid().ToString());Directory.CreateDirectory(dir);
  try {
   var cfg=Settings.Database("postgresql://u:p@localhost/db","local","16","4","true");Settings.WriteTuningReceipt(dir,"production",4,16,cfg);
   using var doc=System.Text.Json.JsonDocument.Parse(File.ReadAllText(Path.Combine(dir,$"tuning-{Environment.ProcessId}.json")));var v=doc.RootElement;
   Assert.Equal(16,v.GetProperty("pool_budget").GetInt32());
   if(Environment.GetEnvironmentVariable("BENCH_PROFILE")=="production4") {
    Assert.Equal(2,v.GetProperty("schema_version").GetInt32());Assert.Equal(4,v.GetProperty("pool_min_size").GetInt32());Assert.True(v.GetProperty("db_prepare_enabled").GetBoolean());Assert.Equal(64,v.GetProperty("db_max_auto_prepare").GetInt32());Assert.Equal(5,v.GetProperty("db_auto_prepare_min_usages").GetInt32());Assert.False(v.GetProperty("db_enlist").GetBoolean());
   } else {Assert.Equal(1,v.GetProperty("schema_version").GetInt32());Assert.False(v.TryGetProperty("pool_min_size",out _));}
  } finally {Directory.Delete(dir,true);}
 }
 [Fact] public void ProductionPreparationIsExplicit() {
  var cfg=Settings.Database("postgresql://u:p@localhost/db","local", "16","4", "true");
  Assert.Equal(16,cfg.MaxPoolSize);Assert.Equal(4,cfg.MinPoolSize);Assert.Equal(64,cfg.MaxAutoPrepare);Assert.Equal(5,cfg.AutoPrepareMinUsages);Assert.False(cfg.Enlist);
  var legacy=Settings.Database("postgresql://u:p@localhost/db","local",null,null,null);
  Assert.Equal(20,legacy.MaxPoolSize);Assert.Equal(0,legacy.MaxAutoPrepare);Assert.True(legacy.Enlist);
  Assert.Throws<ArgumentException>(()=>Settings.Database("postgresql://u:p@localhost/db","local",null,null,"yes"));
 }
}
public class ProductionDatabaseTests {
 public static bool HasDatabase => Environment.GetEnvironmentVariable("CSHARP_TEST_DATABASE_URL") is not null;
 [Fact(Skip="Set CSHARP_TEST_DATABASE_URL to disposable database",SkipUnless=nameof(HasDatabase))]
 public async Task AutomaticPreparationPersistsOnPhysicalConnection() {
  var cfg=Settings.Database(Environment.GetEnvironmentVariable("CSHARP_TEST_DATABASE_URL")!,"local","16","4","true");
  Assert.StartsWith("language_benchmark",cfg.Database);Assert.EndsWith("_test",cfg.Database);
  await using var db=new Database(cfg);
  var token=TestContext.Current.CancellationToken;
  await using var connection=await db.Open(token);
  for(var i=0;i<8;i++) {
   var rows=await Database.Query(connection,null,"SELECT $1::bigint AS value",[42L],token);Assert.Equal(42L,rows[0]["value"]);
  }
  var prepared=await Database.Query(connection,null,"SELECT count(*) AS n FROM pg_prepared_statements WHERE statement='SELECT $1::bigint AS value'",[],token);
  Assert.Equal(1L,prepared[0]["n"]);
 }
}
