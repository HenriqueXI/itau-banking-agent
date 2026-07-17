# Agente Bancário Inteligente

> Assistente bancário com IA — seguro, auditável e observável — que responde perguntas via RAG com citação de fontes e executa operações bancárias via MCP, com RBAC, guardrails e confirmação humana (human-in-the-loop) para ações críticas.

**Status:** MCP, outbox transacional e auditoria completos; o workflow ponta a ponta de operação bancária (alteração de limite / PIX pelo chat) está em fase final de integração. Veja [Limitações e próximos passos](#limitações-e-próximos-passos).

---

## O que é

Implementação do desafio técnico: um agente bancário inteligente capaz de atender clientes e funcionários, que:

- **Responde perguntas** sobre tarifas, produtos e políticas usando RAG com citação obrigatória de fontes
- **Executa operações bancárias** (alteração de limite do cartão, transferências PIX) em sistemas internos expostos via MCP
- **Aplica RBAC** — papéis `customer`, `manager`, `admin` com uma camada de autorização determinística que o LLM nunca pode sobrepor
- **Exige confirmação humana** e autenticação step-up para operações críticas
- **Audita tudo** — toda ação crítica gera um evento de auditoria imutável
- **É totalmente observável** — prompts, chamadas de ferramenta, latência e uso de tokens rastreados no Langfuse

### Casos de uso do desafio → módulos do código

| Caso de uso | Módulo |
|---|---|
| 1. Consulta de informação (RAG) | `backend/src/knowledge/` |
| 2. Operação bancária (aumento de limite) | `backend/src/banking/` |
| 3. Controle de autorização | `backend/src/identity_access/` |
| 4. Operação crítica (PIX com step-up) | `backend/src/banking/` |
| 5. Observabilidade | transversal (`backend/src/shared/telemetry/` + Langfuse) |
| 6. Auditoria | `backend/src/audit/` |

## Arquitetura

Monolito modular · Arquitetura hexagonal (ports & adapters) · Event-driven com outbox transacional · DDD-lite · Orquestração de agente com LangGraph · MCP para sistemas internos.

```mermaid
flowchart LR
    U[Usuário] --> FE[Next.js + CopilotKit]
    FE -- protocolo AG-UI --> API[FastAPI]
    API --> GI[Guardrails de entrada]
    GI --> AG[Agente LangGraph]
    AG --> KB[(ChromaDB\nRAG)]
    AG -- cliente MCP --> MCP[Servidor MCP\nSistemas bancários]
    AG --> GO[Guardrails de saída]
    AG --> LLM[Gemini Flash / OpenRouter / Ollama]
    API --> PG[(PostgreSQL\nusuários · checkpoints · auditoria · outbox)]
    API --> LF[Langfuse\nTraces]
```

Visão completa (C4, ciclo de vida da requisição, portas e adaptadores, eventos): [ARCHITECTURE.md](ARCHITECTURE.md).

## Decisões arquiteturais e trade-offs

| Decisão | Escolha | Alternativas rejeitadas | Por quê |
|---|---|---|---|
| Formato de deploy | Monolito modular | Microserviços; monolito em camadas | Escopo e latência não justificam microserviços; fronteiras estritas entre módulos (verificadas por import-linter) mantêm a extração futura barata |
| Arquitetura de código | Hexagonal (ports & adapters) | N camadas; acoplada ao framework | Domínio livre de frameworks e SDKs de LLM; tudo que é volátil (provedor LLM, vector store, transporte MCP, banco) fica atrás de uma porta |
| Eventos | Outbox transacional + bus em processo | Kafka/RabbitMQ; chamadas síncronas de auditoria | Evento gravado na mesma transação da mudança de estado — nenhum evento se perde — sem o custo operacional de um broker |
| Sistema de registro | PostgreSQL | MongoDB; SQLite | Transações ACID para estado + outbox + auditoria; checkpoints do LangGraph no mesmo banco |
| Vector store | ChromaDB | FAISS; OpenSearch; pgvector | Persistente, dockerizado, filtros de metadados; FAISS não tem modo servidor, OpenSearch é pesado para o escopo |
| Orquestração do agente | LangGraph (LangChain só para loaders/integrações) | Loop manual; CrewAI; AutoGen | Checkpointing nativo, `interrupt()` para human-in-the-loop, grafo determinístico e testável |
| Estratégia de LLM | Porta de provedor: Gemini Flash (padrão) → OpenRouter (fallback) → Ollama (local) | Provedor único fixo; modelos pagos de fronteira | Resiliência a quota/instabilidade de tier gratuito; perfil 100% local para demo sem chave de API |
| Acesso a sistemas internos | Servidor MCP + adaptador cliente | REST direto; function-calling em serviços no mesmo processo | Fronteira explícita e inspecionável entre agente e sistemas bancários, como pede o desafio |
| Modelo de segurança | Camada de autorização determinística em código | Regras impostas por prompt; checagem só no lado da ferramenta | O LLM nunca é a fronteira de segurança — prompts são contornáveis, código não |
| Observabilidade | Langfuse (self-hosted) + logs JSON estruturados | LangSmith; só OpenTelemetry | Traces de prompts/ferramentas/tokens com UI própria, rodando local no compose |
| UX de agente no frontend | Next.js + CopilotKit + AG-UI | Chat SSE artesanal; Vercel AI SDK; Streamlit | Protocolo de eventos de agente pronto (streaming, confirmações) com UI de produto |

## Stack

| Camada | Tecnologia |
|---|---|
| Backend | Python 3.12, FastAPI, LangGraph, LangChain (somente loaders/integrações) |
| Dados | PostgreSQL (estado, auditoria, outbox), ChromaDB (vetores) |
| Observabilidade | Langfuse, logs JSON estruturados |
| Frontend | Next.js (App Router), CopilotKit, AG-UI, Tailwind, shadcn/ui |
| LLM | Gemini Flash (padrão), OpenRouter (fallback), Ollama (local) |
| Infra | Docker Compose |

## Como executar

**Pré-requisitos:** Docker (Docker Desktop no Windows/macOS ou Docker Engine + Compose no Linux) e Python 3.12. O processo é o mesmo nos três sistemas.

### Início rápido (Windows / macOS / Linux)

Com o Docker rodando, execute na raiz do repositório:

```bash
python main.py
```

O launcher prepara o `.env` interativamente (segredos lidos sem eco), sobe a stack completa, espera o `/health`, popula as personas de demonstração e imprime as URLs locais.

- Login de demonstração: `ana@demo` / `demo123`
- Backend: `http://localhost:8000` · Frontend: `http://localhost:3000` · Langfuse: `http://localhost:3001/project/itau-agent/traces`
- `python main.py --configure-only` — só prepara o `.env`
- `python main.py --reset-langfuse` — recria só credenciais/traces locais do Langfuse (nunca apaga dados bancários, de conversa ou auditoria)
- `python stop.py` — para tudo preservando banco e modelos; `python stop.py --volumes` — reset completo

### Perfil 100% local com Ollama

Sem quota do Gemini? Escolha **Ollama** no `python main.py`. O launcher detecta o Ollama e deixa escolher um modelo de chat: `gemma3:4b` (leve), `qwen2.5:7b` (equilibrado) ou `llama3.1:8b` (qualidade). Baixa só o modelo escolhido + `nomic-embed-text`, configura chat e embeddings sem chave externa e reindexa a base de conhecimento quando o espaço de embeddings muda.

Se o Ollama não estiver instalado: no **Windows** o launcher oferece instalação automática via `winget`; no **macOS** instale antes com `brew install ollama` (ou pelo site oficial) e no **Linux** com `curl -fsSL https://ollama.com/install.sh | sh` — depois rode `python main.py` de novo.

O serviço do Ollama fica no host (`localhost:11434`); o backend o alcança via `host.docker.internal:11434`. Para forçar reindexação da KB sem trocar de provedor: `python main.py --refresh-kb`.

### Via Make / Docker Compose

```bash
cp .env.example .env        # defaults funcionam; preencha as chaves de LLM
make up                     # postgres, chromadb, langfuse (+db), backend, frontend
make smoke                  # verifica GET /health == 200
make seed                   # personas de demonstração
```

> **Windows:** `make` não é nativo — use WSL ou Git Bash (`choco install make` / `winget install GnuWin32.Make`). No PowerShell, o equivalente de `cp` é `Copy-Item .env.example .env`. Todos os alvos são wrappers finos de `docker compose` e `uv run`; dá para rodar direto também. Ao invocar o Compose diretamente, inclua `--env-file .env`.

### Rodando só o backend

Para avaliar a API isoladamente, suba apenas as dependências no Docker e rode o backend no host:

```bash
# 1. Dependências (Postgres, ChromaDB, servidor MCP) com portas publicadas no host
cp .env.example .env
docker compose --env-file .env -f infra/docker-compose.yml -f infra/docker-compose.dev.yml up -d postgres chromadb mcp-server

# 2. Endpoints para o processo no host — o restante da configuração vem do .env
#    (bash/zsh; no PowerShell use $env:NOME="valor")
export DATABASE_URL=postgresql+asyncpg://app:app@localhost:5432/app
export CHROMA_URL=http://localhost:8001
export MCP_SERVER_URL=http://localhost:8080/mcp
```

> O `.env` precisa de uma chave de LLM (`GEMINI_API_KEY` ou OpenRouter) **ou** `LLM_PROVIDER=ollama` + `EMBEDDING_PROVIDER=ollama` com Ollama local. Sem provedor de embeddings configurável a aplicação não inicia.

**Com [uv](https://docs.astral.sh/uv/) (recomendado — usa o lockfile):**

```bash
cd backend
uv sync
uv run --env-file ../.env alembic upgrade head
uv run --env-file ../.env python -m api.main
```

(Variáveis já exportadas no shell têm precedência sobre o `--env-file`, então os endpoints `localhost` acima prevalecem.)

**Sem uv (pip + venv):**

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
set -a && source ../.env && set +a   # carrega o .env no shell (bash/zsh)
export DATABASE_URL=postgresql+asyncpg://app:app@localhost:5432/app
export CHROMA_URL=http://localhost:8001
export MCP_SERVER_URL=http://localhost:8080/mcp
alembic upgrade head
python -m api.main
```

A API fica em `http://localhost:8000` (OpenAPI em `/docs`, health em `/health`); use `API_HOST`/`API_PORT` para mudar o bind. Langfuse é opcional: sem `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` o tracer vira no-op e a aplicação avisa uma vez no boot. `python -m api.main` é equivalente a `uvicorn api.main:app`, mas no Windows seleciona o event loop compatível com o driver async do Postgres. `requirements.txt` é gerado do `uv.lock` (`uv export --format requirements-txt --no-dev --no-emit-project --no-hashes -o requirements.txt`) — o lockfile é a fonte da verdade.

### Rodando só o frontend

O frontend precisa apenas de Node 20+ e de um backend acessível (o da stack composta ou o da seção acima):

```bash
cd frontend
npm install
BACKEND_URL=http://localhost:8000 npm run dev   # PowerShell: $env:BACKEND_URL="http://localhost:8000"; npm run dev
```

Detalhes (proxy, scripts de teste): [frontend/README.md](frontend/README.md).

## Estrutura do projeto

```
├── backend/          Monolito modular Python/FastAPI
│   └── src/
│       ├── api/              Composição da API (FastAPI, rotas, middleware)
│       ├── identity_access/  Autenticação JWT, RBAC, step-up
│       ├── conversation/     Agente LangGraph, guardrails, prompts, provedores LLM
│       ├── knowledge/        RAG: ingestão, recuperação, citação
│       ├── banking/          Limite de cartão, PIX, elegibilidade, cliente MCP
│       ├── audit/            Trilha de auditoria imutável (consome eventos)
│       ├── mcp_server/       Servidor MCP que simula os sistemas bancários
│       └── shared/           Kernel compartilhado: eventos, outbox, telemetria, portas
├── frontend/         Next.js + CopilotKit (chat, confirmações)
├── infra/            Docker Compose e Dockerfiles
├── scripts/          Seed, ingestão da KB, smoke test, evals de RAG e agente
├── main.py           Launcher interativo multiplataforma
└── stop.py           Para a stack preservando dados
```

Cada módulo do backend segue a mesma anatomia hexagonal: `domain/` (regras puras, sem framework) → `application/` (casos de uso + portas) → `adapters/` (PostgreSQL, ChromaDB, MCP, LLM). Módulos nunca importam internals uns dos outros — o import-linter falha o CI se isso acontecer.

## Testes e qualidade

Dois problemas distintos, duas disciplinas distintas:

1. **Software determinístico** (regras de negócio, autorização, persistência, protocolo) → pirâmide clássica de testes: unitários rápidos, integração com testcontainers, e2e pela stack composta.
2. **Comportamento probabilístico** (compreensão/geração do LLM) → **suítes de avaliação (evals)** com datasets golden versionados no repo, pontuadas em vez de asseridas.

A matriz de autorização é testada exaustivamente — cada célula (papel × ação × propriedade do recurso), ambos os resultados. Casos adversariais (injeção de prompt, bypass de RBAC, vazamento de PII) exigem 100% de aprovação: um caso adversarial que falha é um bug de segurança, não um teste instável.

```bash
make check        # lint (ruff) + tipos (mypy) + contratos de import + testes unitários
make check-all    # + testes de integração (precisa de Docker)
make eval-rag     # avaliação de RAG contra o provedor configurado
make eval-agent   # avaliação do agente: roteamento de intenção, resolução de referências, contrato comportamental
```

## Segurança

- **O LLM nunca autoriza.** Ele decide *o que o usuário quer*; código determinístico decide *se ele pode*. Toda operação passa por autorizar → validar → gate de criticidade → executar, e não existe caminho para escrita via MCP que pule essa sequência.
- **Operações críticas** (PIX) exigem confirmação explícita do usuário (`interrupt()` do LangGraph) e autenticação step-up antes de executar.
- **Guardrails em anéis:** entrada (triagem de injeção de prompt, filtro de escopo, detecção de PII) e saída (verificação de grounding, citação obrigatória, mascaramento de PII). A resposta só é transmitida depois que os guardrails de saída aprovam — um texto já enviado não pode ser des-mascarado.
- **Auditoria imutável:** mudança de estado + evento de domínio gravados atomicamente (outbox); cada registro carrega usuário, ação, valores, timestamp e `trace_id` de correlação.
- **Dinheiro em `Decimal`** (nunca float), PII mascarada em toda exportação, segredos somente via variáveis de ambiente.

## Limitações e próximos passos

Registro honesto do que o sistema **não** faz — esconder limitações é pior do que tê-las.

**Segurança (nível demo, por decisão):**
- Step-up simulado: código de 6 dígitos emitido pela própria aplicação, não MFA real — demonstra o fluxo e a semântica de vínculo; trocar o `StepUpService` por um provedor real é o caminho.
- JWT HS256 com segredo único e token em sessionStorage; produção pediria RS256 + cookies httpOnly.
- Sem TLS local (demo em compose); login por seleção de persona, sem ciclo de vida real de credenciais.
- Triagem de injeção é melhor-esforço: padrões podem ser evadidos por ataques novos — mas defesa em profundidade garante que evadir a triagem ainda esbarra na autorização determinística; o teto de dano permanece baixo.

**Agente e RAG:**
- A resposta não é transmitida token a token: os deltas só saem depois que os guardrails de saída aprovam o texto completo (eventos de ferramenta transmitem ao vivo). Próximo passo: streaming em dois estágios.
- Histórico é a janela dos últimos N turnos, sem sumarização — conversas muito longas perdem contexto antigo.
- KB pequena e estática (reingestão manual); sem reranking/recuperação híbrida — aceitável neste tamanho de corpus.
- Modelos de tier gratuito limitam a compreensão: o gate de roteamento é 95%, não 100%; o viés para pergunta de esclarecimento é a mitigação.
- Interface apenas em pt-BR.

**Realismo bancário:**
- O servidor MCP simula o core bancário (dados seedados, sem ledger real); tabela de elegibilidade fictícia; moeda única (BRL), só conta corrente; sem score de fraude (gates são por política, não por modelo de risco); PIX sem fluxos de estorno/contestação.

**Plataforma:**
- Backend single-instance (relay do outbox assume um processo; escalar horizontalmente exige separar o worker).
- Entrega de eventos at-least-once — todo handler é idempotente por construção.
- Sem testes de carga; Langfuse é a única superfície de métricas (sem Prometheus/alertas); retenção de conversas sem TTL.

**Próximos passos:** concluir o workflow bancário ponta a ponta pelo chat (confirmação + step-up no fluxo do PIX), dashboard administrativo de auditoria, streaming em dois estágios, sumarização de histórico, reranking na recuperação e red-teaming contínuo dos guardrails.

## Licença

Projeto educacional/de avaliação para desafio técnico. Sem afiliação ou endosso do Itaú Unibanco. Valores de tarifas/taxas na base de conhecimento são fictícios.
