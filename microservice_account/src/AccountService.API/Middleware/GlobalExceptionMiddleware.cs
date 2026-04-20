using AccountService.Application.Common.Exceptions;
using Microsoft.AspNetCore.Mvc;

namespace AccountService.API.Middleware;

public sealed class GlobalExceptionMiddleware
{
    private readonly RequestDelegate _next;
    private readonly ILogger<GlobalExceptionMiddleware> _logger;

    public GlobalExceptionMiddleware(RequestDelegate next, ILogger<GlobalExceptionMiddleware> logger)
    {
        _next = next;
        _logger = logger;
    }

    public async Task InvokeAsync(HttpContext context)
    {
        try
        {
            await _next(context);
        }
        catch (Exception ex)
        {
            await HandleExceptionAsync(context, ex);
        }
    }

    private async Task HandleExceptionAsync(HttpContext context, Exception exception)
    {
        var (statusCode, title) = exception switch
        {
            UserNotFoundException => (StatusCodes.Status404NotFound, "Not Found"),
            InvalidCredentialsException => (StatusCodes.Status401Unauthorized, "Unauthorized"),
            EmailAlreadyExistsException => (StatusCodes.Status409Conflict, "Conflict"),
            UsernameAlreadyExistsException => (StatusCodes.Status409Conflict, "Conflict"),
            WeakPasswordException => (StatusCodes.Status422UnprocessableEntity, "Unprocessable Entity"),
            TokenException => (StatusCodes.Status401Unauthorized, "Unauthorized"),
            AccountException => (StatusCodes.Status400BadRequest, "Bad Request"),
            _ => (StatusCodes.Status500InternalServerError, "Internal Server Error")
        };

        if (statusCode >= 500)
            _logger.LogError(exception, "Unhandled exception: {Message}", exception.Message);
        else
            _logger.LogWarning("Handled exception [{StatusCode}]: {Message}", statusCode, exception.Message);

        var problem = new ProblemDetails
        {
            Status = statusCode,
            Title = title,
            Detail = exception.Message,
            Instance = context.Request.Path
        };

        context.Response.StatusCode = statusCode;
        context.Response.ContentType = "application/problem+json";
        await context.Response.WriteAsJsonAsync(problem);
    }
}
