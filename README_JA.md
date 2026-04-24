<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-blue?logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/License-MIT-green" alt="MIT License">
  <img src="https://img.shields.io/badge/OpenAI_Compatible-yes-brightgreen?logo=openai" alt="OpenAI Compatible">
  <img src="https://img.shields.io/badge/AsyncIO-native-purple" alt="AsyncIO">
</p>

<h1 align="center">AgentX</h1>

<p align="center">
  <strong>カスタマイズ可能で高度に抽象化された、プロダクショングレードのデジタルワーカー基盤</strong>
  <br/>
  AgentX アーキテクチャの厳密な Python 翻訳。あらゆる OpenAI 互換 LLM バックエンドに接続可能。
</p>

<p align="center">
  <a href="README.md">English</a> · <a href="README_CN.md">简体中文</a> · <strong>日本語</strong>
</p>

<p align="center">
  <img src="docs/hello.gif" alt="AgentX — インタラクティブ REPL" width="720">
</p>

---

## ヴィジョン

**カスタマイズ可能で高度に抽象化された、プロダクショングレードのデジタルワーカーを。**

AgentX は単なる AI コーディング CLI ではありません。その目標は、あらゆる個人とチームがオンデマンドでカスタマイズできる**デジタルワーカー基盤**となることです。

| 次元 | 目標 |
|------|------|
| **カスタマイズ可能** | `AGENTX_*` 環境変数、CLAUDE.md ルール体系、MCP プロトコル、カスタム Agent 定義により、各ユーザーが専用のインテリジェントアシスタントを構築。ブランド、動作、ツールスタックのすべてを置換可能。 |
| **高度に抽象化** | 再帰的自己相似 Agent アーキテクチャ：メインスレッドとサブ Agent が同一の `query()` ループを共有。Provider パターンが LLM バックエンドを完全に分離——あらゆる OpenAI 互換 API で動作。7 種の権限モードによる段階的制御。 |
| **プロダクショングレード** | 完全非同期 asyncio エンジン、ストリーミングレスポンス、Pydantic 厳格検証、自動コンテキスト圧縮、セッション永続化、マルチ Agent 連携、エラー復旧チェーン、Stop Hooks 安全終了——全コンポーネントは TypeScript 原版での検証後に厳密翻訳。 |
| **デジタルワーカー** | 「チャットボット」ではなく、読み取り・書き込み・実行・検証・自己修正が可能な Agent。Fork バックグラウンドサブ Agent、独立検証 Agent、Teammates マルチ Agent コラボレーション——本物のエンジニアのように作業を完了します。 |

<p align="center">
  <em>"Don't just chat with AI. Hire it."</em>
</p>


---

## なぜこのプロジェクト？

エージェント型コーディング CLI のパラダイムは開発者の生産性を劇的に変革しましたが、既存の実装は特定の SDK とランタイムに密結合しています。**AgentX** はこの結合を断ち切ります。

**AgentX** はエージェント型コーディング CLI の概念をイディオマティックな Python へ**厳密にアーキテクチャレベルで移植**したものです。すべてのプロンプト、ツール名、パラメータスキーマ、動作フラグを注意深く保持しつつ、ランタイムを Python の非同期エコシステムと OpenAI 互換プロバイダーパターンで再構築——これにより**あらゆる** LLM バックエンド（OpenAI, DeepSeek, Ollama, vLLM, LiteLLM 等）に接続できます。

### 主要目標

- **厳密な忠実性** — システムプロンプト、ツール説明、スキーマ、権限モードは一字一句の翻訳
- **プロバイダー非依存** — OpenAI SDK をユニバーサルインターフェースとして使用。`--provider` / 環境変数でバックエンドを切替
- **純粋な Python、ネイティブ非同期** — `asyncio`、`rich`、`prompt_toolkit`、`pydantic` で構築
- **完全なツール対等性** — 30 以上の組み込みツールを忠実に翻訳、MCP プロトコルにも対応

---

## アーキテクチャ

アーキテクチャはオリジナルの TypeScript ソースを直接ミラーリングしています。完全な翻訳ガイドは [docs/IMPLEMENTATION_STEPS.md](docs/IMPLEMENTATION_STEPS.md) を参照してください。

```
┌─────────────────────────────────────────────────────────┐
│                      CLI / REPL                         │
│              main.py  ·  ui/repl.py                     │
├───────────────────────┬─────────────────────────────────┤
│    クエリエンジン       │         状態管理                 │
│  engine/query_engine  │        state/app_state          │
│  engine/query.py      │        state/store              │
├───────────────────────┼─────────────────────────────────┤
│    LLM サービス        │       ツールシステム (30+)       │
│  services/api/client  │  tools/bash_tool                │
│  services/llm/        │  tools/file_read_tool           │
│  services/compact/    │  tools/file_edit_tool           │
│                       │  tools/agent_tool  ...          │
├───────────────────────┼─────────────────────────────────┤
│  権限システム          │     高度な機能                    │
│  permissions/checker  │  commands/   (スラッシュコマンド)  │
│  permissions/classify │  memory/     (CLAUDE.md + 記憶)  │
│  permissions/modes    │  tasks/      (マルチエージェント)  │
│                       │  services/mcp/ (MCP プロトコル)  │
└───────────────────────┴─────────────────────────────────┘
```

### 技術スタック対応表

| 次元 | TypeScript オリジナル | Python 移植版 |
|------|---------------------|--------------|
| ランタイム | Bun | Python 3.11+ / asyncio |
| AI SDK | `@anthropic-ai/sdk` | `openai`（プロバイダーパターン） |
| UI | React + Ink | `rich` + `prompt_toolkit` |
| スキーマ | Zod | `pydantic` |
| HTTP | fetch (Bun) | `httpx`（非同期） |
| 型 | TypeScript インターフェース | `dataclass(frozen=True)` + `typing` |
| MCP | `@modelcontextprotocol/sdk` | `mcp` Python SDK |
| CLI | Commander.js | `click` |

---

## クイックスタート

### 前提条件

- Python 3.11+
- 任意の OpenAI 互換プロバイダーの API キー

### インストール

```bash
# リポジトリをクローン
git clone https://github.com/NoemaZero/AgentX.git
cd agentx-py

# 仮想環境を作成
python -m venv env
source env/bin/activate  # Windows: env\Scripts\activate

# 依存関係をインストール
pip install -r requirements.txt

# または編集可能モードでインストール
pip install -e .
```

### 設定

環境変数で API 認証情報を設定：

```bash
# OpenAI
export OPENAI_API_KEY="sk-..."

# または DeepSeek
export OPENAI_API_KEY="sk-..."
export OPENAI_BASE_URL="https://api.deepseek.com"

# または任意の OpenAI 互換エンドポイント（Ollama、vLLM、LiteLLM...）
export OPENAI_API_KEY="your-key"
export OPENAI_BASE_URL="http://localhost:11434/v1"
```

### 使い方

```bash
# インタラクティブ REPL
python -m AgentX

# 単発クエリ（非インタラクティブ）
python -m AgentX "main.py のバグを修正して"

# モデル / プロバイダーを指定
python -m AgentX --model gpt-4o
python -m AgentX --provider deepseek --model deepseek-chat

# 権限モード
python -m AgentX --permission-mode auto
python -m AgentX --permission-mode plan

# 詳細出力
python -m AgentX -v
```

またはインストール済み CLI：

```bash
agentx "このコードベースを説明して"
agentx --model gpt-4o --max-turns 10
```

---

## ツールシステム

すべての 30 以上のツールはオリジナルの AgentX ツール定義の厳密な翻訳であり、名前、説明、パラメータスキーマが完全に保持されています：

| ツール | 名前 | 説明 |
|--------|------|------|
| **Bash** | `Bash` | サンドボックスでシェルコマンドを実行 |
| **Read** | `Read` | ローカルファイルシステムからファイルを読み取り |
| **Write** | `Write` | ローカルファイルシステムにファイルを書き込み |
| **Edit** | `Edit` | 検索・置換でファイルを編集 |
| **Glob** | `Glob` | 高速ファイルパターンマッチング |
| **Grep** | `Grep` | ripgrep ベースの強力な検索 |
| **Agent** | `Agent` | 複雑なタスク用のサブエージェントを起動 |
| **WebFetch** | `WebFetch` | Web コンテンツの取得・抽出 |
| **WebSearch** | `WebSearch` | Web 検索 |
| **TodoWrite** | `TodoWrite` | チェックリストでセッション進捗を追跡 |
| **NotebookEdit** | `NotebookEdit` | Jupyter Notebook セルを編集 |
| **Skill** | `Skill` | 学習済みスキルを実行 |
| **EnterPlanMode** | `EnterPlanMode` | プランモードに切替 |
| **ToolSearch** | `ToolSearch` | 遅延読込ツールを検索 |
| **MCP ツール** | 各種 | Model Context Protocol 統合 |
| ... | ... | 合計 30 以上のツール |

---

## LLM プロバイダーサポート

AgentX は **OpenAI 互換プロバイダーパターン** を採用しており、ほぼすべての LLM バックエンドで動作します：

| プロバイダー | 設定 |
|-------------|------|
| **OpenAI** | `--provider openai --model gpt-4o` |
| **DeepSeek** | `--provider deepseek --model deepseek-chat` |
| **Custom** | `--provider custom --base-url http://localhost:11434/v1` |
| **Ollama** | `--provider custom --base-url http://localhost:11434/v1 --model llama3` |
| **vLLM** | `--provider custom --base-url http://localhost:8000/v1` |
| **LiteLLM** | `--provider custom --base-url http://localhost:4000/v1` |

---

## デモ：五目並べ（Gomoku）

[`example/gomoku/`](example/gomoku/) ディレクトリには、**AgentX で完全に生成された** 五目並べゲームが収録されています。盤面の描画から AI 対戦ロジックまで、すべて自動生成です。

<p align="center">
  <img src="docs/gomoku.jpg" alt="五目並べゲームデモ — AgentX で生成" width="640">
</p>

### 生成されたゲームの特徴：
- 🎮 **PvP & PvE モード** — 人間同士または AI と対戦
- 🤖 **4段階の AI 難易度** — イージー、ミディアム、ハード、エキスパート
- ✨ **パーティクル背景エフェクト** — CSS アニメーションパーティクル
- ↩️ **待った機能** — 手を戻せます
- 📱 **レスポンシブデザイン** — デスクトップとモバイルに対応

### 試してみる：

```bash
# ブラウザでゲームを開く
open example/gomoku/index.html
```

このデモはエージェントワークフロー全体を示しています：AgentX が要件を読み取り、アーキテクチャを計画し、HTML/CSS/JS ファイルを書き、ゲームが完成するまで反復します — すべてツールシステムを通じて実行されます。

---

## 権限システム

オリジナルの権限モードの厳密な翻訳：

| モード | 動作 |
|--------|------|
| `default` | 各アクション前に権限を要求 |
| `acceptEdits` | ファイル編集を自動承認、他は要求 |
| `auto` | ほとんどのアクションを自動承認 |
| `plan` | プランモード — 実行前に設計 |
| `bypassPermissions` | すべての権限チェックをスキップ |
| `dontAsk` | 決して質問せず、事前承認されていなければ拒否 |
| `bubble` | 権限リクエストを親エージェントにバブルアップ |

---

## プロジェクト構造

```
AgentX/
├── main.py                 # CLI エントリポイント (→ main.tsx)
├── config.py               # 設定管理 (→ entrypoints/init.ts)
├── data_types.py           # コア型 (→ Tool.ts + types/)
│
├── constants/              # プロンプトと定数（厳密翻訳）
│   ├── prompts.py          # システムプロンプト (→ constants/prompts.ts)
│   └── cyber_risk.py       # セキュリティ指示 (→ cyberRiskInstruction.ts)
│
├── engine/                 # コアエンジン
│   ├── query_engine.py     # クエリエンジン (→ QueryEngine.ts)
│   ├── query.py            # クエリループ (→ query.ts)
│   └── context.py          # コンテキストビルダー (→ context.ts)
│
├── services/               # サービス層
│   ├── api/                # LLM API クライアント (→ services/api/)
│   ├── llm/                # プロバイダーパターン (OpenAI/DeepSeek/Custom)
│   ├── compact/            # コンテキスト圧縮 (→ services/compact/)
│   ├── mcp/                # MCP プロトコル (→ services/mcp/)
│   └── tools/              # ツールオーケストレーション (→ services/tools/)
│
├── tools/                  # 30+ 組み込みツール (→ tools/)
│   ├── base.py             # ツール基底クラス (→ Tool.ts)
│   ├── bash_tool.py        # シェル実行 (→ BashTool/)
│   ├── file_read_tool.py   # ファイル読み取り (→ FileReadTool/)
│   ├── file_edit_tool.py   # ファイル編集 (→ FileEditTool/)
│   ├── agent_tool.py       # サブエージェント (→ AgentTool/)
│   └── ...                 # その他のツール
│
├── permissions/            # 権限システム (→ utils/permissions/)
├── commands/               # スラッシュコマンド (→ commands.ts)
├── tasks/                  # マルチエージェントタスク (→ tasks/)
├── memory/                 # 記憶システム (→ memdir/)
├── state/                  # 状態管理 (→ state/)
├── ui/                     # ターミナル UI (→ screens/ + ink/)
└── utils/                  # ユーティリティ (→ utils/)
```

---

## 翻訳原則

### 厳密に保持（一字一句）

| カテゴリ | 保持内容 |
|----------|---------|
| **システムプロンプト** | すべての単語、すべての行 |
| **ツール名** | `'Bash'`、`'Read'`、`'Edit'` など |
| **ツール説明** | 完全な `getSimplePrompt()` / `getDescription()` テキスト |
| **パラメータスキーマ** | フィールド名、型、`.describe()` 文字列 |
| **ツール動作フラグ** | `isReadOnly`、`isConcurrencySafe`、`shouldDefer` |
| **権限モード** | 列挙値はオリジナル TypeScript と一致 |
| **コンテキスト形式** | Git Status、CLAUDE.md 出力フォーマット |

### Python への適応

| カテゴリ | 適応内容 |
|----------|---------|
| **API 層** | Anthropic SDK → OpenAI SDK（プロバイダーパターン） |
| **UI フレームワーク** | React + Ink → Rich + prompt_toolkit |
| **並行処理** | JS Promises → Python asyncio |
| **プロセス管理** | Bun.spawn → asyncio.create_subprocess_exec |
| **型システム** | TS インターフェース → dataclass(frozen=True) + typing |
| **スキーマ** | Zod → Pydantic |

---

## 開発

```bash
# 開発依存関係をインストール
pip install -e ".[dev]"

# テストを実行
pytest

# リント
ruff check AgentX/

# 型チェック
mypy AgentX/
```

---

## ドキュメント

- [IMPLEMENTATION_STEPS.md](docs/IMPLEMENTATION_STEPS.md) — 厳密翻訳ルール付きの完全な 8 フェーズ実装ガイド
- [SOURCE_EXTRACTION.md](docs/SOURCE_EXTRACTION.md) — 翻訳リファレンス用にオリジナル TypeScript ソースから抽出した正確な文字列

---

## コントリビューション

コントリビューション歓迎！貢献する際は翻訳原則に従ってください：

1. **プロンプトと説明** は TypeScript オリジナルの一字一句の翻訳であること
2. **ツール名とスキーマ** はオリジナルと完全に一致すること
3. **Python の適応** はイディオマティックに（asyncio、dataclass、pydantic）
4. **テスト必須** — 80% 以上のカバレッジを維持

---

## ライセンス

[MIT](LICENSE) © 2026 NoemaZero

---

<p align="center">
  <sub>Python エコシステムのために構築 — オープンソース、プロバイダー非依存。</sub>
</p>
