"""Run with uv run python -m app.order_flow_export.example_client ASTS NBIS."""

import argparse
import asyncio
import json
import logging
import os
from getpass import getpass
from typing import Any

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus


def read_token() -> str:
    token = os.environ.get("MARKETBOT_ORDER_FLOW_WS_TOKEN", "")
    if not token.strip():
        token = getpass("Token del WebSocket (entrada oculta): ")
    if not token.strip():
        raise ValueError("Es necesario ingresar el token del servicio.")
    return token


def format_message(data: dict[str, Any], *, raw_json: bool = False) -> str:
    if raw_json:
        return json.dumps(data, ensure_ascii=False)
    if data.get("type") == "subscribed":
        return (f"Suscripto: {', '.join(data['symbols'])}. "
                "Esperando novedades en vivo (sin historico). Ctrl+C para salir.")
    payload: dict[str, Any] = data.get("payload") or {}
    prefix = f"{payload.get('occurred_at', '-')} | {payload.get('symbol', '-')}"
    if data.get("event_type") == "order-flow.state.transitioned":
        return (f"{prefix} | CAMBIO {payload.get('previous_state', '-')} -> "
                f"{payload.get('state', '-')} | precio={payload.get('current_price', '-')} "
                f"| confianza={payload.get('confidence', '-')}")
    if data.get("event_type") != "order-flow.state.assessed":
        return json.dumps(data, ensure_ascii=False)
    quote = "fresca" if payload.get("quote_fresh") else "sin cotizacion fresca"
    lines = [
        f"{prefix} | {payload.get('state', '-')} | precio={payload.get('current_price', '-')} "
        f"| CVD={payload.get('cumulative_delta', '-')} "
        f"| confianza={payload.get('confidence', '-')} | quote={quote}",
        f"  pulso={payload.get('pulse_state')} | candidato={payload.get('candidate_state')} "
        f"| muestras={payload.get('candidate_samples', 0)}",
    ]
    windows: list[dict[str, Any]] = payload.get("windows", [])
    for window in windows:
        lines.append(
            f"  {window['window_seconds']}s: buy={window['buy_volume']} "
            f"sell={window['sell_volume']} delta={window['delta']}"
        )
    return "\n".join(lines)


async def listen(
    url: str, symbols: list[str], token: str, *, raw_json: bool = False, once: bool = False,
) -> None:
    # Keep Authorization out of the library's optional DEBUG handshake output.
    logger = logging.Logger("order_flow_example.transport", level=logging.INFO)
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    while True:
        try:
            print("Conectando al WebSocket...", flush=True)
            async with connect(
                url, additional_headers={"Authorization": f"Bearer {token}"},
                ping_interval=20, ping_timeout=20, logger=logger,
            ) as socket:
                print("Conectado y autenticado. Solicitando suscripcion...", flush=True)
                await socket.send(json.dumps({"action": "subscribe", "symbols": symbols}))
                async for message in socket:
                    data = json.loads(message)
                    if data.get("type") == "error":
                        raise ValueError("Suscripcion rechazada; revisa los tickers habilitados.")
                    print(format_message(data, raw_json=raw_json), flush=True)
                    if once and data.get("event_type") in (
                        "order-flow.state.assessed", "order-flow.state.transitioned",
                    ):
                        return
        except InvalidStatus as error:
            if error.response.status_code in (401, 403, 404):
                raise ValueError("Revisa la URL y el token de acceso.") from None
            print("Servicio no disponible. Reintentando en 2 segundos.", flush=True)
        except (ConnectionClosed, OSError, TimeoutError):
            print("Conexion interrumpida. Reintentando en 2 segundos; sin historico.", flush=True)
        await asyncio.sleep(2)


def main() -> None:
    parser = argparse.ArgumentParser(description="Listen to live MarketBot Order Flow events.")
    parser.add_argument("symbols", nargs="*", help="Tickers habilitados; se solicitan si se omiten")
    parser.add_argument("--url", default="ws://127.0.0.1:8766/ws/order-flow")
    parser.add_argument("--json", action="store_true", help="Mostrar el JSON completo")
    parser.add_argument("--once", action="store_true", help="Salir despues del primer evento")
    args = parser.parse_args()
    try:
        symbols = args.symbols or input("Tickers separados por espacios: ").split()
        symbols = list(dict.fromkeys(
            symbol.strip().upper() for symbol in symbols if symbol.strip()
        ))
        if not symbols:
            parser.error("Indica al menos un ticker habilitado.")
        token = read_token()
        asyncio.run(listen(args.url, symbols, token, raw_json=args.json, once=args.once))
    except ValueError as error:
        parser.exit(1, f"{error}\n")
    except KeyboardInterrupt:
        pass
    except EOFError:
        parser.exit(1, "Indica los tickers y configura MARKETBOT_ORDER_FLOW_WS_TOKEN.\n")


if __name__ == "__main__":
    main()
