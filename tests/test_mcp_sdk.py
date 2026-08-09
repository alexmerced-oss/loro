from types import SimpleNamespace

import pytest

from loro.config import MCPConfig, MCPCredentialProfileConfig, MCPServerConfig
from loro.mcp.client import MCPService, _MemoryTokenStorage
from loro.mcp.extensions import TASKS_EXTENSION_ID
from loro.mcp.tasks import _sdk_types, create_sdk_tasks_extension

mcp = pytest.importorskip("mcp")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expected_version", "expected_lifecycle"),
    [("auto", "2026-07-28", "stateless"), ("legacy", "2025-11-25", "classic")],
)
async def test_official_sdk_in_process_interoperability(
    mode: str, expected_version: str, expected_lifecycle: str
) -> None:
    from mcp import Client
    from mcp.server import MCPServer

    server = MCPServer("loro-test", version="1.0")

    @server.tool()
    async def echo(value: str) -> str:
        return value

    config = MCPConfig(
        enabled=True,
        servers={
            "fixture": MCPServerConfig(
                command="in-process",
                protocol_mode=mode,
                allowed_protocol_versions=["2026-07-28", "2025-11-25", "2024-11-05"],
            )
        },
    )
    service = MCPService(
        config,
        client_factory=lambda server_config: Client(server, mode=server_config.protocol_mode),
    )

    result = await service.call_tool("fixture", "echo", {"value": "hello"})

    assert result["connection"]["protocol_version"] == expected_version
    assert result["connection"]["lifecycle"] == expected_lifecycle
    assert result["result"]["isError"] is False


def test_official_sdk_oauth_profiles_use_supported_providers() -> None:
    from mcp.client.auth import OAuthClientProvider
    from mcp.client.auth.extensions.client_credentials import ClientCredentialsOAuthProvider

    authorization = MCPCredentialProfileConfig(
        type="oauth_authorization_code",
        client_metadata_url="https://agents.example/loro/client-metadata.json",
    )
    service = MCPService(MCPConfig())
    provider = service._oauth_provider("https://mcp.example/mcp", authorization, {})
    assert isinstance(provider, OAuthClientProvider)
    assert provider.context.client_metadata_url == authorization.client_metadata_url

    workload = MCPCredentialProfileConfig(
        type="oauth_client_credentials",
        client_id_env="MCP_CLIENT_ID",
        client_secret_env="MCP_CLIENT_SECRET",
    )
    provider = service._oauth_provider(
        "https://mcp.example/mcp",
        workload,
        {"MCP_CLIENT_ID": "client", "MCP_CLIENT_SECRET": "secret"},
    )
    assert isinstance(provider, ClientCredentialsOAuthProvider)


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol_version", ["2025-11-25", "2026-07-28"])
async def test_official_sdk_callbacks_fail_closed_without_terminal(
    protocol_version: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = MCPService(MCPConfig(allow_input_required=True))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    result = await service._elicitation_callback(
        SimpleNamespace(session=SimpleNamespace(protocol_version=protocol_version)),
        SimpleNamespace(message="untrusted request"),
    )
    assert result.code == -32600
    if protocol_version == "2025-11-25":
        assert "Classic" in result.message
    else:
        assert "interactive terminal" in result.message


@pytest.mark.asyncio
async def test_oauth_token_storage_is_connection_local() -> None:
    storage = _MemoryTokenStorage()
    await storage.set_tokens("tokens")
    await storage.set_client_info("client")
    assert await storage.get_tokens() == "tokens"
    assert await storage.get_client_info() == "client"


def test_official_sdk_rejects_oauth_issuer_confusion() -> None:
    from mcp.client.auth import OAuthFlowError
    from mcp.client.auth.utils import validate_metadata_issuer
    from mcp.shared.auth import OAuthMetadata

    metadata = OAuthMetadata(
        issuer="https://attacker.example",
        authorization_endpoint="https://attacker.example/authorize",
        token_endpoint="https://attacker.example/token",
    )
    with pytest.raises(OAuthFlowError, match="issuer mismatch"):
        validate_metadata_issuer(metadata, "https://identity.example")


def test_tasks_extension_uses_namespaced_identity_and_modern_claim() -> None:
    extension = create_sdk_tasks_extension(lambda _task, _name: None, {"notifications": False})
    claims = extension.claims()

    assert extension.identifier == TASKS_EXTENSION_ID
    assert extension.settings() == {"notifications": False}
    assert len(claims) == 1
    assert claims[0].result_type == "task"
    assert claims[0].protocol_versions == frozenset({"2026-07-28"})


def test_tasks_sdk_requests_serialize_task_routing_aliases() -> None:
    sdk = _sdk_types()
    request = sdk["UpdateTaskRequest"](
        params=sdk["UpdateTaskParams"](task_id="task-1", input_responses={"format": "pptx"})
    )

    payload = request.model_dump(by_alias=True, mode="json", exclude_none=True)
    assert request.name_param == "taskId"
    assert payload == {
        "method": "tasks/update",
        "params": {
            "taskId": "task-1",
            "inputResponses": {"format": "pptx"},
        },
    }


def test_tasks_claim_preserves_reserved_input_requests_as_extension_data() -> None:
    model = _sdk_types()["CreateTaskResult"].model_validate(
        {
            "resultType": "task",
            "taskId": "task-1",
            "status": "input_required",
            "createdAt": "2026-08-09T00:00:00Z",
            "lastUpdatedAt": "2026-08-09T00:00:01Z",
            "ttlMs": 60_000,
            "inputRequests": {"format": {}},
        }
    )

    assert model.model_dump(by_alias=True)["inputRequests"] == {"format": {}}
