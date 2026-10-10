"""Scripted stand-in for the Node sidecar, speaking the same JSON-lines protocol.

Each item's `user` text is a JSON script of RPCs to issue; the RPC answers are
returned in the item_result payload so tests can assert the backend's side of
the protocol. It also refuses to start if it inherited unexpected environment.
"""

import json
import os
import sys

ALLOWED_ENV = {
    "HOME",
    "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME",
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
    "TMPDIR",
    "TZ",
    "LANG",
    "NODE_ENV",
    "LC_CTYPE",  # set by CPython's UTF-8 mode on some platforms
}
_next = 0


def send(value: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(value) + "\n")
    sys.stdout.flush()


def receive() -> dict[str, object] | None:
    line = sys.stdin.readline()
    return json.loads(line) if line else None


def rpc(method: str, params: dict[str, object]) -> dict[str, object]:
    global _next
    request_id = _next
    _next += 1
    send({"type": "rpc", "id": request_id, "method": method, "params": params})
    while True:
        message = receive()
        if message is None:
            sys.exit(3)
        if message.get("type") == "rpc_result" and message.get("id") == request_id:
            return message


def main() -> None:
    start = receive()
    if not start or start.get("type") != "start" or sys.argv[-1] != "run":
        sys.exit(2)
    if set(os.environ) - ALLOWED_ENV or os.listdir(os.environ["HOME"]):
        send({"type": "fatal", "category": "env_leak"})
        sys.exit(1)
    send({"type": "ready", "provider_id": "custom", "model_id": start["model_id"]})
    while True:
        message = receive()
        if message is None or message.get("type") == "end":
            send({"type": "done"})
            return
        script = json.loads(str(message["user"]))
        if script.get("raw") is not None:
            sys.stdout.write(script["raw"] + "\n")
            sys.stdout.flush()
            continue
        answers: list[dict[str, object]] = []
        last: dict[str, object] = {}
        for step in script["steps"]:
            params = dict(step["params"])
            if params.get("item_id") == "":
                params["item_id"] = message["item_id"]
            for key in ("attempt", "lock"):
                if params.get(key) == "$last":
                    params[key] = last.get(key)
            answer = rpc(step["method"], params)
            result = answer.get("result")
            if isinstance(result, dict):
                last.update(
                    {key: result[key] for key in ("attempt", "lock") if key in result}
                )
            answers.append(answer)
        if script.get("crash"):
            sys.exit(9)  # die mid-item, leaving reserved attempts unfinalized
        send(
            {
                "type": "item_result",
                "item_id": message["item_id"],
                "status": script.get("status", "submitted"),
                "payload": {"answers": answers}
                if script.get("status", "submitted") == "submitted"
                else None,
                "category": script.get("category"),
                "turns": 1,
                "tool_calls": 0,
            }
        )


if __name__ == "__main__":
    main()
