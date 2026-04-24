using Amazon.Runtime;
using Amazon.S3;
using Amazon.S3.Model;
using DataService.API.Settings;
using Microsoft.Extensions.Options;

namespace DataService.API.Minio;

/// <summary>
/// Stores and retrieves large payloads (claim-check pattern) using MinIO / S3-compatible storage.
/// </summary>
public sealed class MinioClaimCheckService : IDisposable
{
    private readonly AmazonS3Client _client;
    private readonly string _bucket;
    private readonly ILogger<MinioClaimCheckService> _log;

    public MinioClaimCheckService(IOptions<DataServiceSettings> opts, ILogger<MinioClaimCheckService> log)
    {
        _log = log;
        var cfg = opts.Value.Minio;
        _bucket = cfg.Bucket;
        var creds = new BasicAWSCredentials(cfg.AccessKey, cfg.SecretKey);
        _client = new AmazonS3Client(creds, new AmazonS3Config
        {
            ServiceURL    = cfg.Endpoint,
            ForcePathStyle = true,
        });
    }

    /// <summary>
    /// Upload bytes to MinIO and return a claim-check descriptor.
    /// </summary>
    public async Task<Dictionary<string, object>> PutBytesAsync(
        byte[] data, string? key = null, string contentType = "application/octet-stream",
        CancellationToken ct = default)
    {
        key ??= $"{Guid.NewGuid():N}";
        await EnsureBucketAsync(ct);
        using var ms = new MemoryStream(data);
        var req = new PutObjectRequest
        {
            BucketName  = _bucket,
            Key         = key,
            InputStream = ms,
            ContentType = contentType,
        };
        await _client.PutObjectAsync(req, ct);
        _log.LogDebug("Stored {Bytes} bytes at s3://{Bucket}/{Key}", data.Length, _bucket, key);
        return new Dictionary<string, object>
        {
            ["url"]    = $"s3://{_bucket}/{key}",
            ["key"]    = key,
            ["bucket"] = _bucket,
            ["size"]   = data.Length,
        };
    }

    /// <summary>Download bytes from MinIO by key.</summary>
    public async Task<byte[]> GetBytesAsync(string key, CancellationToken ct = default)
    {
        var req = new GetObjectRequest { BucketName = _bucket, Key = key };
        using var resp = await _client.GetObjectAsync(req, ct);
        using var ms = new MemoryStream();
        await resp.ResponseStream.CopyToAsync(ms, ct);
        return ms.ToArray();
    }

    private async Task EnsureBucketAsync(CancellationToken ct)
    {
        try
        {
            await _client.PutBucketAsync(
                new PutBucketRequest { BucketName = _bucket, UseClientRegion = true }, ct);
        }
        catch (AmazonS3Exception ex) when (ex.ErrorCode is "BucketAlreadyExists" or "BucketAlreadyOwnedByYou")
        {
            // Already exists — fine
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "EnsureBucket failed for {Bucket}, continuing", _bucket);
        }
    }

    public void Dispose() => _client.Dispose();
}
