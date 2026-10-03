# IPAP — Inter-Planet Application Protocol

> **Published as prior art to prevent patent monopolization of the described concepts.**  
> Publication date: 2026-04-30. Author: A. Nakagawa (Independent)

---

## What is IPAP?

IPAP (Inter-Planet Application Protocol) is a proposed application-layer protocol for deep space communication. Instead of transmitting binary programs directly, IPAP sends natural-language **prompts** to an LLM engine installed on a remote node (rover, lander, or planetary base), which generates and executes the program locally.

**Core insight:**

> If the same prompt reproducibly generates a program that satisfies the same specification,  
> then that prompt functions as a semantic compressed representation of the program.

| | Traditional | IPAP |
|---|---|---|
| What is sent | Binary (MB~GB) | Prompt (KB) |
| Compression | Byte-level | Semantic |
| Dependency | None | LLM engine on remote node |

---

## Motivation

Earth–Mars communication has hard physical constraints:

- **One-way latency**: 3–22 minutes (depending on orbital position)
- **Bandwidth**: Typically 10 kbps–8 Mbps via Deep Space Network (DSN)
- **Blackout periods**: Up to 2 weeks during solar conjunction

Transmitting a 100 MB software update at 10 kbps takes ~22 hours.
IPAP addresses this by transmitting *intent* rather than *bytes*.

---

## IPFS Integration

IPAP leverages IPFS (InterPlanetary File System) content addressing for asset references. Instead of sending binary assets across interplanetary links, IPAP payloads can reference files already present on the remote node's local IPFS node via CID:

```json
{
  "prompt": "Analyze terrain data and optimize rover route.",
  "assets": [
    "ipfs://QmXf3kR9v2T...  (terrain map v3.2)",
    "ipfs://QmP8wN4mK1L...  (obstacle database 2026-04)"
  ]
}
```

This enables a natural two-layer architecture:

```
Intra-planet  (Mars internal) : IPFS  — high bandwidth, ms latency
Inter-planet  (Earth → Mars)  : IPAP  — low bandwidth, minutes latency
```

IPFS and IPAP share a common philosophy: addressing *what something is* rather than *where it lives*.

---

## Specification

The full protocol specification is available in RFC format:

- [`RFC-9999-IPAP.docx`](./RFC-9999-IPAP-v2.docx) — RFC-style specification document

Key sections:
- Message format & packet structure
- 6 message types: WAKE / READY / EXEC / RESULT / ABORT / STATUS
- Power management & LLM activation thresholds
- IPFS asset reference integration
- Verification & safety (3-layer: static analysis → test vectors → sandbox)
- Security considerations (prompt injection, MITM, LLM hallucination)

---

## Reference Implementation

A Python reference implementation and Earth↔Mars simulator live in [`src/ipap`](./src/ipap).
Where the RFC is silent or ambiguous, the choices made are recorded in
[`docs/spec-notes.md`](./docs/spec-notes.md).

| Module | RFC section |
|---|---|
| `packet.py`, `payloads.py` | 5. Message format (16-byte header, 6 message types) |
| `crypto.py`, `session.py` | 10. Ed25519 signatures, end-to-end encryption, replay protection |
| `node.py` | 6. Remote-node state machine, 4.2 core workflow |
| `power.py` | 7. Power thresholds and LLM activation |
| `egc.py` | 7.2 WAKE/READY handshake (skipped for CRITICAL) |
| `assets.py` | 8. IPFS CID asset references |
| `verify/` | 9. Static analysis → test vectors → sandbox, rollback to fallback |
| `llm/` | Pluggable LLM engine: deterministic mock, Claude API, Ollama |
| `link.py`, `sim.py` | Delayed, bandwidth-limited DTN-style link with blackouts |

### Quick start

```sh
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest                      # unit + end-to-end tests
ipap sim                    # run every simulation scenario
ipap sim happy abort        # or pick scenarios
```

`ipap sim` replays each exchange in compressed mission time (default: 1 mission second =
1 ms) with a 10-minute one-way light time and a 10 kbps link:

```
== happy: Route optimisation with an IPFS asset
   T+00:00:01 [earth] send WAKE seq=1 priority=NORMAL (142 B, eta T+602s)
   T+00:10:03 [mars ] recv WAKE seq=1 priority=NORMAL
   T+00:10:04 [mars ] send READY seq=1 (216 B) llm=AVAILABLE power=87.0% reply_to=1
   T+00:20:07 [earth] send EXEC seq=2 priority=HIGH (684 B, eta T+1808s)
   T+00:30:11 [mars ] state READY_CHECK -> LLM_ACTIVE
   T+00:30:56 [mars ] state LLM_ACTIVE -> VERIFYING
   T+00:32:00 [mars ] state VERIFYING -> EXECUTING
   T+00:32:32 [mars ] send RESULT seq=2 (455 B) status=OK reply_to=2
   T+00:42:35 [earth] recv RESULT seq=2 status=OK reply_to=2
-> PASS WAKE→READY→EXEC→RESULT status=OK, route cost=8; EXEC packet 684 B (0.5 s at 10 kbps)
   vs. 100 MB binary (23.3 h)
```

Scenarios: `happy`, `low-power`, `verify-fail`, `missing-asset`, `abort`, `tamper`,
`critical`, `emergency`, `conjunction`.

### Working with packets

```sh
ipap keygen earth && ipap keygen mars
ipap encode --type exec --payload examples/exec_obstacle.json \
    --key earth.key --peer mars.pub --encrypt -o exec.bin
ipap decode exec.bin --peer earth.pub --key mars.key     # verify + decrypt
ipap verify examples/obstacle_avoidance.py --vectors examples/obstacle_vectors.json
```

### Using a real LLM

The mock engine is deterministic, so tests and simulations are reproducible. To have a real
model generate the programs:

```sh
pip install -e '.[anthropic]' && ipap sim happy critical --llm anthropic   # Claude API
ipap sim happy critical --llm ollama --model qwen2.5-coder                  # local Ollama
```

Generated programs must define `main(input, assets)`; they only run after passing the
three verification layers.

---

## Prior Art Statement

This repository is published explicitly as **prior art** under the principles of defensive publication.

Any patent application filed after **2026-04-30** that claims:

- Transmission of prompts to remote LLM engines as a substitute for binary program transfer
- Semantic compression of software via natural language specifications for space communication
- Use of IPFS content identifiers within interplanetary application protocol payloads
- Power-aware LLM activation scheduling in deep space communication protocols

...should be considered to lack novelty or inventive step in light of this publication.

---

## License

This work is dedicated to the public domain under **CC0 1.0 Universal**.
No rights reserved. Use freely, without restriction.

[![CC0](https://licensebuttons.net/p/zero/1.0/88x31.png)](https://creativecommons.org/publicdomain/zero/1.0/)

---

## References

- [RFC 5050] Bundle Protocol Specification
- [RFC 9171] Bundle Protocol Version 7
- [IPFS] Benet, J., "IPFS - Content Addressed, Versioned, P2P File System", arXiv:1407.3561, 2014
- [LLM-Compression] Delétang, G. et al., "Language Modeling is Compression", ICLR 2024
- [DSN] NASA JPL, Deep Space Network

---

*"Humanity will need this before it becomes a interplanetary civilization.
This document exists so no one can own it."*
