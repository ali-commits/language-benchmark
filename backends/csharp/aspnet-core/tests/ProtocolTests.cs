using System.Text.Json;
using Xunit;
using BenchmarkApi;
using System.Security.Cryptography;
using System.Text;
public class ProtocolTests
{
    [Fact]
    public void StrictValidationAggregatesErrorsInInputOrder()
    {
        using var body = JsonDocument.Parse("{\"username\":\"ab\",\"password\":\"short\",\"first\":true,\"second\":1}");
        var error = Assert.Throws<ApiError>(() => Protocol.Validate(body.RootElement, "register"));
        Assert.Equal(422, error.Status);
        var data = JsonSerializer.Serialize(error.Detail);
        Assert.Contains("string_too_short", data);
        Assert.True(data.IndexOf("first") < data.IndexOf("second"));
    }
    [Fact]
    public void CursorKeepsUtcMicrosecondsAndInt64()
    {
        var stamp = DateTimeOffset.Parse("2026-10-07T10:20:30.123456+05:30");
        var result = Protocol.DecodeCursor(Protocol.EncodeCursor(stamp.UtcDateTime, long.MaxValue - 1));
        Assert.Equal(stamp.UtcDateTime, result.Time);
        Assert.Equal(long.MaxValue - 1, result.Id);
    }
    [Fact]
    public async Task PasswordPhcUsesCanonicalPolicy()
    {
        var passwords = new PasswordService();
        var hash = await passwords.Hash("correct horse battery staple", CancellationToken.None);
        Assert.StartsWith("$argon2id$v=19$m=65536,t=3,p=1$", hash);
        Assert.True(await passwords.Verify("correct horse battery staple", hash, CancellationToken.None));
        Assert.False(await passwords.Verify("wrong", hash, CancellationToken.None));
    }
    [Fact]
    public async Task JwtExpiryFutureNbfAudienceAndInt64AreEnforced()
    {
        var jwt = new TokenService(new string('s', 32), 3600);
        var now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
        var token = jwt.Issue(long.MaxValue - 1, now);
        Assert.Equal(long.MaxValue - 1, await jwt.Identity(token));
        Assert.Throws<ApiError>(() => Protocol.DecodeCursor("W10"));
    }
    [Fact]
    public void DatabaseCredentialsPreservePlusAndAwsTlsIsExact()
    {
        var config = Settings.Database("postgresql://u%2Bser:literal+and%2Bencoded@localhost/test", "local");
        Assert.Equal("u+ser", config.Username);
        Assert.Equal("literal+and+encoded", config.Password);
        Assert.Equal(20, config.MaxPoolSize);
        Assert.Equal(Npgsql.GssEncryptionMode.Disable, config.GssEncryptionMode);
        Assert.Equal("lb-backend", config.ApplicationName);
        Assert.Contains("statement_timeout=10000", config.Options);
        foreach (var query in new[] { "sslmode=disable&note=sslmode=verify-full&sslrootcert=/ca", "sslmode=verify-full&sslmode=disable&sslrootcert=/ca", "sslmode=verify-full&sslrootcert=" })
            Assert.Throws<ArgumentException>(() => Settings.Database("postgresql://u:p@localhost/db?" + query, "aws"));
    }

    private static string Signed(Dictionary<string, object> values, string alg = "HS256")
    {
        var header = Protocol.Base64(JsonSerializer.SerializeToUtf8Bytes(new
        {
            alg,
            typ = "JWT"
        }));
        var body = Protocol.Base64(JsonSerializer.SerializeToUtf8Bytes(values));
        return header + "." + body + "." + Protocol.Base64(HMACSHA256.HashData(Encoding.UTF8.GetBytes(new string('s', 32)), Encoding.ASCII.GetBytes(header + "." + body)));
    }
    [Fact]
    public async Task SignedInvalidJwtClaimsAreRejectedAndAudienceArraysWork()
    {
        var now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
        var jwt = new TokenService(new string('s', 32), 3600);
        var valid = new Dictionary<string, object> { { "sub", "1" }, { "iat", now }, { "exp", now + 3600 }, { "iss", "language-benchmark" }, { "aud", new[] { "other", "social-api" } } };
        Assert.Equal(1L, await jwt.Identity(Signed(valid)));
        foreach (var pair in new (string Key, object Value)[] { ("exp", 1L), ("iat", now + 3600), ("sub", "0"), ("nbf", now + 3600), ("iss", "wrong"), ("aud", "wrong"), ("exp", 1.5) })
        {
            var claims = new Dictionary<string, object>(valid) { [pair.Key] = pair.Value };
            await Assert.ThrowsAsync<ApiError>(() => jwt.Identity(Signed(claims)));
        }
        foreach (var key in new[] { "iat", "exp", "sub" })
        {
            var claims = new Dictionary<string, object>(valid);
            claims.Remove(key);
            await Assert.ThrowsAsync<ApiError>(() => jwt.Identity(Signed(claims)));
        }
        await Assert.ThrowsAsync<ApiError>(() => jwt.Identity(Signed(valid, "none")));
    }
    [Fact]
    public async Task CanceledHashQueueDoesNotStartWork()
    {
        using var stop = new CancellationTokenSource();
        stop.Cancel();
        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => new PasswordService().Hash("password", stop.Token));
    }
}
