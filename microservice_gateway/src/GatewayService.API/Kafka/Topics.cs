namespace GatewayService.API.Kafka;

/// <summary>
/// Topic constants consumed by microservice_account — kept in sync with
/// AccountService.API/Kafka/Topics.cs.
/// </summary>
public static class Topics
{
    public const string CmdAccountHealth  = "cmd.account.health";
    public const string CmdAccountGetUser = "cmd.account.get_user";
}
