using System.Globalization;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
namespace BenchmarkApi;

public sealed class ApiError(int status, object? detail, bool bearer = false) : Exception("HTTP " + status)
{
    public int Status { get; } = status; public object? Detail { get; } = detail; public bool Bearer { get; } = bearer;
}
public static class Protocol
{
    public static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web);
    public static string Base64(byte[] value) => Convert.ToBase64String(value).TrimEnd('=').Replace('+', '-').Replace('/', '_');
    public static byte[] Unbase64(string value) => Convert.FromBase64String(value.Replace('-', '+').Replace('_', '/') + new string('=', (4 - value.Length % 4) % 4));
    public static string Stamp(DateTime value) => value.ToUniversalTime().ToString("yyyy-MM-dd'T'HH:mm:ss.ffffff'Z'", CultureInfo.InvariantCulture);
    public static string EncodeCursor(DateTime value, long id) => Base64(JsonSerializer.SerializeToUtf8Bytes(new object[] { Stamp(value), id }));
    public static (DateTime? Time, long Id) DecodeCursor(string? value)
    {
        if (value is null)
            return (null, 0);
        try
        {
            if (value.Length is < 1 or > 256)
                throw new FormatException();
            using var doc = JsonDocument.Parse(Unbase64(value));
            var root = doc.RootElement;
            if (root.ValueKind != JsonValueKind.Array || root.GetArrayLength() != 2 || root[0].ValueKind != JsonValueKind.String || root[1].ValueKind != JsonValueKind.Number || !root[1].TryGetInt64(out var id) || id <= 0)
                throw new FormatException();
            var text = root[0].GetString()!;
            if (!Regex.IsMatch(text, @"\A\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})\z"))
                throw new FormatException();
            var time = DateTimeOffset.Parse(text, CultureInfo.InvariantCulture, DateTimeStyles.None).UtcDateTime;
            return (time, id);
        }
        catch (Exception e) when (e is FormatException or JsonException or ArgumentException or InvalidOperationException) { throw new ApiError(422, "invalid cursor"); }
    }
    public static Dictionary<string, object?> Error(string type, object[] location, string message, object? input, object? context = null)
    {
        var d = new Dictionary<string, object?> { { "type", type }, { "loc", location }, { "msg", message }, { "input", input } };
        if (context is not null)
            d["ctx"] = context;
        return d;
    }
    public static Dictionary<string, string> Validate(JsonElement input, string kind)
    {
        if (input.ValueKind is JsonValueKind.Null or JsonValueKind.Undefined)
            throw new ApiError(422, new[] { Error("missing", ["body"], "Field required", null) });
        if (input.ValueKind != JsonValueKind.Object)
            throw new ApiError(422, new[] { Error("model_attributes_type", ["body"], "Input should be a valid dictionary or object to extract fields from", input.Clone()) });
        string[] fields = kind is "login" or "register" ? ["username", "password"] : ["content"];
        var errors = new List<Dictionary<string, object?>>();
        var result = new Dictionary<string, string>();
        foreach (var field in fields)
        {
            if (!input.TryGetProperty(field, out var value))
            {
                errors.Add(Error("missing", ["body", field], "Field required", input.Clone()));
                continue;
            }
            if (value.ValueKind != JsonValueKind.String)
            {
                errors.Add(Error("string_type", ["body", field], "Input should be a valid string", value.Clone()));
                continue;
            }
            var text = value.GetString()!;
            int min = field == "username" ? 3 : field == "password" && kind == "register" ? 12 : 1, max = field == "username" ? 32 : field == "password" ? 128 : kind == "comment" ? 1000 : 280;
            int n = text.EnumerateRunes().Count();
            if (n < min)
                errors.Add(Error("string_too_short", ["body", field], $"String should have at least {min} character{(min == 1 ? "" : "s")}", text, new
                {
                    min_length = min
                }));
            else if (n > max)
                errors.Add(Error("string_too_long", ["body", field], $"String should have at most {max} characters", text, new
                {
                    max_length = max
                }));
            else if (field == "username" && !Regex.IsMatch(text, @"\A[A-Za-z0-9_]+\z"))
                errors.Add(Error("string_pattern_mismatch", ["body", field], "String should match pattern '^[A-Za-z0-9_]+$'", text, new
                {
                    pattern = "^[A-Za-z0-9_]+$"
                }));
            else if (field == "content" && text.All(c => char.IsWhiteSpace(c) || c is >= '\u001c' and <= '\u001f'))
                errors.Add(Error("value_error", ["body", field], "Value error, content must not be blank", text, new
                {
                    error = new
                    {
                    }
                }));
            else
                result[field] = field == "username" ? text.ToLowerInvariant() : text;
        }
        foreach (var p in input.EnumerateObject())
            if (!fields.Contains(p.Name))
                errors.Add(Error("extra_forbidden", ["body", p.Name], "Extra inputs are not permitted", p.Value.Clone()));
        if (errors.Count != 0)
            throw new ApiError(422, errors);
        return result;
    }
    public static long PathId(string value, string field)
    {
        if (long.TryParse(value, NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture, out var id))
            return id;
        throw new ApiError(422, new[] { Error("int_parsing", ["path", field], "Input should be a valid integer, unable to parse string as an integer", value) });
    }
    public static (int Limit, DateTime? Time, long Id) Page(HttpRequest request)
    {
        var raw = request.Query.TryGetValue("limit", out var values) ? values.Last()! : "20";
        if (!long.TryParse(raw, NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture, out var limit))
            throw new ApiError(422, new[] { Error("int_parsing", ["query", "limit"], "Input should be a valid integer, unable to parse string as an integer", raw) });
        if (limit < 1 || limit > 100)
            throw new ApiError(422, new[] { Error(limit < 1 ? "greater_than_equal" : "less_than_equal", ["query", "limit"], limit < 1 ? "Input should be greater than or equal to 1" : "Input should be less than or equal to 100", raw, limit < 1 ? (object)new { ge = 1 } : new { le = 100 }) });
        var cursor = request.Query.TryGetValue("cursor", out values) ? values.Last() : null;
        var decoded = DecodeCursor(cursor);
        return ((int)limit, decoded.Time, decoded.Id);
    }
    public static async Task<Dictionary<string, string>> Body(HttpRequest request, string kind)
    {
        var data = new MemoryStream();
        var buffer = new byte[4096];
        int n;
        while ((n = await request.Body.ReadAsync(buffer, request.HttpContext.RequestAborted)) != 0)
        {
            if (data.Length + n > 16384)
                throw new ApiError(413, "request body too large");
            data.Write(buffer, 0, n);
        }
        if (data.Length == 0)
            throw new ApiError(422, new[] { Error("missing", ["body"], "Field required", null) });
        // FastAPI's strict media policy only parses explicitly declared application JSON.
        // Unsupported/absent types remain raw text inputs, producing model validation422.
        var declared = Microsoft.Net.Http.Headers.MediaTypeHeaderValue.TryParse(request.ContentType, out var parsed)
            ? parsed.MediaType.Value : null;
        bool jsonMedia = declared is not null && (declared.Equals("application/json", StringComparison.OrdinalIgnoreCase)
            || declared.StartsWith("application/", StringComparison.OrdinalIgnoreCase) && declared.EndsWith("+json", StringComparison.OrdinalIgnoreCase));
        if (!jsonMedia)
            throw new ApiError(422, new[] { Error("model_attributes_type", ["body"], "Input should be a valid dictionary or object to extract fields from", Encoding.UTF8.GetString(data.GetBuffer(), 0, (int)data.Length)) });
        try
        {
            new UTF8Encoding(false, true).GetString(data.GetBuffer(), 0, (int)data.Length);
        }
        catch (DecoderFallbackException) { throw new ApiError(400, "There was an error parsing the body"); }
        try
        {
            using var doc = JsonDocument.Parse(data.ToArray());
            return Validate(doc.RootElement, kind);
        }
        catch (JsonException e) { throw new ApiError(422, new[] { Error("json_invalid", ["body", (int)(e.BytePositionInLine ?? 0)], "JSON decode error", new { }, new { error = "Expecting value" }) }); }
    }
}
