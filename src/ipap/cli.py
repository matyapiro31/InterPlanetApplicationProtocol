"""Command-line interface: ``ipap keygen | encode | decode | verify | sim``."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import zlib
from pathlib import Path

from .constants import Flags, MessageType, Priority
from .crypto import Identity, load_public_key
from .link import LinkConfig
from .packet import Packet
from .payloads import PAYLOAD_TYPES, TestVector, decode_payload
from .session import Endpoint, ProtocolError
from .sim import SCENARIOS, SimOptions, run_scenario
from .verify import Verifier


def _cmd_keygen(args: argparse.Namespace) -> int:
    out = Path(args.dir)
    out.mkdir(parents=True, exist_ok=True)
    identity = Identity.generate()
    identity.save(out / f"{args.name}.key")
    (out / f"{args.name}.pub").write_text(identity.public_key.hex() + "\n")
    print(f"wrote {out / args.name}.key (secret) and {out / args.name}.pub")
    return 0


def _cmd_encode(args: argparse.Namespace) -> int:
    msg_type = MessageType[args.type.upper()]
    data = json.loads(Path(args.payload).read_text()) if args.payload else {}
    payload = PAYLOAD_TYPES[msg_type].from_dict(data)
    endpoint = Endpoint(Identity.load(args.key), load_public_key(args.peer), encrypt=args.encrypt)
    priority = Priority[args.priority.upper()] if args.priority else None
    raw = endpoint.encode(payload, priority=priority, seq=args.seq,
                          llm_version=args.llm_version)
    if args.output:
        Path(args.output).write_bytes(raw)
        print(f"wrote {len(raw)} bytes to {args.output}")
    else:
        print(raw.hex())
    return 0


def _read_packet_file(path: str) -> bytes:
    data = Path(path).read_bytes()
    try:
        return bytes.fromhex(data.decode().strip())
    except (UnicodeDecodeError, ValueError):
        return data


def _cmd_decode(args: argparse.Namespace) -> int:
    raw = _read_packet_file(args.file)
    packet = Packet.decode(raw)
    header = {
        "version": packet.version,
        "type": packet.msg_type.name,
        "flags": [f.name for f in Flags if f and packet.flags & f],
        "length": packet.length,
        "seq": packet.seq,
        "timestamp": packet.timestamp,
        "llm_version": packet.llm_version,
        "priority": packet.priority.name,
    }
    out: dict = {"header": header}
    if args.peer:
        identity = Identity.load(args.key) if args.key else Identity.generate()
        endpoint = Endpoint(identity, load_public_key(args.peer))
        try:
            out["payload"] = endpoint.parse(raw).payload.to_dict()
            out["signature"] = "valid"
        except ProtocolError as e:
            out["error"] = str(e)
    elif packet.flags & Flags.ENCRYPTED:
        out["payload"] = "<encrypted: pass --peer and --key to decrypt>"
        out["signature"] = "not checked"
    else:
        body = packet.payload
        if packet.flags & Flags.COMPRESSED:
            body = zlib.decompress(body)
        out["payload"] = decode_payload(packet.msg_type, body).to_dict()
        out["signature"] = "not checked (pass --peer)"
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 1 if "error" in out else 0


def _cmd_verify(args: argparse.Namespace) -> int:
    source = Path(args.program).read_text()
    vectors = []
    if args.vectors:
        vectors = [TestVector.from_dict(d) for d in json.loads(Path(args.vectors).read_text())]
    assets = {p.name: p.read_bytes() for p in map(Path, args.asset or [])}
    if args.input is not None:
        run_input = json.loads(args.input)
    else:  # dry-run with the first vector's input so the default invocation is meaningful
        run_input = vectors[0].input if vectors else None
    verifier = Verifier(require_test_vectors=bool(vectors))
    report = asyncio.run(verifier.verify(source, vectors, assets, args.timeout, run_input))
    print(json.dumps(report.to_list(), ensure_ascii=False, indent=2))
    print("PASSED" if report.passed else f"FAILED: {report.failure}")
    return 0 if report.passed else 1


def _cmd_sim(args: argparse.Namespace) -> int:
    names = list(SCENARIOS) if not args.scenarios or "all" in args.scenarios else args.scenarios
    unknown = [n for n in names if n not in SCENARIOS]
    if unknown:
        print(f"unknown scenario(s): {', '.join(unknown)}; choose from: all, "
              + ", ".join(SCENARIOS), file=sys.stderr)
        return 2
    link = LinkConfig(one_way_delay_s=args.delay, bandwidth_bps=args.bandwidth,
                      loss_rate=args.loss)
    opts = SimOptions(time_scale=args.time_scale, link=link, llm=args.llm,
                      encrypt=not args.no_encrypt,
                      llm_kwargs={"model": args.model} if args.model else {},
                      echo=None if args.quiet else (lambda line: print("   " + line)))

    async def run_all() -> list:
        results = []
        for name in names:
            print(f"\n== {name}: {SCENARIOS[name][0]}")
            t0 = time.monotonic()
            result = await run_scenario(name, opts)
            mark = "PASS" if result.passed else "FAIL"
            print(f"-> {mark} ({time.monotonic() - t0:.1f}s real) {result.summary}")
            results.append(result)
        return results

    results = asyncio.run(run_all())
    passed = sum(r.passed for r in results)
    print(f"\n{passed}/{len(results)} scenarios passed")
    return 0 if passed == len(results) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ipap", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("keygen", help="generate an Ed25519/X25519 identity")
    p.add_argument("name", help="file stem, e.g. earth or mars")
    p.add_argument("--dir", default=".", help="output directory")
    p.set_defaults(func=_cmd_keygen)

    p = sub.add_parser("encode", help="build a signed IPAP packet from a JSON payload")
    p.add_argument("--type", required=True, choices=[t.name.lower() for t in MessageType])
    p.add_argument("--payload", help="JSON payload file (omit for an empty payload)")
    p.add_argument("--key", required=True, help="sender secret key file")
    p.add_argument("--peer", required=True, help="recipient public key file")
    p.add_argument("--encrypt", action="store_true", help="encrypt the payload end to end")
    p.add_argument("--priority", choices=[x.name.lower() for x in Priority])
    p.add_argument("--seq", type=int, default=None)
    p.add_argument("--llm-version", type=int, default=0)
    p.add_argument("-o", "--output", help="write binary packet here (default: hex to stdout)")
    p.set_defaults(func=_cmd_encode)

    p = sub.add_parser("decode", help="inspect a packet (binary or hex file)")
    p.add_argument("file")
    p.add_argument("--peer", help="sender public key file: verify the signature")
    p.add_argument("--key", help="recipient secret key file: decrypt the payload")
    p.set_defaults(func=_cmd_decode)

    p = sub.add_parser("verify", help="run the 3-layer verification on a program file")
    p.add_argument("program")
    p.add_argument("--vectors", help="JSON list of {id, input, expected[, tolerance]}")
    p.add_argument("--asset", action="append", help="asset file passed to main (repeatable)")
    p.add_argument("--input", help="JSON input for the sandbox dry run "
                                   "(default: the first test vector's input)")
    p.add_argument("--timeout", type=float, default=10.0, help="seconds per run")
    p.set_defaults(func=_cmd_verify)

    p = sub.add_parser("sim", help="run Earth<->Mars simulation scenarios")
    p.add_argument("scenarios", nargs="*", metavar="scenario",
                   help="all (default) or any of: " + ", ".join(SCENARIOS))
    p.add_argument("--time-scale", type=float, default=1e-3,
                   help="real seconds per mission second (default 0.001)")
    p.add_argument("--delay", type=float, default=600.0, help="one-way light time, seconds")
    p.add_argument("--bandwidth", type=float, default=10_000.0, help="link rate, bit/s")
    p.add_argument("--loss", type=float, default=0.0, help="packet loss probability")
    p.add_argument("--llm", default="mock", choices=["mock", "anthropic", "ollama"])
    p.add_argument("--model", help="model name for the anthropic/ollama engines")
    p.add_argument("--no-encrypt", action="store_true", help="send payloads in the clear")
    p.add_argument("-q", "--quiet", action="store_true", help="only print scenario summaries")
    p.set_defaults(func=_cmd_sim)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
