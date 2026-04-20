using AccountService.Application.DTOs.Requests;
using AccountService.Application.Validators;
using FluentAssertions;
using FluentValidation.TestHelper;
using Xunit;

namespace AccountService.UnitTests.Validators;

public sealed class RegisterRequestValidatorTests
{
    private readonly RegisterRequestValidator _validator = new();

    [Theory]
    [InlineData("user@example.com", "username", "Password1")]
    [InlineData("a@b.co", "usr", "Password1")]
    public void Valid_Request_HasNoErrors(string email, string username, string password)
    {
        var request = new RegisterRequest(email, username, password);
        var result = _validator.TestValidate(request);
        result.ShouldNotHaveAnyValidationErrors();
    }

    [Theory]
    [InlineData("", "username", "Password1")]            // empty email
    [InlineData("notanemail", "username", "Password1")]  // invalid email
    [InlineData("a@b.com", "ab", "Password1")]           // username too short
    [InlineData("a@b.com", "username", "short")]         // password too short
    public void Invalid_Request_HasErrors(string email, string username, string password)
    {
        var request = new RegisterRequest(email, username, password);
        var result = _validator.TestValidate(request);
        result.ShouldHaveAnyValidationError();
    }
}
