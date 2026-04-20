using AccountService.Application.DTOs.Requests;
using FluentValidation;

namespace AccountService.Application.Validators;

public sealed class UpdateProfileRequestValidator : AbstractValidator<UpdateProfileRequest>
{
    public UpdateProfileRequestValidator()
    {
        RuleFor(x => x.Username)
            .MinimumLength(3)
            .MaximumLength(50)
            .Matches(@"^[a-zA-Z0-9_\-\.]+$")
            .WithMessage("Username may only contain letters, digits, underscores, hyphens, and dots.")
            .When(x => x.Username is not null);
    }
}
