# Git運用

## コミットメッセージ
- 形式: `<prefix>: <日本語の要約>`
- prefix: feat / fix / refactor / docs / test / chore
- 例: `feat: Phase 2のverifier.pyを実装`

## ブランチ戦略
- main: 動作確認済みのみ
- 機能開発はブランチを切る: `feature/step-0-phase-1`
- 1ステップ完了でmainにマージ

## .gitignore必須項目
- .env
- data/*.csv
- __pycache__/
- .venv/
- *.pyc