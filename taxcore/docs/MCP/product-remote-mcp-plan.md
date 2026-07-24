# Product plan — B3 Portfolio as a remote MCP service (BYO inference)

**Status**: Draft / parked — "we will do that later"
**Created**: 2026-05-27
**Decision**: Productize the existing B3 toolkit as a **hosted (remote) MCP server** that users connect to from their own AI app (Claude Desktop / ChatGPT). Tools run server-side; only derived output reaches the LLM. The user's own AI subscription funds inference.

> This is a strategy/architecture doc, not an implementation. It captures the chosen direction and its accepted caveats so we can pick it up later.

---

## 1) The model in one paragraph

A non-technical Brazilian retail investor connects their existing **Claude Desktop or ChatGPT subscription** to our hosted MCP server (a connector — no local install). They upload their B3 broker exports through a simple web account. Our **server-side tools** (the current Python scripts) parse, rank, and compute tax/portfolio analysis. Only the **derived output** (tables, summaries) is returned to the LLM, which the user's own subscription drives. We never sell inference — the customer brings it.

---

## 2) Why this shape (and what we explicitly accept)

**Chosen because:**
- **Zero install for the user** — solves the biggest blocker for a non-technical audience. Just connect a remote MCP server in their AI app.
- **We don't pay for inference** — the cost that destroys bring-your-own-margin products is funded by the user's own Claude/ChatGPT subscription. We only pay for cheap deterministic compute (pandas scripts).
- **Their own harness/subscription** — matches the original "people use their own LLM" intent.

**Accepted caveats (the user explicitly likes/accepts these):**
- **We become a financial-data custodian.** Raw B3 exports + holdings live on our server so server-side tools can process them. This triggers **LGPD** obligations, breach liability, and a real security burden. This is the opposite of the "data never leaves your machine" model — accepted on purpose.
- **Privacy claim is narrowed**, not eliminated: "only *derived* output (not raw broker files) reaches the LLM provider." The LLM still sees the user's portfolio/tax numbers in those outputs — unavoidable, since reasoning about them is the point. Marketing must not overclaim.
- **This is effectively a hosted SaaS where the customer brings the inference.** ~80% of the build is a normal web backend (accounts, upload, storage, security); the MCP connector is the thin layer. Accepted.

**The trilemma we resolved:** {no install, data stays local, non-technical} — pick ~two. We chose **no-install + non-technical** and sacrificed **data-local**.

---

## 3) Architecture sketch

```
┌─────────────────────┐         ┌──────────────────────────────────────┐
│ User's AI app        │  MCP    │ Our hosted MCP server                 │
│ (Claude Desktop /    │◀──────▶ │  - auth (maps MCP session → account)  │
│  ChatGPT connector)  │ remote  │  - tools: ingest / rank / irpf / tlh  │
│  ↑ user's own sub    │ (HTTP/  │  - returns DERIVED OUTPUT only        │
└─────────────────────┘  SSE)   └───────────────┬──────────────────────┘
        ▲ derived output only                    │ reads/writes
        │                                         ▼
┌───────┴──────────┐                  ┌────────────────────────────┐
│ Web app          │  upload broker   │ Per-user encrypted storage  │
│ (account + file  │ ───────────────▶ │ (B3 exports, parsed CSVs)   │
│  upload UI)      │                  └────────────────────────────┘
└──────────────────┘
```

- **Transport**: remote MCP over HTTP/SSE (supported by Claude Desktop & ChatGPT connectors). Each MCP session must authenticate and resolve to one user's data scope.
- **Tools run server-side**; the LLM receives only tool results (text/markdown/tables), never raw files.
- **Inference**: entirely on the user's subscription, outside our infra.

---

## 4) Scripts → MCP tools mapping

| Current script | MCP tool | Notes for server-side |
|---|---|---|
| `scripts/load_b3.py` | `ingest_b3_reports` | Operates on the user's uploaded files in their storage scope; returns a parse summary (positions count, date range, orphan-sell warnings) — NOT raw rows. |
| `scripts/fetch_fundamentus.py` / `_fii.py` | `refresh_market_snapshot` | Shared across users (market data isn't personal); snapshot cache server-side, append-only per existing convention. |
| `scripts/fetch_external_prices.py` | (folded into refresh) | yfinance fallback for held tickers. |
| `scripts/rank.py` / `rank_fii.py` | `rank_equities` / `rank_fiis` | Accept weight overrides as tool args (the "override weights ad-hoc" behavior from CLAUDE.md). |
| `scripts/portfolio_status.py` | `portfolio_status` | Returns the holdings table + totals as markdown. |
| `scripts/sector_exposure.py` | `sector_exposure` | Concentration flags. |
| _(removed; now `GET /portfolio/index`)_ | `portfolio_vs_benchmark` | Daily TWR index vs IBOV/CDI |
| `scripts/suggest_allocation.py` | `suggest_allocation` | amount arg. |
| `scripts/irpf_2026.py` | `irpf_report` | Year arg; returns the copy-paste IRPF markdown. **Liability-sensitive** — keep the "verificar no informe" / not-advice framing. |
| `scripts/tlh_plan.py` | `tlh_plan` | Currently interactive — needs a non-interactive tool signature (inputs as args, no stdin prompts). |

**Refactor implied:** tools must be pure functions of (user data scope, args) → result. The interactive `tlh_plan.py` and any stdin/CLI-arg coupling must be wrapped in a non-interactive API. The methodology modules (`methodologies/`) are already pure and reusable.

---

## 5) Build phases (rough, for later)

1. **Tool API layer** — wrap each script as a callable (no stdin, no `print`-to-stdout coupling); return structured + markdown. Add tests.
2. **MCP server** — expose the tools over remote MCP; stub auth (single test user) first.
3. **Storage + account model** — per-user encrypted file storage; upload endpoint; map MCP session → user scope.
4. **Web app** — minimal: sign up, upload B3 exports, see connection instructions for Claude Desktop / ChatGPT.
5. **Auth hardening + LGPD** — consent, data-retention/delete, encryption at rest, audit log, DPA.
6. **Connector UX** — one-screen "connect your AI app" guide; test against Claude Desktop and ChatGPT.
7. **Compliance/legal review** — financial-advice framing, CVM-adjacent review, ToS, privacy policy.

---

## 6) Open risks to resolve before building

- **LGPD / data custody** — biggest one. Need legal review, encryption, deletion flows, breach plan. Possibly minimize stored data (delete raw exports after parsing? keep only derived CSVs?).
- **Consumer-subscription reality** — confirm remote MCP connector support & terms for *consumer* Claude/ChatGPT plans (not just API). Verify this isn't against either provider's usage terms.
- **Financial-advice liability**, amplified for non-technical users who may read output as advice. Keep all "analysis, not recommendation" guardrails from `CLAUDE.md`.
- **Fundamentus scraping at scale** — ToS, rate limits, reliability when many users trigger refreshes. Likely need a single shared scheduled fetch, not per-user.
- **Broker export format drift** — many users, many layouts → parser-robustness + support burden.
- **Distribution mismatch** — BR retail is mobile/WhatsApp-first; desktop AI connectors are a narrow (but growing) audience today.

---

## 7) Decisions still pending (intentionally deferred)

- Free vs paid (subscription? per-report? open-core?).
- Whether to also offer the **local-privacy** variant (packaged local MCP/app) for privacy-sensitive users.
- Whether to generalize beyond B3 later (market-agnostic framework, B3 as one plugin).

---

*Not financial, legal, or compliance advice. LGPD and CVM items above are flags for a qualified review, not conclusions.*
