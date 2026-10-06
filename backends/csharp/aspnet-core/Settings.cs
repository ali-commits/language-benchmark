using Microsoft.AspNetCore.WebUtilities;
using Npgsql;
namespace BenchmarkApi;

public static class Settings
{
    public static NpgsqlConnectionStringBuilder Database(string value, string mode)
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
        var builder = new NpgsqlConnectionStringBuilder { Host = uri.Host, Port = uri.IsDefaultPort ? 5432 : uri.Port, Database = Uri.UnescapeDataString(uri.AbsolutePath[1..]), Username = Uri.UnescapeDataString(userInfo[0]), Password = userInfo.Length > 1 ? Uri.UnescapeDataString(userInfo[1]) : "", MaxPoolSize = 20, MinPoolSize = 0, GssEncryptionMode = GssEncryptionMode.Disable, ApplicationName = "lb-backend", Options = "-c statement_timeout=10000", Timeout = 5, CommandTimeout = 10, ConnectionIdleLifetime = 300 };
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
