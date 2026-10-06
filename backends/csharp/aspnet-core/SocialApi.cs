using Npgsql;
namespace BenchmarkApi;

public sealed class SocialApi(Database db, PasswordService passwords, TokenService tokens, int ttl)
{
    private const string PostSql = "SELECT p.id,p.content,p.created_at,p.author_id,u.username,(SELECT count(*) FROM likes l WHERE l.post_id=p.id) AS like_count,(SELECT count(*) FROM comments c WHERE c.post_id=p.id) AS comment_count,EXISTS(SELECT 1 FROM likes l WHERE l.post_id=p.id AND l.user_id=$1) AS liked FROM posts p JOIN users u ON u.id=p.author_id";
    private static Dictionary<string, object?> Item(Dictionary<string, object?> row)
    {
        row["author"] = new
        {
            id = (long)row["author_id"]!,
            username = (string)row["username"]!
        };
        row.Remove("author_id");
        row.Remove("username");
        row["created_at"] = Protocol.Stamp((DateTime)row["created_at"]!);
        return row;
    }
    private static object Page(List<Dictionary<string, object?>> rows, int limit)
    {
        bool more = rows.Count > limit;
        var selected = rows.Take(limit).ToList();
        string? cursor = more ? Protocol.EncodeCursor((DateTime)selected[^1]["created_at"]!, (long)selected[^1]["id"]!) : null;
        return new
        {
            items = selected.Select(Item),
            next_cursor = cursor
        };
    }
    private static Dictionary<string, object?> First(List<Dictionary<string, object?>> rows, int status = 404) => rows.Count == 0 ? throw new ApiError(status, status == 401 ? "invalid credentials" : "not found") : rows[0];
    private static async Task Exists(NpgsqlConnection c, NpgsqlTransaction? tx, string table, long id, CancellationToken token)
    {
        // Table identifiers are internal literals, never request input.
        First(await Database.Query(c, tx, "SELECT 1 FROM " + table + " WHERE id=$1", [id], token));
    }
    private async Task<long> Identity(HttpContext context)
    {
        string value = context.Request.Headers.Authorization.ToString();
        int split = value.IndexOf(' ');
        if (split < 0 || !value[..split].Equals("Bearer", StringComparison.OrdinalIgnoreCase))
            throw new ApiError(401, "invalid credentials", true);
        return await tokens.Identity(value[(split + 1)..]);
    }
    public void Routes(WebApplication app)
    {
        app.MapGet("/health/live", () => Results.Json(new { status = "ok" }));
        app.MapGet("/health/ready", async (CancellationToken ct) => { await db.Read("SELECT 1", [], ct); return Results.Json(new { status = "ok" }); });
        app.MapGet("/health/metrics", () => Results.Json(db.Metrics()));
        app.MapPost("/api/v1/auth/register", (Func<HttpContext, Task<IResult>>)Register);
        app.MapPost("/api/v1/auth/login", (Func<HttpContext, Task<IResult>>)Login);
        app.MapGet("/api/v1/users/me", (Func<HttpContext, Task<IResult>>)Me);
        app.MapPost("/api/v1/posts", (Func<HttpContext, Task<IResult>>)CreatePost);
        app.MapGet("/api/v1/posts/{post_id}", Detail);
        app.MapGet("/api/v1/feed", (Func<HttpContext, Task<IResult>>)Feed);
        app.MapMethods("/api/v1/users/{target_id}/follow", ["PUT", "DELETE"], Follow);
        app.MapMethods("/api/v1/posts/{post_id}/like", ["PUT", "DELETE"], Like);
        app.MapPost("/api/v1/posts/{post_id}/comments", CreateComment);
        app.MapGet("/api/v1/posts/{post_id}/comments", Comments);
    }
    private async Task<IResult> Register(HttpContext context)
    {
        var ct = context.RequestAborted;
        var body = await Protocol.Body(context.Request, "register");
        var hash = await passwords.Hash(body["password"], ct);
        try
        {
            return Results.Json(First(await db.Read("INSERT INTO users(username,password_hash) VALUES($1,$2) RETURNING id,username", [body["username"], hash], ct)), statusCode: 201);
        }
        catch (PostgresException e) when (e.SqlState == "23505") { throw new ApiError(409, "username already exists"); }
    }
    private async Task<IResult> Login(HttpContext context)
    {
        var ct = context.RequestAborted;
        var body = await Protocol.Body(context.Request, "login");
        var rows = await db.Read("SELECT id,password_hash FROM users WHERE username=$1", [body["username"]], ct);
        var row = rows.FirstOrDefault();
        // Read disposed its pooled connection before expensive password verification.
        bool valid = await passwords.Verify(body["password"], row?["password_hash"] as string, ct);
        if (!valid || row is null)
            throw new ApiError(401, "invalid credentials", true);
        return Results.Json(new
        {
            access_token = tokens.Issue((long)row["id"]!, DateTimeOffset.UtcNow.ToUnixTimeSeconds()),
            token_type = "bearer",
            expires_in = ttl
        });
    }
    private async Task<IResult> Me(HttpContext context)
    {
        var id = await Identity(context);
        return Results.Json(First(await db.Read("SELECT id,username FROM users WHERE id=$1", [id], context.RequestAborted), 401));
    }
    private async Task<IResult> CreatePost(HttpContext context)
    {
        var ct = context.RequestAborted;
        var viewer = await Identity(context);
        var body = await Protocol.Body(context.Request, "post");
        var result = await db.Transaction(async (c, tx) => { var id = First(await Database.Query(c, tx, "INSERT INTO posts(author_id,content) VALUES($1,$2) RETURNING id", [viewer, body["content"]], ct))["id"]; return Item(First(await Database.Query(c, tx, PostSql + " WHERE p.id=$2", [viewer, id], ct))); }, ct);
        return Results.Json(result, statusCode: 201);
    }
    private async Task<IResult> Detail(HttpContext context, string post_id)
    {
        var viewer = await Identity(context);
        long id = Protocol.PathId(post_id, "post_id");
        return Results.Json(Item(First(await db.Read(PostSql + " WHERE p.id=$2", [viewer, id], context.RequestAborted))));
    }
    private async Task<IResult> Feed(HttpContext context)
    {
        var viewer = await Identity(context);
        var page = Protocol.Page(context.Request);
        var rows = await db.Read(PostSql + " WHERE p.author_id IN(SELECT followed_id FROM follows WHERE follower_id=$1 UNION ALL SELECT CAST($1 AS bigint)) AND(CAST($2 AS timestamptz) IS NULL OR(p.created_at,p.id)<(CAST($2 AS timestamptz),$3)) ORDER BY p.created_at DESC,p.id DESC LIMIT $4", [viewer, page.Time, page.Id, page.Limit + 1], context.RequestAborted);
        return Results.Json(Page(rows, page.Limit));
    }
    private async Task<IResult> Follow(HttpContext context, string target_id)
    {
        var viewer = await Identity(context);
        long target = Protocol.PathId(target_id, "target_id");
        if (viewer == target)
            throw new ApiError(422, "cannot follow yourself");
        var ct = context.RequestAborted;
        await db.Transaction(async (c, tx) => { await Exists(c, tx, "users", target, ct); await Database.Query(c, tx, context.Request.Method == "PUT" ? "INSERT INTO follows(follower_id,followed_id) VALUES($1,$2) ON CONFLICT DO NOTHING" : "DELETE FROM follows WHERE follower_id=$1 AND followed_id=$2", [viewer, target], ct); return true; }, ct);
        return Results.NoContent();
    }
    private async Task<IResult> Like(HttpContext context, string post_id)
    {
        var viewer = await Identity(context);
        long post = Protocol.PathId(post_id, "post_id");
        var ct = context.RequestAborted;
        await db.Transaction(async (c, tx) => { await Exists(c, tx, "posts", post, ct); await Database.Query(c, tx, context.Request.Method == "PUT" ? "INSERT INTO likes(post_id,user_id) VALUES($1,$2) ON CONFLICT DO NOTHING" : "DELETE FROM likes WHERE post_id=$1 AND user_id=$2", [post, viewer], ct); return true; }, ct);
        return Results.NoContent();
    }
    private async Task<IResult> CreateComment(HttpContext context, string post_id)
    {
        var viewer = await Identity(context);
        long post = Protocol.PathId(post_id, "post_id");
        var ct = context.RequestAborted;
        var body = await Protocol.Body(context.Request, "comment");
        var result = await db.Transaction(async (c, tx) => { await Exists(c, tx, "posts", post, ct); return Item(First(await Database.Query(c, tx, "WITH inserted AS(INSERT INTO comments(post_id,author_id,content) VALUES($1,$2,$3) RETURNING *) SELECT c.id,c.content,c.created_at,c.author_id,u.username FROM inserted c JOIN users u ON u.id=c.author_id", [post, viewer, body["content"]], ct))); }, ct);
        return Results.Json(result, statusCode: 201);
    }
    private async Task<IResult> Comments(HttpContext context, string post_id)
    {
        await Identity(context);
        long post = Protocol.PathId(post_id, "post_id");
        var page = Protocol.Page(context.Request);
        var ct = context.RequestAborted;
        await using var c = await db.Open(ct);
        await Exists(c, null, "posts", post, ct);
        var rows = await Database.Query(c, null, "SELECT c.id,c.content,c.created_at,c.author_id,u.username FROM comments c JOIN users u ON u.id=c.author_id WHERE c.post_id=$1 AND(CAST($2 AS timestamptz) IS NULL OR(c.created_at,c.id)<(CAST($2 AS timestamptz),$3)) ORDER BY c.created_at DESC,c.id DESC LIMIT $4", [post, page.Time, page.Id, page.Limit + 1], ct);
        return Results.Json(Page(rows, page.Limit));
    }
}
