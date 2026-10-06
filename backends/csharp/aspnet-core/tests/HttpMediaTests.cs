using BenchmarkApi;
using System.Net;
using System.Text;
using System.Text.Json;
using Xunit;
public class HttpMediaTests
{
    public static bool HasApi => Environment.GetEnvironmentVariable("CSHARP_TEST_API_URL") is not null;
    [Fact(Skip = "Set CSHARP_TEST_API_URL for disposable local HTTP integration", SkipUnless = nameof(HasApi))]
    public async Task RegisterLoginAndAuthenticatedPostsRespectCanonicalMediaWithoutMutations()
    {
        using var client = new HttpClient { BaseAddress = new Uri(Environment.GetEnvironmentVariable("CSHARP_TEST_API_URL")!) };
        var name = "media_" + Guid.NewGuid().ToString("N")[..12];
        var credentials = JsonSerializer.Serialize(new
        {
            username = name,
            password = "correct horse battery staple"
        });
        foreach (var media in new string?[] { "text/plain", null })
        {
            using var content = new ByteArrayContent(Encoding.UTF8.GetBytes(credentials));
            if (media is not null)
                content.Headers.ContentType = new(media);
            var response = await client.PostAsync("/api/v1/auth/register", content, TestContext.Current.CancellationToken);
            Assert.Equal(HttpStatusCode.UnprocessableEntity, response.StatusCode);
            var expected = JsonSerializer.Serialize(new
            {
                detail = new[] { Protocol.Error("model_attributes_type", ["body"], "Input should be a valid dictionary or object to extract fields from", credentials) }
            });
            Assert.True(System.Text.Json.Nodes.JsonNode.DeepEquals(System.Text.Json.Nodes.JsonNode.Parse(expected), System.Text.Json.Nodes.JsonNode.Parse(await response.Content.ReadAsStringAsync(TestContext.Current.CancellationToken))));
        }
        // If either unsupported registration mutated the DB, the proper request conflicts.
        var register = await client.PostAsync("/api/v1/auth/register", new StringContent(credentials, Encoding.UTF8, "application/vnd.example+json"), TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.Created, register.StatusCode);
        foreach (var media in new string?[] { "text/plain", null })
        {
            using var content = new ByteArrayContent(Encoding.UTF8.GetBytes(credentials));
            if (media is not null)
                content.Headers.ContentType = new(media);
            Assert.Equal(HttpStatusCode.UnprocessableEntity, (await client.PostAsync("/api/v1/auth/login", content, TestContext.Current.CancellationToken)).StatusCode);
        }
        var login = await client.PostAsync("/api/v1/auth/login", new StringContent(credentials, Encoding.UTF8, "application/json"), TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.OK, login.StatusCode);
        using var token = JsonDocument.Parse(await login.Content.ReadAsStringAsync(TestContext.Current.CancellationToken));
        client.DefaultRequestHeaders.Authorization = new("Bearer", token.RootElement.GetProperty("access_token").GetString());
        foreach (var media in new string?[] { "text/plain", null })
        {
            using var content = new ByteArrayContent(Encoding.UTF8.GetBytes("{\"content\":\"must not become a post\"}"));
            if (media is not null)
                content.Headers.ContentType = new(media);
            Assert.Equal(HttpStatusCode.UnprocessableEntity, (await client.PostAsync("/api/v1/posts", content, TestContext.Current.CancellationToken)).StatusCode);
        }
        using var feed = JsonDocument.Parse(await (await client.GetAsync("/api/v1/feed", TestContext.Current.CancellationToken)).Content.ReadAsStringAsync(TestContext.Current.CancellationToken));
        Assert.Equal(0, feed.RootElement.GetProperty("items").GetArrayLength());
        Assert.Equal(HttpStatusCode.Created, (await client.PostAsync("/api/v1/posts", new StringContent("{\"content\":\"proper JSON\"}", Encoding.UTF8, "application/vnd.example+json"), TestContext.Current.CancellationToken)).StatusCode);
    }
}
