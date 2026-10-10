using BenchmarkApi;
using Microsoft.AspNetCore.Server.Kestrel.Core;
var builder = WebApplication.CreateBuilder(args);
builder.Logging.ClearProviders();
builder.Logging.AddSimpleConsole();
builder.Logging.SetMinimumLevel(LogLevel.Warning);
builder.WebHost.ConfigureKestrel(options => { options.Limits.MaxRequestBodySize = null; options.Limits.KeepAliveTimeout = TimeSpan.FromSeconds(60); options.Limits.RequestHeadersTimeout = TimeSpan.FromSeconds(5); });
var database = Settings.Database(Environment.GetEnvironmentVariable("DATABASE_URL") ?? "", Environment.GetEnvironmentVariable("MODE") ?? "local");
var secret = Environment.GetEnvironmentVariable("JWT_SECRET") ?? "";
int ttl = int.Parse(Environment.GetEnvironmentVariable("JWT_TTL_SECONDS") ?? "3600");
builder.Services.AddSingleton(new Database(database));
builder.Services.AddSingleton(new PasswordService());
builder.Services.AddSingleton(new TokenService(secret, ttl));
await using var app = builder.Build();
app.Urls.Add($"http://{Environment.GetEnvironmentVariable("BIND_IP") ?? "0.0.0.0"}:{Environment.GetEnvironmentVariable("PORT") ?? "8000"}");
app.Use(async (context, next) =>
{
    try
    {
        if (context.Request.Method is "POST" or "PUT" or "PATCH")
        {
            // Bound all mutating bodies, including chunked PUTs without a JSON model.
            await using var copy = new MemoryStream();
            var bytes = new byte[4096];
            int count;
            while ((count = await context.Request.Body.ReadAsync(bytes, context.RequestAborted)) != 0)
            {
                if (copy.Length + count > 16384)
                    throw new ApiError(413, "request body too large");
                await copy.WriteAsync(bytes.AsMemory(0, count), context.RequestAborted);
            }
            copy.Position = 0;
            context.Request.Body = copy;
            await next(context);
        }
        else
            await next(context);
        if (context.Response.StatusCode is 404 or 405 && !context.Response.HasStarted)
        {
            await context.Response.WriteAsJsonAsync(new
            {
                detail = context.Response.StatusCode == 404 ? "Not Found" : "Method Not Allowed"
            }, context.RequestAborted);
        }
    }
    catch (ApiError e) { context.Response.StatusCode = e.Status; if (e.Bearer) context.Response.Headers.WWWAuthenticate = "Bearer"; await context.Response.WriteAsJsonAsync(new { detail = e.Detail }, context.RequestAborted); }
    catch (OperationCanceledException) when (context.RequestAborted.IsCancellationRequested) { }
    catch (Exception e) { app.Logger.LogError("Request failed: {ExceptionType}", e.GetType().Name); if (!context.Response.HasStarted) { context.Response.StatusCode = 500; await context.Response.WriteAsJsonAsync(new { detail = "Internal Server Error" }); } }
});
new SocialApi(app.Services.GetRequiredService<Database>(), app.Services.GetRequiredService<PasswordService>(), app.Services.GetRequiredService<TokenService>(), ttl).Routes(app);
await app.StartAsync();
Settings.WriteTuningReceipt(Environment.GetEnvironmentVariable("RUN_DIR"), Environment.GetEnvironmentVariable("RUN_ID"), app.Services.GetRequiredService<PasswordService>().HashConcurrency, database.MaxPoolSize);
await app.WaitForShutdownAsync();
