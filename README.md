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
