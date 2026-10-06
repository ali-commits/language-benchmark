using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Konscious.Security.Cryptography;
using Microsoft.IdentityModel.JsonWebTokens;
using Microsoft.IdentityModel.Tokens;
namespace BenchmarkApi;

public sealed class PasswordService
{
    private readonly SemaphoreSlim slots = new(2, 2);
    private static readonly string Dummy = "$argon2id$v=19$m=65536,t=3,p=1$MDEyMzQ1Njc4OWFiY2RlZg$6LZzQxzV/D4kfEE2uvT2MhHRKaCWGjBE36GO9JsDk3k";
    private async Task<byte[]> Derive(string password, byte[] salt, CancellationToken token)
    {
        await slots.WaitAsync(token);
        try
        {
            token.ThrowIfCancellationRequested();
            var bytes = Encoding.UTF8.GetBytes(password);
            try
            {
                using var argon = new Argon2id(bytes) { Salt = salt, MemorySize = 65536, Iterations = 3, DegreeOfParallelism = 1 };
                return await argon.GetBytesAsync(32);
            }
            finally { CryptographicOperations.ZeroMemory(bytes); }
        }
        finally { slots.Release(); }
    }
    public async Task<string> Hash(string password, CancellationToken token)
    {
        var salt = RandomNumberGenerator.GetBytes(16);
        var hash = await Derive(password, salt, token);
        return "$argon2id$v=19$m=65536,t=3,p=1$" + Convert.ToBase64String(salt).TrimEnd('=') + "$" + Convert.ToBase64String(hash).TrimEnd('=');
    }
    public async Task<bool> Verify(string password, string? hash, CancellationToken token)
    {
        hash ??= Dummy;
        var parts = hash.Split('$');
        if (parts.Length != 6 || parts[1] != "argon2id" || parts[2] != "v=19" || parts[3] != "m=65536,t=3,p=1")
            return false;
        try
        {
            var salt = Protocol.Unbase64(parts[4]);
            var expected = Protocol.Unbase64(parts[5]);
            if (salt.Length != 16 || expected.Length != 32)
                return false;
            var computed = await Derive(password, salt, token);
            return CryptographicOperations.FixedTimeEquals(expected, computed);
        }
        catch (FormatException) { return false; }
    }
}
public sealed class TokenService
{
    private readonly JsonWebTokenHandler handler = new() { SetDefaultTimesOnTokenCreation = false, MapInboundClaims = false }; private readonly SymmetricSecurityKey key; private readonly int ttl;
    public TokenService(string secret, int ttl)
    {
        if (Encoding.UTF8.GetByteCount(secret) < 32 || ttl is < 600 or > 86400)
            throw new ArgumentException("invalid JWT configuration");
        key = new(Encoding.UTF8.GetBytes(secret));
        this.ttl = ttl;
    }
    public string Issue(long id, long now) => handler.CreateToken(new SecurityTokenDescriptor { Claims = new Dictionary<string, object> { { "sub", id.ToString(System.Globalization.CultureInfo.InvariantCulture) }, { "iat", now }, { "exp", now + ttl }, { "iss", "language-benchmark" }, { "aud", "social-api" } }, SigningCredentials = new(key, SecurityAlgorithms.HmacSha256) });
    public async Task<long> Identity(string token)
    {
        try
        {
            var segments = token.Split('.');
            if (segments.Length != 3)
                throw new FormatException();
            using var doc = JsonDocument.Parse(Protocol.Unbase64(segments[1]));
            var claims = doc.RootElement;
            long now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
            if (!claims.TryGetProperty("iat", out var iat) || !iat.TryGetInt64(out long issued) || issued > now + 30 || !claims.TryGetProperty("exp", out var exp) || !exp.TryGetInt64(out long expires) || expires <= now || !claims.TryGetProperty("sub", out var subject) || subject.ValueKind != JsonValueKind.String || !long.TryParse(subject.GetString(), out var id) || id <= 0)
                throw new FormatException();
            if (claims.TryGetProperty("nbf", out var nbf))
            {
                long starts;
                if (nbf.ValueKind == JsonValueKind.Number && nbf.TryGetInt64(out starts))
                {
                }
                else if (nbf.ValueKind == JsonValueKind.String && long.TryParse(nbf.GetString(), out starts))
                {
                }
                else
                    throw new FormatException();
                if (starts > now)
                    throw new FormatException();
            }
            var result = await handler.ValidateTokenAsync(token, new TokenValidationParameters { ValidateIssuer = true, ValidIssuer = "language-benchmark", ValidateAudience = true, ValidAudience = "social-api", RequireExpirationTime = true, ValidateLifetime = true, ClockSkew = TimeSpan.Zero, RequireSignedTokens = true, ValidateIssuerSigningKey = true, IssuerSigningKey = key, ValidAlgorithms = [SecurityAlgorithms.HmacSha256] });
            if (!result.IsValid)
                throw new FormatException();
            return id;
        }
        catch (Exception e) when (e is FormatException or ArgumentException or JsonException or InvalidOperationException) { throw new ApiError(401, "invalid credentials", true); }
    }
}
