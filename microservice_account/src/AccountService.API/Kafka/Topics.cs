namespace AccountService.API.Kafka;

public static class Topics
{
    public const string CmdAccountHealth  = "cmd.account.health";
    public const string CmdAccountGetUser = "cmd.account.get_user";

    public static readonly string[] AllConsumed =
    [
        CmdAccountHealth,
        CmdAccountGetUser,
    ];
}
