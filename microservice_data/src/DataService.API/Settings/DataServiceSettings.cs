namespace DataService.API.Settings;

/// <summary>
/// Strongly-typed configuration bound from "DataService" section in appsettings.json.
/// Environment variables override: DataService__Postgres__Host etc.
/// </summary>
public sealed class DataServiceSettings
{
    public PostgresSettings Postgres { get; set; } = new();
    public KafkaSettings Kafka { get; set; } = new();
    public MinioSettings Minio { get; set; } = new();
    public ApiSettings Api { get; set; } = new();

    public string ServiceName => "microservice_data";
    public string Version => "1.0.0";
}

public sealed class PostgresSettings
{
    public string Host { get; set; } = "postgres";
    public int Port { get; set; } = 5432;
    public string Database { get; set; } = "crypt_date";
    public string User { get; set; } = "postgres";
    public string Password { get; set; } = "postgres";

    public string ConnectionString =>
        $"Host={Host};Port={Port};Database={Database};Username={User};Password={Password};Pooling=true;MinPoolSize=1;MaxPoolSize=10;CommandTimeout=60";
}

public sealed class KafkaSettings
{
    public string BootstrapServers { get; set; } = "redpanda:29092";
}

public sealed class MinioSettings
{
    public string Endpoint { get; set; } = "http://minio:9000";
    // Public hostname that browsers use to reach MinIO (presigned URL host rewrite).
    // Internal Endpoint lives on the Docker network ("http://minio:9000"); browsers
    // can't resolve that, so presigned URLs are rewritten to this base before being
    // returned to the client.
    public string PublicUrl { get; set; } = "http://localhost:9000";
    public string AccessKey { get; set; } = "modelline";
    public string SecretKey { get; set; } = "modelline_secret";
    public string Bucket { get; set; } = "modelline-blobs";
    public string Region { get; set; } = "us-east-1";
}

public sealed class ApiSettings
{
    public string Host { get; set; } = "0.0.0.0";
    public int Port { get; set; } = 8100;
}
