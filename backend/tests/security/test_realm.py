"""Static review of the local IdP realm. Login brute-force protection, PKCE, and token signing are
the IdP's job; these checks keep the committed realm from regressing. Production uses Entra ID,
where the equivalents are Smart Lockout, Conditional Access, and app registration settings."""

import json
import pathlib

import pytest

REALM = json.loads(
    (
        pathlib.Path(__file__).resolve().parents[3] / "keycloak" / "internal-tools-realm.json"
    ).read_text()
)
CLIENTS = {c["clientId"]: c for c in REALM["clients"]}


def test_brute_force_protection_enabled():
    assert REALM["bruteForceProtected"] is True
    assert 1 <= REALM["failureFactor"] <= 10
    assert REALM["waitIncrementSeconds"] >= 30
    assert REALM["maxFailureWaitSeconds"] >= 300


def test_self_service_account_creation_and_identity_changes_disabled():
    assert REALM["registrationAllowed"] is False
    assert REALM["editUsernameAllowed"] is False
    # The API's subject is the verified email: two accounts must never share one.
    assert REALM["duplicateEmailsAllowed"] is False


def test_password_policy_and_token_settings():
    policy = REALM["passwordPolicy"]
    assert "length(12)" in policy and "notUsername" in policy and "notEmail" in policy
    assert REALM["defaultSignatureAlgorithm"] == "RS256"
    assert REALM["accessTokenLifespan"] <= 900
    assert REALM["revokeRefreshToken"] is True and REALM["refreshTokenMaxReuse"] == 0
    assert REALM["eventsEnabled"] is True and "LOGIN_ERROR" in REALM["enabledEventTypes"]
    assert REALM["sslRequired"] in ("external", "all")


def test_spa_client_uses_auth_code_with_pkce_only():
    web = CLIENTS["internal-tools-web"]
    assert web["publicClient"] is True and web["standardFlowEnabled"] is True
    assert web["implicitFlowEnabled"] is False and web["directAccessGrantsEnabled"] is False
    assert web["attributes"]["pkce.code.challenge.method"] == "S256"
    assert "*" not in web["webOrigins"] and all(u != "*" for u in web["redirectUris"])


@pytest.mark.parametrize("client_id", sorted(CLIENTS))
def test_no_client_secrets_or_implicit_flow_in_repo(client_id):
    client = CLIENTS[client_id]
    assert "secret" not in client
    assert client["implicitFlowEnabled"] is False
    assert client["serviceAccountsEnabled"] is False


def test_password_grant_only_on_dev_cli_client():
    assert [c for c, v in CLIENTS.items() if v["directAccessGrantsEnabled"]] == [
        "internal-tools-dev-cli"
    ]


def test_access_tokens_carry_api_audience():
    for client in CLIENTS.values():
        mappers = {m["name"]: m for m in client["protocolMappers"]}
        audience = mappers["api-audience"]["config"]
        assert audience["included.custom.audience"] == "internal-tools-api"
        assert audience["id.token.claim"] == "false"
