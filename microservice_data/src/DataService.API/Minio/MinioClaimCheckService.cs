using Amazon.Runtime;
using Amazon.S3;
using Amazon.S3.Model;
using Amazon.S3.Transfer;
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
    private readonly string _endpoint;
    private readonly ILogger<MinioClaimCheckService> _log;

    public MinioClaimCheckService(IOptions<DataServiceSettings> opts, ILogger<MinioClaimCheckService> log)
    {
        _log = log;
        var cfg = opts.Value.Minio;
        _bucket = cfg.Bucket;
        _endpoint = cfg.Endpoint;
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

    /// <summary>
    /// Stream an arbitrary-length <paramref name="input"/> into MinIO as
    /// <paramref name="key"/>. Uses <see cref="TransferUtility"/>, which
    /// performs a multipart upload as bytes arrive — no need to know the
    /// content length up-front, and no client-side buffering of the whole
    /// payload in memory. The stream is read sequentially; the caller is
    /// responsible for producing data into it (e.g. a pipe writer).
    /// </summary>
    public async Task PutStreamAsync(
        Stream input, string key, string contentType,
        CancellationToken ct = default)
    {
        await EnsureBucketAsync(ct);
        var req = new TransferUtilityUploadRequest
        {
            BucketName  = _bucket,
            Key         = key,
            InputStream = input,
            ContentType = contentType,
            AutoCloseStream = false,
            // AutoResetStreamPosition must be false — our input is a forward-only
            // pipe reader that doesn't support Seek.
            AutoResetStreamPosition = false,
            // 5 MB part size (MinIO min). TransferUtility automatically flips to
            // multipart once the stream exceeds this, which is what we want for
            // large CSVs; small payloads still stream as a single PUT.
            PartSize = 5 * 1024 * 1024,
        };
        using var util = new TransferUtility(_client);
        await util.UploadAsync(req, ct);
        _log.LogDebug("Streamed object to s3://{Bucket}/{Key}", _bucket, key);
    }

    /// <summary>
    /// Build a presigned GET URL for <paramref name="key"/> valid for
    /// <paramref name="expiresMinutes"/>. The AWS SDK signs the URL against
    /// the internal <c>ServiceURL</c> (e.g. <c>http://minio:9000</c>), which
    /// browsers can't resolve. We rewrite the host portion with
    /// <paramref name="publicBaseUrl"/> (e.g. <c>http://localhost:9000</c>)
    /// — the signature remains valid because MinIO, unlike AWS S3, does not
    /// bind the signature to the <c>Host</c> header when the signing vhost
    /// matches the request vhost. Additionally attaches a
    /// <c>response-content-disposition</c> override so the browser downloads
    /// the object with a <c>{key}</c>-based filename.
    /// </summary>
    public Task<string> GetPresignedUrlAsync(
        string key, string publicBaseUrl, int expiresMinutes,
        string? downloadFilename = null, string? contentType = null,
        CancellationToken ct = default)
    {
        var req = new GetPreSignedUrlRequest
        {
            BucketName = _bucket,
            Key        = key,
            Expires    = DateTime.UtcNow.AddMinutes(expiresMinutes),
            Verb       = HttpVerb.GET,
            Protocol   = publicBaseUrl.StartsWith("https", StringComparison.OrdinalIgnoreCase)
                ? Protocol.HTTPS
                : Protocol.HTTP,
        };
        if (!string.IsNullOrEmpty(downloadFilename))
        {
            req.ResponseHeaderOverrides.ContentDisposition =
                $"attachment; filename=\"{downloadFilename}\"";
        }
        if (!string.IsNullOrEmpty(contentType))
        {
            req.ResponseHeaderOverrides.ContentType = contentType;
        }

        var signed = _client.GetPreSignedURL(req);

        // Rewrite internal host ("http://minio:9000") → publicBaseUrl
        // ("http://localhost:9000"). AWSSDK signs against the ServiceURL,
        // which is the Docker-internal hostname — not reachable from a
        // browser. Replace only the scheme+authority prefix; the path and
        // query (including the SigV4 signature) are preserved verbatim.
        var internalPrefix = _endpoint.TrimEnd('/');
        var publicPrefix   = publicBaseUrl.TrimEnd('/');
        if (!string.IsNullOrEmpty(internalPrefix)
            && !string.IsNullOrEmpty(publicPrefix)
            && signed.StartsWith(internalPrefix, StringComparison.OrdinalIgnoreCase))
        {
            signed = publicPrefix + signed[internalPrefix.Length..];
        }
        return Task.FromResult(signed);
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
