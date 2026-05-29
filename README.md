<div align="center">

# RPG Platform

**English** · [中文](./README.zh-CN.md)

Open-source RPG runtime for novel-adapted, LLM-driven roleplay games.

[![tests](https://img.shields.io/badge/tests-passing-brightgreen)](#)
[![license](https://img.shields.io/badge/license-MIT-blue)](./LICENSE)
[![rust](https://img.shields.io/badge/rust-stable-orange)](#)

</div>

---

## What is this

RPG Platform is a self-hostable runtime that turns a novel, a setting bible, or any structured world data into a playable, LLM-driven roleplay game. Authors drop their story into a scenario folder; players get a typed web client backed by a Rust service that handles dice, encounters, inventory, branching saves, conversation memory, and retrieval over long-form lore. The engine stays out of the way of the fiction: rules are data, providers are pluggable, secrets are encrypted at rest, and saves are versioned like code so a single campaign can fork, replay, and recombine without losing canon. Think of it as the boring infrastructure layer sitting between your story and a frontier model, so the model can focus on roleplay and the platform can focus on state.

## Built for

- **Novel authors** who want a playable adaptation of their world without writing a game engine.
- **GMs and tinkerers** running long-form solo or shared campaigns against frontier models.
- **Developers** experimenting with agent architectures, retrieval, and tool-use over rich fictional state.

## Highlights

- **Bring your own story.** Scenarios are pure data: characters, locations, phase progression, suggestion rules, worldbook entries, and acceptance criteria. Swapping fiction is a folder swap, not a code change, and the same engine can host a dozen worlds side by side without recompiling.
- **Ten LLM providers, one catalog.** OpenAI, Anthropic, Google AI Studio, Anthropic Agent Platform, OpenRouter, DeepSeek, xAI, Alibaba Qwen, Tencent Hunyuan, and Xiaomi MiMo, surfaced through a single typed model picker with capability metadata so the client knows up front which models support tools, vision, or extended context.
- **Branchable save tree.** Commits, refs, and checkouts work like Git: snapshot any moment, fork a timeline to try an alternate decision, name a branch, merge a redemption arc back onto main, or rewind a death and keep playing.
- **Production-grade backend.** Rust with axum and sqlx, pgvector retrieval for long-form lore, KMS-wrapped provider secrets, Prometheus metrics, WebSocket and SSE streaming for token-by-token output, and an end-to-end test harness that exercises the API the way the frontend does.
- **Typed end to end.** Rust domain types are exported to TypeScript via ts-rs and consumed directly by the React client, so the API contract is checked at compile time on both sides and a backend rename surfaces as a frontend type error before the request is even sent.
- **Pluggable rules.** A D&D 5E-compatible core ships in the box covering dice, encounters, inventory, scene management, HP and AC enforcement; per-scenario JSON overrides let you reshape any of it for a different system without forking the engine or maintaining a long-running patch series.

## Quick start

```bash
docker compose -f deploy/docker-compose.yml up -d postgres
cargo run -p rpg-server
cd frontend && npm install && npm run dev
# open http://localhost:5173
```

## Documentation

- [Architecture overview](./docs/architecture.md) (TODO)
- [Scenario authoring guide](./docs/scenarios.md) (TODO)
- [Provider and model catalog](./docs/providers.md) (TODO)
- [Deployment modes](./docs/deployment.md) (TODO)

## Demo

[Screenshot here]

## Contributing

Issues, scenario packs, provider adapters, and rule modules are all welcome. The codebase is structured so that most extensions land in their own crate or JSON file rather than touching the engine core: a new provider is an implementation of a single trait, a new scenario is a directory, a new rule override is a JSON file shipped alongside it. See [CONTRIBUTING.md](./CONTRIBUTING.md) for the development loop, branch conventions, code style, and how to run the workspace tests locally before opening a pull request.

## License

MIT. See [LICENSE](./LICENSE).

---

*Originally built to adapt one specific novel, now generalized into a platform anyone can drop their story into.*
