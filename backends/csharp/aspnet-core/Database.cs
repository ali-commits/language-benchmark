using System.Diagnostics;
using Npgsql;
using NpgsqlTypes;
namespace BenchmarkApi;

public sealed class Database : IAsyncDisposable
{
    private readonly NpgsqlDataSource source; private long acquires, ticks;
    public Database(NpgsqlConnectionStringBuilder configuration) => source = new NpgsqlDataSourceBuilder(configuration.ConnectionString).Build();
    public async Task<NpgsqlConnection> Open(CancellationToken token)
    {
        long start = Stopwatch.GetTimestamp();
        var connection = await source.OpenConnectionAsync(token);
        Interlocked.Increment(ref acquires);
        Interlocked.Add(ref ticks, Stopwatch.GetTimestamp() - start);
        return connection;
    }
    public object Metrics() => new { connection_acquires = Interlocked.Read(ref acquires), connection_acquire_ms = Interlocked.Read(ref ticks) * 1000.0 / Stopwatch.Frequency };
    public static async Task<List<Dictionary<string, object?>>> Query(NpgsqlConnection connection, NpgsqlTransaction? transaction, string sql, object?[] values, CancellationToken token)
    {
        await using var command = new NpgsqlCommand(sql, connection, transaction);
        foreach (var value in values)
        {
            var parameter = new NpgsqlParameter { Value = value ?? DBNull.Value };
            if (value is null)
                parameter.NpgsqlDbType = NpgsqlDbType.Unknown;
            command.Parameters.Add(parameter);
        }
        await using var rows = await command.ExecuteReaderAsync(token);
        var result = new List<Dictionary<string, object?>>();
        while (await rows.ReadAsync(token))
        {
            var item = new Dictionary<string, object?>();
            for (int i = 0; i < rows.FieldCount; i++)
                item[rows.GetName(i)] = await rows.IsDBNullAsync(i, token) ? null : rows.GetValue(i);
            result.Add(item);
        }
        return result;
    }
    public async Task<List<Dictionary<string, object?>>> Read(string sql, object?[] values, CancellationToken token)
    {
        await using var connection = await Open(token);
        return await Query(connection, null, sql, values, token);
    }
    public async Task<T> Transaction<T>(Func<NpgsqlConnection, NpgsqlTransaction, Task<T>> action, CancellationToken token)
    {
        await using var connection = await Open(token);
        await using var transaction = await connection.BeginTransactionAsync(token);
        try
        {
            var value = await action(connection, transaction);
            await transaction.CommitAsync(token);
            return value;
        }
        catch { await transaction.RollbackAsync(CancellationToken.None); throw; }
    }
    public ValueTask DisposeAsync() => source.DisposeAsync();
}
