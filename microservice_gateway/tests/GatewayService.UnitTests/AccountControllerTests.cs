using System.Text.Json;
using FluentAssertions;
using GatewayService.API.Clients.Account;
using GatewayService.API.Clients.Account.Dtos;
using GatewayService.API.Common;
using GatewayService.API.Controllers;
using GatewayService.API.DTOs;
using GatewayService.API.Middleware;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using Xunit;

namespace GatewayService.UnitTests;

public sealed class AccountControllerTests
{
    [Fact]
    public async Task Login_returns_unified_error_envelope_when_auth_proxy_returns_error_status()
    {
        var controller = CreateController(
            accountClient: new StubAccountServiceClient(ServiceResult<AccountUserDto>.Fail("unused")),
            authProxyClient: new StubAccountAuthProxyClient(
                new AccountProxyResult(401, "{\"message\":\"bad credentials\"}", "application/json")),
            correlationId: "corr-auth");

        using var document = JsonDocument.Parse("{\"email\":\"test@example.com\",\"password\":\"wrong\"}");
        var result = await controller.Login(document.RootElement.Clone(), CancellationToken.None);

        var objectResult = result.Should().BeOfType<ObjectResult>().Subject;
        objectResult.StatusCode.Should().Be(StatusCodes.Status401Unauthorized);
        objectResult.Value.Should().BeEquivalentTo(new ErrorResponse
        {
            Status = 401,
            Title = "Unauthorized",
            Code = "account_proxy_error",
            Detail = "bad credentials",
            CorrelationId = "corr-auth",
        }, options => options.Excluding(item => item.Timestamp));
    }

    [Fact]
    public async Task Me_returns_unified_error_envelope_when_account_service_fails()
    {
        var controller = CreateController(
            accountClient: new StubAccountServiceClient(ServiceResult<AccountUserDto>.Fail("Account service timeout")),
            authProxyClient: new StubAccountAuthProxyClient(
                new AccountProxyResult(200, "{}", "application/json")),
            correlationId: "corr-me");

        controller.ControllerContext.HttpContext.Request.Headers.Authorization = "Bearer test-token";

        var result = await controller.Me(CancellationToken.None);

        var objectResult = result.Should().BeOfType<ObjectResult>().Subject;
        objectResult.StatusCode.Should().Be(StatusCodes.Status503ServiceUnavailable);
        objectResult.Value.Should().BeEquivalentTo(new ErrorResponse
        {
            Status = 503,
            Title = "Service Unavailable",
            Code = "account_profile_unavailable",
            Detail = "Account service timeout",
            CorrelationId = "corr-me",
        }, options => options.Excluding(item => item.Timestamp));
    }

    private static AccountController CreateController(
        IAccountServiceClient accountClient,
        IAccountAuthProxyClient authProxyClient,
        string correlationId)
    {
        var controller = new AccountController(accountClient, authProxyClient)
        {
            ControllerContext = new ControllerContext
            {
                HttpContext = new DefaultHttpContext()
            }
        };

        controller.ControllerContext.HttpContext.Items[CorrelationIdMiddleware.ItemsKey] = correlationId;
        return controller;
    }

    private sealed class StubAccountServiceClient : IAccountServiceClient
    {
        private readonly ServiceResult<AccountUserDto> _result;

        public StubAccountServiceClient(ServiceResult<AccountUserDto> result)
        {
            _result = result;
        }

        public Task<ServiceResult<AccountUserDto>> GetCurrentUserAsync(string bearerToken, CancellationToken ct = default)
            => Task.FromResult(_result);
    }

    private sealed class StubAccountAuthProxyClient : IAccountAuthProxyClient
    {
        private readonly AccountProxyResult _result;

        public StubAccountAuthProxyClient(AccountProxyResult result)
        {
            _result = result;
        }

        public Task<AccountProxyResult> ForwardAsync(
            HttpMethod method,
            string path,
            JsonElement? body = null,
            string? bearerToken = null,
            CancellationToken ct = default)
            => Task.FromResult(_result);
    }
}