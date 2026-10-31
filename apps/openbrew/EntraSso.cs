using System;
using System.Data.SqlClient;
using System.Globalization;
using System.IO;
using System.Security.Principal;
using System.Text;
using System.Web;
using System.Xml;

// OAuth2 Proxy validates Entra tokens. XSP only listens on loopback; this
// adapter also requires the proxy's private Basic credential on every request.
public sealed class EntraSso : IHttpModule
{
    static readonly string Email = Required("OPENBREW_ALLOWED_EMAIL");
    static readonly string ProxyPassword = Required("OPENBREW_PROXY_PASSWORD");
    static readonly string UserId = File.ReadAllText("/runtime/sso-user-id").Trim();

    static string Required(string name)
    {
        var value = Environment.GetEnvironmentVariable(name);
        if (String.IsNullOrWhiteSpace(value)) throw new InvalidOperationException(name + " is required");
        return value;
    }

    public static bool IsAllowed(string authorization, string email, string password, string allowedEmail)
    {
        if (String.IsNullOrEmpty(password) ||
            !String.Equals(email, allowedEmail, StringComparison.OrdinalIgnoreCase) ||
            authorization == null || !authorization.StartsWith("Basic ", StringComparison.OrdinalIgnoreCase))
            return false;
        try
        {
            var credential = Encoding.UTF8.GetString(Convert.FromBase64String(authorization.Substring(6)));
            var separator = credential.IndexOf(':');
            if (separator < 0) return false;
            var actual = Encoding.UTF8.GetBytes(credential.Substring(separator + 1));
            var expected = Encoding.UTF8.GetBytes(password);
            var difference = actual.Length ^ expected.Length;
            for (var i = 0; i < expected.Length; i++)
                difference |= expected[i] ^ (i < actual.Length ? actual[i] : 0);
            return difference == 0;
        }
        catch (FormatException) { return false; }
    }

    public void Init(HttpApplication app)
    {
        app.BeginRequest += delegate
        {
            var context = app.Context;
            // Used only by the container's loopback probe, never an auth bypass
            // in OAuth2 Proxy. Return only health, with no account or recipe data.
            if (context.Request.Path == "/healthz" && context.Request.IsLocal)
            {
                using (var connection = new SqlConnection(Required("OPENBREW_CONNECTION_STRING")))
                {
                    connection.Open();
                    using (var command = connection.CreateCommand())
                    {
                        command.CommandText = "SELECT COUNT(*) FROM dbo.[User] WHERE EmailAddress=@email AND IsActive=1";
                        command.Parameters.AddWithValue("@email", Email);
                        context.Response.StatusCode = Convert.ToInt32(command.ExecuteScalar()) == 1 ? 200 : 503;
                    }
                }
                context.Response.ContentType = "text/plain";
                context.Response.Write("health");
                app.CompleteRequest();
                return;
            }
            if (!context.Request.IsLocal || !IsAllowed(context.Request.Headers["Authorization"],
                    context.Request.Headers["X-Forwarded-Email"], ProxyPassword, Email))
            {
                context.Items["sso-denied"] = true;
                context.Response.StatusCode = 403;
                context.Response.SuppressFormsAuthenticationRedirect = true;
                context.Response.Write("Entra authentication required");
                app.CompleteRequest();
                return;
            }
            context.Response.Cache.SetCacheability(HttpCacheability.NoCache);
            context.Response.Cache.SetNoStore();
            var path = context.Request.Path.TrimEnd('/').ToLowerInvariant();
            if (path == "/logout" || path == "/auth/logout")
            {
                context.Response.Redirect("/oauth2/sign_out?rd=" + HttpUtility.UrlEncode(
                    "https://login.microsoftonline.com/345cd18b-5bee-4ac4-b729-a3f7059a28f3/oauth2/v2.0/logout" +
                    "?post_logout_redirect_uri=https://openbrew.cooked.beer/"), false);
                app.CompleteRequest();
            }
            else if (path == "/login" || path == "/loginviadialog" || path == "/signup" ||
                     path == "/signupviadialog" || path == "/sign-up" || path == "/changepassword" ||
                     path == "/googlelogin" || path == "/googleloginviadialog" ||
                     path == "/oauthlogin" || path == "/oauthloginviadialog" ||
                     path == "/reset-password" || path == "/set-password" ||
                     path.StartsWith("/verify-email/") || path.StartsWith("/auth/"))
            {
                if (context.Request.HttpMethod == "GET") context.Response.Redirect("/", false);
                else context.Response.StatusCode = 403;
                app.CompleteRequest();
            }
        };
        app.PostAuthenticateRequest += delegate
        {
            if (app.Context.Items["sso-denied"] == null)
            {
                var principal = new GenericPrincipal(new GenericIdentity(UserId, "Entra"), new string[0]);
                app.Context.User = principal;
                System.Threading.Thread.CurrentPrincipal = principal;
            }
        };
    }

    public void Dispose() { }
}

public static class ProvisionOpenBrew
{
    public static int Main()
    {
        var admin = new SqlConnectionStringBuilder {
            DataSource = "openbrew-db,1433", InitialCatalog = "master", UserID = "sa",
            Password = Environment.GetEnvironmentVariable("MSSQL_SA_PASSWORD"),
            Encrypt = false, TrustServerCertificate = true
        };
        var runtime = new SqlConnectionStringBuilder(Environment.GetEnvironmentVariable("OPENBREW_CONNECTION_STRING"));
        using (var connection = new SqlConnection(admin.ConnectionString))
        {
            connection.Open();
            using (var command = connection.CreateCommand())
            {
                command.CommandText = @"
IF NOT EXISTS (SELECT 1 FROM sys.sql_logins WHERE name=@login)
BEGIN
 DECLARE @sql nvarchar(max)=N'CREATE LOGIN '+QUOTENAME(@login)+N' WITH PASSWORD='+QUOTENAME(@password,'''')+N', CHECK_POLICY=ON';
 EXEC sys.sp_executesql @sql;
END";
                command.Parameters.AddWithValue("@login", runtime.UserID);
                command.Parameters.AddWithValue("@password", runtime.Password);
                command.ExecuteNonQuery();
            }
            connection.ChangeDatabase(runtime.InitialCatalog);
            using (var command = connection.CreateCommand())
            {
                command.CommandText = @"
IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name=N'openbrew')
 CREATE USER [openbrew] FOR LOGIN [openbrew];
ALTER ROLE db_datareader ADD MEMBER [openbrew];
ALTER ROLE db_datawriter ADD MEMBER [openbrew];
GRANT EXECUTE TO [openbrew];
IF NOT EXISTS (SELECT 1 FROM dbo.[User] WHERE EmailAddress=@email)
 INSERT dbo.[User] (Username,EmailAddress,Password,FirstName,HasCustomUsername,IsActive,DateCreated,IsEmailVerified,EmailVerifiedAt)
 VALUES ('sam',@email,CRYPT_GEN_RANDOM(64),'Sam',1,1,GETDATE(),1,GETDATE());
SELECT UserId FROM dbo.[User] WHERE EmailAddress=@email AND IsActive=1;";
                command.Parameters.AddWithValue("@email", Environment.GetEnvironmentVariable("OPENBREW_ALLOWED_EMAIL"));
                var id = command.ExecuteScalar();
                if (id == null || id == DBNull.Value) throw new InvalidOperationException("Active SSO account is missing");
                File.WriteAllText("/runtime/sso-user-id", Convert.ToString(id, CultureInfo.InvariantCulture));
            }
        }
        var config = new XmlDocument();
        config.Load("/runtime/Web.config");
        var web = config.SelectSingleNode("/configuration/system.web");
        var authentication = web.SelectSingleNode("authentication");
        authentication.RemoveAll();
        ((XmlElement)authentication).SetAttribute("mode", "None");
        var modules = web.SelectSingleNode("httpModules") ?? web.AppendChild(config.CreateElement("httpModules"));
        var module = config.CreateElement("add");
        module.SetAttribute("name", "EntraSso");
        module.SetAttribute("type", "EntraSso, HomelabEntraSso");
        modules.AppendChild(module);
        config.Save("/runtime/Web.config");
        Console.WriteLine("Entra-only account and runtime configuration ready.");
        return 0;
    }
}
