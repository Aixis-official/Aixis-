# Aixis Platform - 災害復旧手順書 (Disaster Recovery Runbook)

## 1. バックアップ体制の概要

```
┌─ Railway (本番) ─────────────────────────────────┐
│  PostgreSQL (永続ボリューム)                        │
│    ├─ Railway自動バックアップ (PITR)               │
│    └─ Aixis自動バックアップ:                       │
│         ├─ 毎時 (48世代) → GDrive自動UP           │
│         ├─ 毎日 (30世代)                          │
│         └─ 毎週 (12世代)                          │
└──────────────────────────────────────────────────┘
           │
           ▼
┌─ Google Drive (オフサイト) ───────────────────────┐
│  pg_dump バックアップファイル (SHA-256検証済み)       │
└──────────────────────────────────────────────────┘
```

## 2. 復元シナリオ別手順

### シナリオA: Railwayコンテナの再起動/再デプロイ

**影響**: なし (PostgreSQLはコンテナ外の永続ボリュームに保存)
**対応**: 不要。自動的にデータが復元されます。

### シナリオB: PostgreSQLデータの破損/誤削除

**手順**:

1. Google Driveから最新のバックアップファイルをダウンロード
   - ダッシュボード → 設定 → GDriveバックアップ一覧から取得
   - またはGoogle Driveフォルダに直接アクセス

2. ローカルでバックアップの整合性を確認
   ```bash
   # SHA-256チェックサム確認
   sha256sum downloaded_backup.pgdump
   # backups/backup_manifest.json の checksum と照合
   ```

3. PostgreSQLにリストア
   ```bash
   # Railway CLI でリモートDBに接続
   railway run pg_restore \
       --clean --if-exists --no-owner --no-acl \
       -d "$DATABASE_URL" \
       downloaded_backup.pgdump
   ```

4. アプリを再起動
   ```bash
   railway up
   ```

### シナリオC: Railway全体の障害

**手順**:

1. 別のホスティング環境にPostgreSQLを立てる
   ```bash
   docker run -d --name aixis-pg \
       -e POSTGRES_USER=aixis \
       -e POSTGRES_PASSWORD=YOUR_PASSWORD \
       -e POSTGRES_DB=aixis \
       -p 5432:5432 \
       postgres:16-alpine
   ```

2. GDriveからバックアップをリストア
   ```bash
   pg_restore --clean --if-exists --no-owner --no-acl \
       -h localhost -U aixis -d aixis \
       downloaded_backup.pgdump
   ```

3. `DATABASE_URL` を新しいPGに向けてアプリを起動
   ```bash
   DATABASE_URL="postgresql://aixis:PASSWORD@HOST:5432/aixis" \
   uvicorn aixis_web.app:app --host 0.0.0.0 --port 8000
   ```

### シナリオD: SQLiteからの復元 (開発環境)

```bash
# SQLiteバックアップから直接復元
cp backups/aixis_YYYYMMDD_HHMMSS_daily.db ./aixis.db

# 整合性確認
sqlite3 aixis.db "PRAGMA integrity_check;"
```

### シナリオE: SQLite → PostgreSQL 移行

```bash
# 1. psycopg2をインストール
pip install psycopg2-binary

# 2. 移行スクリプト実行
python scripts/migrate_sqlite_to_pg.py \
    --sqlite ./aixis.db \
    --pg "postgresql://aixis:PASSWORD@HOST:5432/aixis"

# 3. ドライラン (事前確認)
python scripts/migrate_sqlite_to_pg.py --dry-run
```

## 3. バックアップの確認コマンド

```bash
# バックアップヘルスチェック (API)
curl -H "Authorization: Bearer $TOKEN" \
     https://platform.aixis.jp/api/v1/settings/backup/health

# 個別バックアップの整合性検証
curl -X POST \
     -H "Authorization: Bearer $TOKEN" \
     https://platform.aixis.jp/api/v1/settings/backup/verify/FILENAME

# バックアップ一覧
curl -H "Authorization: Bearer $TOKEN" \
     https://platform.aixis.jp/api/v1/settings/backups
```

## 4. 定期確認チェックリスト

- [ ] 週1回: ダッシュボード設定画面でバックアップ状態を確認
- [ ] 週1回: Google Driveフォルダにバックアップが蓄積されているか確認
- [ ] 月1回: バックアップからの復元テストを実施
- [ ] デプロイ後: `/api/v1/settings/backup/health` が `healthy` を返すことを確認

## 5. 連絡先・エスカレーション

| 事象 | 対応者 | 手順 |
|------|--------|------|
| バックアップ失敗アラート | 管理者 | ダッシュボード設定画面を確認 |
| DB破損 | 管理者 | シナリオBの手順に従う |
| Railway全体障害 | 管理者 | シナリオCの手順に従う |
