namespace AccountService.Domain.Enums;

public enum AuditEventType
{
    Register = 1,
    LoginSuccess = 2,
    LoginFailed = 3,
    Logout = 4,
    TokenRefresh = 5,
    PasswordChange = 6,
    ProfileUpdate = 7,
    AccountDeactivated = 8
}
