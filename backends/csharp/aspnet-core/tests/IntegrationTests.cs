using Xunit;
using BenchmarkApi;
public class IntegrationTests
{
    public static bool HasDatabase => Environment.GetEnvironmentVariable("CSHARP_TEST_DATABASE_URL") is not null;
    [Fact(Skip = "Set CSHARP_TEST_DATABASE_URL to a dedicated test database", SkipUnless = nameof(HasDatabase))]
    public async Task ScopedTransactionRollsBackAndSessionsAreTagged()
    {
        var config = Settings.Database(Environment.GetEnvironmentVariable("CSHARP_TEST_DATABASE_URL")!, "local");
        Assert.StartsWith("language_benchmark", config.Database);
        Assert.EndsWith("_test", config.Database);
        await using var db = new Database(config);
        var token = CancellationToken.None;
        var rows = await db.Read("SELECT current_setting('application_name') AS name,current_setting('statement_timeout') AS timeout", [], token);
        Assert.Equal("lb-backend", rows[0]["name"]);
        Assert.Equal("10s", rows[0]["timeout"]);
        await db.Read("CREATE TABLE IF NOT EXISTS csharp_transaction_guard(id bigint PRIMARY KEY)", [], token);
        await db.Read("TRUNCATE csharp_transaction_guard", [], token);
        try
        {
            await Assert.ThrowsAsync<InvalidOperationException>(() => db.Transaction<bool>(async (c, tx) => { await Database.Query(c, tx, "INSERT INTO csharp_transaction_guard VALUES($1)", [1L], token); throw new InvalidOperationException("rollback proof"); }, token));
            rows = await db.Read("SELECT count(*) AS n FROM csharp_transaction_guard", [], token);
            Assert.Equal(0L, rows[0]["n"]);
        }
        finally { await db.Read("DROP TABLE csharp_transaction_guard", [], token); }
    }
}
