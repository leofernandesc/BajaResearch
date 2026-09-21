# BAJA Research

BAJA Research é um plugin standalone para o Hermes Agent. Ele transforma uma
pergunta técnica em uma busca multi-fonte de literatura acadêmica, normaliza e
deduplica os registros, aplica um ranking determinístico e devolve ao Hermes
um JSON compacto para a resposta em português ou no idioma do usuário.

O MVP é local e deliberadamente simples: não há aplicação web, FastAPI,
PostgreSQL, Redis, Docker obrigatório, RAG, banco vetorial, processamento em
massa de PDFs ou multiusuário.

## Arquitetura

```text
Hermes chat / gateway
        |
        +-- plugin.yaml + register(ctx)
        |      +-- search_academic_papers
        |      +-- get_paper
        |      +-- find_related_papers
        |      +-- format_citation
        |      +-- research_cache_stats
        |      +-- skill baja-research
        |
        +-- OpenAlex (principal) --+
        +-- Semantic Scholar -------+--> normalização --> deduplicação
        +-- Crossref (complementar) -+--> ranking --> SQLite/cache
```

Na versão local do Hermes verificada para este projeto (v0.20.6), a API
pública de plugin é `register(ctx)` + `ctx.register_tool(...)` +
`ctx.register_skill(...)`. Essa instalação não possui `plugin_db()`; o plugin
usa o estado persistente oficial `ctx.state.data_dir`, guardando o SQLite em
`$HERMES_HOME/plugin-data/.../baja_research.sqlite3`, fora da pasta instalável.
O core do Hermes não é modificado.

As três fontes são consultadas com `httpx`, User-Agent identificável, timeout,
retries limitados, tratamento de HTTP 429/5xx e degradação parcial. A chave
DOI, depois IDs acadêmicos, depois título+ano e somente então fuzzy matching
conservador definem a deduplicação. O citation count é log-normalizado e tem
peso pequeno no ranking.

## Pré-requisitos

- Hermes Agent instalado e executável como `hermes`.
- Python 3.11+.
- A dependência `httpx` disponível no ambiente Python usado pelo Hermes.
- Conectividade com as APIs públicas para buscas reais.

Não é obrigatório possuir nenhuma API key: OpenAlex tenta operar sem chave.
Semantic Scholar e Crossref também funcionam sem chave/mailto, mas podem sofrer
limites mais baixos. Nunca coloque credenciais neste repositório.

## Configuração

Copie os valores relevantes do `.env.example` para o `.env` do Hermes, que pode
ser localizado com:

```bash
hermes config env-path
```

Variáveis:

```dotenv
OPENALEX_API_KEY=
SEMANTIC_SCHOLAR_API_KEY=
CROSSREF_MAILTO=seu-email@exemplo.com
BAJA_RESEARCH_CACHE_TTL_HOURS=24
BAJA_RESEARCH_REQUEST_TIMEOUT_SECONDS=15
BAJA_RESEARCH_MAX_RETRIES=2
BAJA_RESEARCH_VALIDATE_LINKS=true
BAJA_RESEARCH_LINK_TIMEOUT_SECONDS=6
```

`OPENALEX_API_KEY` é opcional. `SEMANTIC_SCHOLAR_API_KEY` é opcional e, quando
presente, é enviado no header `x-api-key`. `CROSSREF_MAILTO` é opcional, mas
recomendado para o pool educado da Crossref. `BAJA_RESEARCH_VALIDATE_LINKS=true`
evita expor links de artigos que não respondem; defina `false` apenas para
diagnóstico. A validação adiciona no máximo uma verificação curta para cada
URL dos resultados finais.

## Instalação/ativação no Hermes

O repositório entregue é o diretório `/home/leofernandesc/BajaResearch`. Para
desenvolvimento local, a instalação equivalente suportada pelo scanner do
Hermes é um link dentro de `~/.hermes/plugins`:

```bash
mkdir -p ~/.hermes/plugins
ln -s /home/leofernandesc/BajaResearch ~/.hermes/plugins/baja-research
```

Se o link já existir e você estiver atualizando o checkout, remova apenas o
link específico antes de recriá-lo. Não conceda permissão para substituir
ferramentas nativas:

```bash
hermes plugins enable baja-research --no-allow-tool-override
hermes plugins list --plain --no-bundled | grep baja-research
hermes plugins doctor /home/leofernandesc/BajaResearch --ci
```

Quando o repositório tiver um commit remoto acessível, o instalador nativo
também pode ser usado com a URL Git:

```bash
hermes plugins install git@github.com:leofernandesc/BajaResearch.git --no-enable
hermes plugins enable baja-research --no-allow-tool-override
```

O instalador do Hermes apenas declara dependências Python; ele não as instala
automaticamente. Se `httpx` não estiver no venv do Hermes:

```bash
~/.hermes/hermes-agent/venv/bin/pip install 'httpx>=0.27,<1'
```

## Primeiro teste no terminal

Depois de ativar o plugin, o teste de ponta a ponta no chat é:

```bash
hermes chat -s baja-research:baja-research -q "Encontre trabalhos acadêmicos sobre fadiga de chassis tubular para Baja SAE e veículos off-road."
```

Na API atual, skills fornecidas por plugins são explicitamente carregadas com o
nome qualificado `<plugin>:<skill>`. Com a skill carregada, o Hermes deve
expandir a pergunta para cerca de três a cinco consultas em inglês técnico e
chamar `search_academic_papers`. O plugin também deixa o toolset
`baja_research` disponível no catálogo padrão após a ativação; o `-s` acima
torna o primeiro teste determinístico. Para conferir a ferramenta em modo
one-shot, use:

```bash
hermes -s baja-research:baja-research -z "Use search_academic_papers para buscar: telemetry data acquisition Formula SAE; retorne os cinco melhores resultados e informe qualquer fonte indisponível."
```

No resultado, título, autores, ano, DOI, citações e URLs devem aparecer apenas
quando retornados pelas fontes. A explicação de relevância é interpretação do
Hermes; os campos bibliográficos são dados da ferramenta.

## Testes locais e smoke test real

Crie um ambiente de desenvolvimento sem alterar o Python do Hermes:

```bash
cd /home/leofernandesc/BajaResearch
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

O smoke test faz chamadas reais, imprime status/latência/resultados
normalizados e não depende das APIs para a suíte unitária:

```bash
.venv/bin/python smoke_test.py --query "telemetry data acquisition Formula SAE"
.venv/bin/python smoke_test.py --query "Baja SAE suspension optimization"
.venv/bin/python smoke_test.py --query "off-road CVT performance"
.venv/bin/python smoke_test.py --query "Baja SAE chassis finite element analysis" --limit 5
```

Ele usa um SQLite temporário do smoke test (`.baja-research-smoke.sqlite3`,
ignorado pelo Git). Para testar apenas open access ou um período:

```bash
.venv/bin/python smoke_test.py --query "Baja SAE telemetry" --open-access-only --year-from 2018 --year-to 2026
```

## WhatsApp self-chat

O plugin não implementa uma ponte paralela. Use o suporte nativo do Hermes e
confira as opções exatas da versão instalada:

```bash
hermes whatsapp --help
hermes whatsapp
```

Complete o pareamento com o próprio número e habilite o modo self-chat quando o
assistente de configuração perguntar. Na instalação verificada, escolha `2`
(`personal number (self-chat)`), informe seu próprio número se solicitado e
escaneie o QR em `WhatsApp → Configurações → Dispositivos conectados →
Conectar dispositivo`. O wizard grava `WHATSAPP_MODE=self-chat` e habilita a
integração somente depois de encontrar a sessão pareada. Depois inicie o
gateway local em outro terminal:

```bash
hermes gateway status
hermes gateway run
```

Envie do WhatsApp uma mensagem para você mesmo, por exemplo:

```text
Busque trabalhos sobre otimização de suspensão em Baja SAE e veículos off-road.
```

Não configure grupos, allowlist de grupo, `require mention`, número dedicado ou
VPS nesta fase. A sessão do WhatsApp permanece sob controle do Hermes; BAJA
Research não lê nem registra credenciais ou conteúdo da pasta de sessão.

## Troubleshooting

- `plugin not enabled`: execute `hermes plugins enable baja-research
  --no-allow-tool-override` e confirme `hermes plugins list`.
- `httpx` ausente: instale-o no venv indicado acima e reinicie o Hermes.
- HTTP 429: o resultado ainda pode ser útil; o JSON informa a fonte limitada e
  usa as demais. Reduza a quantidade de consultas ou configure as chaves.
- Link inválido/indisponível: o resultado permanece bibliograficamente útil
  quando tem DOI, mas a URL não é exibida até passar pela verificação. Um
  status `unknown` não significa que o trabalho não existe; significa que a
  disponibilidade não pôde ser confirmada naquele momento.
- OpenAlex sem chave: isso é permitido; a disponibilidade real é informada
  pelo status da consulta.
- Nenhum resultado: tente consultas em inglês mais específicas, remova um
  filtro de ano/open access ou peça ao Hermes para ampliar para o domínio
  geral.
- SQLite: o diagnóstico está em `research_cache_stats`; o caminho real é
  profile-scoped sob `$HERMES_HOME/plugin-data`.
- WhatsApp: primeiro valide `hermes chat`; depois valide o pareamento nativo e
  o gateway. Um smoke test de API não comprova entrega no WhatsApp.

## Limitações atuais

- Query expansion é uma instrução para o Hermes/LLM; o plugin recebe a lista
  de consultas e não tenta adivinhar a intenção por regras rígidas. Há apenas
  uma salvaguarda pequena para adicionar contexto Baja/Formula SAE/off-road e
  uma variante de tese quando a consulta recebida é ampla.
- O ranking é determinístico e anterior à análise do LLM, mas não substitui
  revisão acadêmica humana. Consultas amplas mantêm contexto Baja/Formula
  SAE/off-road/veículo com `baja_context=true`; `prefer_theses=true` (padrão)
  prioriza teses, dissertações, monografias e repositórios quando a fonte
  fornece esse sinal. Isso é uma preferência, não uma garantia de que todo
  resultado será TCC.
- Links passam por verificação HTTP leve antes de serem expostos. Links 4xx
  são omitidos; 429, 5xx e falhas de rede ficam como `unknown` e também não
  são apresentados como links utilizáveis. O DOI retornado pela fonte pode
  continuar disponível como identificador bibliográfico.
- As APIs podem limitar ou alterar resultados; falhas parciais são reportadas.
- Não há download/processamento de PDF, RAG, biblioteca interna, usuários,
  frontend, servidor web ou sincronização entre computadores.

## Roadmap (não implementado neste MVP)

1. Fase 2: número dedicado, WhatsApp bot mode, grupo BAJA Research, group
   allowlist, `require mention` e Oracle VPS.
2. Fase 3: biblioteca compartilhada, artigos salvos, usuários/áreas e
   PostgreSQL se necessário.
3. Fase 4: PDFs, RAG, documentação interna, regulamentos, normas e datasheets.
4. Fase 5: monitoramento de novos papers, notificações e recomendações por
   área.
