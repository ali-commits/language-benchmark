using BenchmarkApi;
using Microsoft.AspNetCore.Http;
using System.Text;
using Xunit;
public class MediaTypeTests
{
    [Theory]
    [InlineData("register", "{\"username\":\"media_user\",\"password\":\"correct horse battery staple\"}")]
    [InlineData("login", "{\"username\":\"media_user\",\"password\":\"correct horse battery staple\"}")]
    [InlineData("post", "{\"content\":\"unsupported media content\"}")]
    [InlineData("post", "{\"content\":\"unsupported media content\"}", null)]
    public async Task PlainTextJsonHasCanonical422BodyAndNoAcceptedModel(string kind, string body, string? media = "text/plain")
    {
        var context = new DefaultHttpContext();
        context.Request.ContentType = media;
        context.Request.Body = new MemoryStream(Encoding.UTF8.GetBytes(body));
        var error = await Assert.ThrowsAsync<ApiError>(() => Protocol.Body(context.Request, kind));
        Assert.Equal(422, error.Status);
        Assert.Equal(System.Text.Json.JsonSerializer.Serialize(new[] { Protocol.Error("model_attributes_type", ["body"], "Input should be a valid dictionary or object to extract fields from", body) }), System.Text.Json.JsonSerializer.Serialize(error.Detail));
    }
    [Theory]
    [InlineData("application/json")]
    [InlineData("application/vnd.example+json")]
    public async Task SupportedJsonTypesKeepStringContent(string? media)
    {
        var context = new DefaultHttpContext();
        context.Request.ContentType = media;
        context.Request.Body = new MemoryStream(Encoding.UTF8.GetBytes("{\"content\":\"typed media content\"}"));
        Assert.Equal("typed media content", (await Protocol.Body(context.Request, "post"))["content"]);
    }
}
