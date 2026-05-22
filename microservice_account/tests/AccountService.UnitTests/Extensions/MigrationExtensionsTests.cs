using System.Reflection;
using AccountService.Application.Common.Settings;
using AccountService.Application.Interfaces.Services;
using AccountService.Application.Services;
using AccountService.Domain.Entities;
using AccountService.Infrastructure.Data;
using FluentAssertions;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Xunit;

namespace AccountService.UnitTests.Extensions;

public sealed class MigrationExtensionsTests
{
    [Fact]
    public async Task EnsureBootstrapAdminAsync_CreatesAdminUser_WhenAdminUsernameIsMissing()
    {
        await using var db = CreateDbContext();
        await SeedAdminRoleAsync(db);

        await InvokeEnsureBootstrapAdminAsync(db);

        var user = await db.Users.SingleAsync(u => u.Username == "admin");
        user.Email.Should().Be("admin@modelline.local");
        CreatePasswordService().Verify("admin", user.PasswordHash).Should().BeTrue();
        (await db.UserRoles.AnyAsync(ur => ur.UserId == user.Id && ur.RoleId == 1)).Should().BeTrue();
    }

    [Fact]
    public async Task EnsureBootstrapAdminAsync_UpdatesExistingBootstrapUser_WhenAdminUsernameIsMissing()
    {
        await using var db = CreateDbContext();
        await SeedAdminRoleAsync(db);

        var existingUser = User.Create("admin@modelline.local", "legacy-admin", CreatePasswordService().Hash("OldPass1"));
        await db.Users.AddAsync(existingUser);
        await db.UserSettings.AddAsync(UserSettings.CreateDefault(existingUser.Id));
        await db.SaveChangesAsync();

        await InvokeEnsureBootstrapAdminAsync(db);

        var user = await db.Users.SingleAsync();
        user.Username.Should().Be("admin");
        (await db.UserRoles.AnyAsync(ur => ur.UserId == user.Id && ur.RoleId == 1)).Should().BeTrue();
    }

    private static async Task InvokeEnsureBootstrapAdminAsync(AccountDbContext db)
    {
        var method = typeof(AccountService.API.Extensions.MigrationExtensions)
            .GetMethod("EnsureBootstrapAdminAsync", BindingFlags.NonPublic | BindingFlags.Static)
            ?? throw new InvalidOperationException("EnsureBootstrapAdminAsync was not found.");

        var configuration = new ConfigurationBuilder().AddInMemoryCollection().Build();
        var passwordService = CreatePasswordService();
        var task = (Task?)method.Invoke(null, [configuration, db, passwordService, NullLogger.Instance])
            ?? throw new InvalidOperationException("EnsureBootstrapAdminAsync invocation did not return a task.");

        await task;
    }

    private static AccountDbContext CreateDbContext()
    {
        var options = new DbContextOptionsBuilder<AccountDbContext>()
            .UseInMemoryDatabase(Guid.NewGuid().ToString("N"))
            .Options;

        return new AccountDbContext(options);
    }

    private static IPasswordService CreatePasswordService() =>
        new PasswordService(Options.Create(new PasswordSettings
        {
            MinLength = 8,
            RequireUppercase = true,
            RequireLowercase = true,
            RequireDigit = true,
            RequireSpecialChar = false,
            WorkFactor = 4,
        }));

    private static async Task SeedAdminRoleAsync(AccountDbContext db)
    {
        await db.Roles.AddAsync(CreateRole(1, Role.Codes.Admin, "Admin"));
        await db.SaveChangesAsync();
    }

    private static Role CreateRole(int id, string code, string name)
    {
        var role = (Role?)Activator.CreateInstance(typeof(Role), nonPublic: true)
            ?? throw new InvalidOperationException("Failed to create role instance.");

        SetProperty(role, nameof(Role.Id), id);
        SetProperty(role, nameof(Role.Code), code);
        SetProperty(role, nameof(Role.Name), name);
        return role;
    }

    private static void SetProperty<T>(object target, string propertyName, T value)
    {
        var property = target.GetType().GetProperty(propertyName, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)
            ?? throw new InvalidOperationException($"Property {propertyName} was not found.");

        property.SetValue(target, value);
    }
}