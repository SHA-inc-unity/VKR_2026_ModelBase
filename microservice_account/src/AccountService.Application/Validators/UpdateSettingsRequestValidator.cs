using AccountService.Application.DTOs.Requests;
using FluentValidation;

namespace AccountService.Application.Validators;

public sealed class UpdateSettingsRequestValidator : AbstractValidator<UpdateSettingsRequest>
{
    private static readonly string[] AllowedThemes = ["light", "dark", "system"];
    private static readonly string[] AllowedLocales = ["en", "ru", "de", "fr", "es", "zh"];

    public UpdateSettingsRequestValidator()
    {
        RuleFor(x => x.Theme)
            .Must(t => AllowedThemes.Contains(t, StringComparer.OrdinalIgnoreCase))
            .WithMessage($"Theme must be one of: {string.Join(", ", AllowedThemes)}")
            .When(x => x.Theme is not null);

        RuleFor(x => x.Locale)
            .Must(l => AllowedLocales.Contains(l, StringComparer.OrdinalIgnoreCase))
            .WithMessage($"Locale must be one of: {string.Join(", ", AllowedLocales)}")
            .When(x => x.Locale is not null);
    }
}
