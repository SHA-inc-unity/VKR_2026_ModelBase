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
        // Pool budget: _heavyConcurrency(4) × ~5 parallel detector connections ≈ 20 heavy
        // + a few light handlers → 25 is the safe ceiling for this service's concurrency model.
        $"Host={Host};Port={Port};Database={Database};Username={User};Password={Password};Pooling=true;MinPoolSize=1;MaxPoolSize=25;CommandTimeout=60";
}

public sealed class KafkaSettings
{
    public string BootstrapServers { get; set; } = "redpanda:29092";
}

public sealed class MinioSettings
{
    // Internal S3 endpoint inside the Docker network — used as the SDK
    // ServiceURL (signing) и для server-to-server presigned URL'ов
    // (например, ответ `cmd.data.dataset.export_full`, который потребляет
    // microservice_analitic из той же сети).
    public string Endpoint { get; set; } = "http://minio:9000";

    // Browser-facing origin для signed download path /modelline-blobs/*.
    // Это **тот же внешний вход**, на котором живёт admin-панель: nginx
    // в microservice_infra публикуется на host-порте 8501 и проксирует
    // /modelline-blobs/* → http://minio:9000, поэтому presigned URL,
    // выданный браузеру, ходит через тот же origin, что и UI.
    // Перекрывается env-переменной PUBLIC_DOWNLOAD_BASE_URL.
    public string PublicDownloadBaseUrl { get; set; } = "http://localhost:8501";

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
