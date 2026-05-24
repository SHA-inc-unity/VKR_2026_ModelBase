using FluentValidation;
using SocialService.Application.DTOs.Requests;

namespace SocialService.Application.Validators;

public sealed class CreateCommentRequestValidator : AbstractValidator<CreateCommentRequest>
{
    public CreateCommentRequestValidator()
    {
        RuleFor(x => x.TargetType).NotEmpty().Must(v => v == "asset" || v == "news")
            .WithMessage("targetType must be 'asset' or 'news'");
        RuleFor(x => x.TargetId).NotEmpty().MaximumLength(128);
        RuleFor(x => x.Body).NotEmpty().MaximumLength(4000);
    }
}

public sealed class UpdateCommentRequestValidator : AbstractValidator<UpdateCommentRequest>
{
    public UpdateCommentRequestValidator()
    {
        RuleFor(x => x.Body).NotEmpty().MaximumLength(4000);
    }
}
