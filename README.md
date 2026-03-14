# Aixis 破壊的テスト自動化エージェント

AI/SaaSツールに対する破壊的テストの自動実行・スコアリングエージェント。

## セットアップ

```bash
uv venv .venv && source .venv/bin/activate
uv pip install -e ".[dev]"
playwright install chromium
```

## 使い方

```bash
# テストケース生成のみ
aixis generate

# ドライラン（実行なし）
aixis run --target example_target --dry-run

# 実行
aixis run --target config/targets/example_target.yaml

# レポート生成
aixis report --session <session-id> --format all
```
