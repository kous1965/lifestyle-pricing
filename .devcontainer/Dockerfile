# 擬似ダイナミックプライシング 開発用コンテナ
# Python 3.13 + 非rootユーザー(vscode)入りの Dev Containers 公式イメージをベースにする。
# このコンテナ内は隔離環境なので、Claude Code の承認を緩めても
# ホスト(Mac)側には影響しない ── というのが本構成の狙い。
FROM mcr.microsoft.com/devcontainers/python:3.13

# 依存は postCreateCommand で pricing/requirements.txt から入れるが、
# キャッシュを効かせたい場合はここで先に入れてもよい。
WORKDIR /workspace

# 日本語ログが化けないように
ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONUNBUFFERED=1

# Node.js(LTS)と Claude Code CLI をコンテナに同梱する。
# このコンテナ内で `claude` を起動して開発する前提(冒頭コメントの狙い)。
# グローバル導入なので非rootの vscode ユーザーからも実行できる。
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @anthropic-ai/claude-code \
    && npm cache clean --force
