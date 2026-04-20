namespace GatewayService.API.Common;

/// <summary>
/// Wraps the result of a downstream service call, supporting graceful partial/degraded responses.
/// </summary>
public sealed class ServiceResult<T>
{
    private ServiceResult(T? value, bool isSuccess, string? error)
    {
        Value = value;
        IsSuccess = isSuccess;
        Error = error;
    }

    public bool IsSuccess { get; }
    public T? Value { get; }
    public string? Error { get; }

    public static ServiceResult<T> Ok(T value) => new(value, true, null);
    public static ServiceResult<T> Fail(string error) => new(default, false, error);
}
