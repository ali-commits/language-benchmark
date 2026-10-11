using Microsoft.AspNetCore.WebUtilities;
using Npgsql;
namespace BenchmarkApi;

public static class Settings
{
    public static int HashConcurrency(string? value)
    {
        if (value is null) return 2;
        if (value.Length != 1 || value[0] < '1' || value[0] > '4')
            throw new ArgumentException("HASH_CONCURRENCY must be an integer between 1 and 4");
        return value[0] - '0';
    }
    public static void WriteTuningReceipt(string? directory, string? runId, int hashes, int poolSize, NpgsqlConnectionStringBuilder? configuration=null)
    {
        if (string.IsNullOrEmpty(directory)) return;
        var payload = new Dictionary<string,object?> {
            ["schema_version"]=1,["run_id"]=runId??"",["pid"]=Environment.ProcessId,["language"]="csharp",["workers_total"]=1,["connections_per_worker"]=poolSize,["pool_budget"]=poolSize,["cpu_parallelism"]=Environment.ProcessorCount,["hash_concurrency"]=hashes,["hash_scope"]="process"
        };
        if(Environment.GetEnvironmentVariable("BENCH_PROFILE")=="production4") {
            if(configuration is null || configuration.MaxPoolSize!=poolSize) throw new ArgumentException("production receipt requires actual database configuration");
            payload["schema_version"]=2;payload["pool_min_size"]=configuration.MinPoolSize;
            payload["db_prepare_enabled"]=configuration.MaxAutoPrepare>0;
            payload["db_max_auto_prepare"]=configuration.MaxAutoPrepare;
            payload["db_auto_prepare_min_usages"]=configuration.AutoPrepareMinUsages;
            payload["db_enlist"]=configuration.Enlist;
        }
        var temporary = Path.Combine(directory, $".tuning-{Environment.ProcessId}-{Guid.NewGuid()}.tmp");
        try
        {
            File.WriteAllText(temporary, System.Text.Json.JsonSerializer.Serialize(payload));
            if (!OperatingSystem.IsWindows())
                File.SetUnixFileMode(temporary, UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.GroupRead | UnixFileMode.OtherRead);
            File.Move(temporary, Path.Combine(directory, $"tuning-{Environment.ProcessId}.json"), true);
        }
        finally { File.Delete(temporary); }
    }

    public static (int Max,int Min) PoolBounds(string? max,string? min) {
        static int Parse(string? value,int fallback,int minimum) {
            if(value is null) return fallback;
            if(!int.TryParse(value,System.Globalization.NumberStyles.None,System.Globalization.CultureInfo.InvariantCulture,out var n) || n<minimum || n>256 || n.ToString(System.Globalization.CultureInfo.InvariantCulture)!=value) throw new ArgumentException("invalid DB pool size");
            return n;
        }
        var maximum=Parse(max,20,1);var minimum=Parse(min,0,0);
        if(minimum>maximum) throw new ArgumentException("DB pool minimum exceeds maximum");
        return (maximum,minimum);
    }
    public static NpgsqlConnectionStringBuilder Database(string value,string mode) => Database(value,mode,Environment.GetEnvironmentVariable("DB_POOL_MAX_SIZE"),Environment.GetEnvironmentVariable("DB_POOL_MIN_SIZE"),Environment.GetEnvironmentVariable("DB_PREPARE_ENABLED"));
    public static NpgsqlConnectionStringBuilder Database(string value, string mode,string? max,string? min,string? prepare)
    {
        if (!Uri.TryCreate(value.Replace("postgresql+psycopg://", "postgresql://"), UriKind.Absolute, out var uri) || uri.Scheme != "postgresql" || string.IsNullOrEmpty(uri.Host) || uri.AbsolutePath.Length < 2)
            throw new ArgumentException("PostgreSQL URL required");
        var query = QueryHelpers.ParseQuery(uri.Query);
        foreach (var key in new[] { "sslmode", "sslrootcert" })
            if (query.TryGetValue(key, out var values) && values.Count != 1)
                throw new ArgumentException("ambiguous TLS configuration");
        var ssl = query.TryGetValue("sslmode", out var modes) ? modes[0] : null;
        var root = query.TryGetValue("sslrootcert", out var roots) ? roots[0] : null;
        if (mode == "aws" && (ssl != "verify-full" || string.IsNullOrWhiteSpace(root)))
            throw new ArgumentException("AWS requires sslmode=verify-full and sslrootcert");
        var userInfo = uri.GetComponents(UriComponents.UserInfo, UriFormat.UriEscaped).Split(':', 2);
        var pool=PoolBounds(max,min);
        if(prepare is not null && prepare is not "true" and not "false") throw new ArgumentException("DB_PREPARE_ENABLED must be true or false");
        var builder = new NpgsqlConnectionStringBuilder { Host = uri.Host, Port = uri.IsDefaultPort ? 5432 : uri.Port, Database = Uri.UnescapeDataString(uri.AbsolutePath[1..]), Username = Uri.UnescapeDataString(userInfo[0]), Password = userInfo.Length > 1 ? Uri.UnescapeDataString(userInfo[1]) : "", MaxPoolSize = pool.Max, MinPoolSize = pool.Min, GssEncryptionMode = GssEncryptionMode.Disable, ApplicationName = "lb-backend", Options = "-c statement_timeout=10000", Timeout = 5, CommandTimeout = 10, ConnectionIdleLifetime = 300 };
        if(prepare=="true") {builder.MaxAutoPrepare=64;builder.AutoPrepareMinUsages=5;builder.Enlist=false;}
        if (ssl is not null)
            builder.SslMode = ssl switch
            {
                "disable" => SslMode.Disable,
                "allow" => SslMode.Allow,
                "prefer" => SslMode.Prefer,
                "require" => SslMode.Require,
                "verify-ca" => SslMode.VerifyCA,
                "verify-full" => SslMode.VerifyFull,
                _ => throw new ArgumentException("invalid sslmode")
            };
        if (root is not null)
            builder.RootCertificate = root;
        return builder;
    }
}
