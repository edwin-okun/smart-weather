import argparse
import asyncio
import sys

from app.db import close_db, init_db
from app.permissions import ALL_SCOPES, WEATHER_HISTORY_READ, WEATHER_READ
from app.services.auth import (
    add_api_client_redirect_uri,
    disable_api_client,
    list_registered_api_clients,
    register_api_client,
    rotate_api_client_secret,
)


DEFAULT_SCOPES = [WEATHER_READ, WEATHER_HISTORY_READ]


async def _run_with_db(action, *args, **kwargs):
    await init_db()
    try:
        return await action(*args, **kwargs)
    finally:
        await close_db()


async def _create_client(args: argparse.Namespace) -> None:
    client = await _run_with_db(register_api_client, name=args.name, scopes=args.scope)
    print(f"client_id={client.client_id}")
    print(f"client_secret={client.client_secret}")
    print(f"name={client.name}")
    print(f"scopes={' '.join(client.scopes)}")
    print("Store the client_secret now; it is not stored in plaintext.")


async def _rotate_secret(args: argparse.Namespace) -> None:
    client = await _run_with_db(rotate_api_client_secret, client_id=args.client_id)
    print(f"client_id={client.client_id}")
    print(f"client_secret={client.client_secret}")
    print("Previous secrets and active access tokens for this client were revoked.")


async def _disable_client(args: argparse.Namespace) -> None:
    revoked_count = await _run_with_db(disable_api_client, client_id=args.client_id)
    print(f"client_id={args.client_id}")
    print("status=disabled")
    print(f"revoked_tokens={revoked_count}")


async def _list_clients(_: argparse.Namespace) -> None:
    clients = await _run_with_db(list_registered_api_clients)
    for client in clients:
        print(
            "\t".join(
                [
                    str(client["client_id"]),
                    str(client["status"]),
                    str(client["name"]),
                    " ".join(client["scopes"]),
                    ",".join(client["redirect_uris"]),
                    str(client["last_used_at"] or ""),
                ]
            )
        )


async def _add_redirect_uri(args: argparse.Namespace) -> None:
    """Register an OAuth redirect URI for an existing API client."""
    redirect_uris = await _run_with_db(
        add_api_client_redirect_uri,
        client_id=args.client_id,
        redirect_uri=args.redirect_uri,
    )
    print(f"client_id={args.client_id}")
    print(f"redirect_uris={','.join(redirect_uris)}")


def main() -> None:
    """Run smart-weather administration commands."""
    parser = argparse.ArgumentParser(description="smart-weather administration")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_client = subparsers.add_parser(
        "create-client",
        help="Create an API client and print its one-time client secret",
    )
    create_client.add_argument("--name", required=True)
    create_client.add_argument(
        "--scope",
        action="append",
        choices=sorted(ALL_SCOPES),
        default=[],
        help="Allowed scope. Can be passed more than once.",
    )

    rotate_secret = subparsers.add_parser(
        "rotate-secret",
        help="Rotate an API client's secret and revoke its active tokens",
    )
    rotate_secret.add_argument("--client-id", required=True)

    disable_client = subparsers.add_parser(
        "disable-client",
        help="Disable an API client and revoke its active tokens",
    )
    disable_client.add_argument("--client-id", required=True)

    add_redirect_uri = subparsers.add_parser(
        "add-redirect-uri",
        help="Allow an OAuth authorization-code redirect URI for a client",
    )
    add_redirect_uri.add_argument("--client-id", required=True)
    add_redirect_uri.add_argument("--redirect-uri", required=True)

    subparsers.add_parser("list-clients", help="List API clients without secrets")

    args = parser.parse_args()
    try:
        if args.command == "create-client":
            if not args.scope:
                args.scope = DEFAULT_SCOPES
            asyncio.run(_create_client(args))
        elif args.command == "rotate-secret":
            asyncio.run(_rotate_secret(args))
        elif args.command == "disable-client":
            asyncio.run(_disable_client(args))
        elif args.command == "add-redirect-uri":
            asyncio.run(_add_redirect_uri(args))
        elif args.command == "list-clients":
            asyncio.run(_list_clients(args))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
