#!/usr/bin/env python3
"""SQLite → PostgreSQL データ移行スクリプト

全テーブルのデータをSQLiteからPostgreSQLへ移行します。
テーブルはPostgreSQL側に事前にcreate_allで作成済みである前提です。

使い方:
  # 1. PostgreSQLの接続先を指定して実行
  python scripts/migrate_sqlite_to_pg.py \
      --sqlite ./aixis.db \
      --pg "postgresql://aixis:PASSWORD@HOST:5432/aixis"

  # 2. Railway環境変数から自動取得
  DATABASE_URL="postgresql://..." python scripts/migrate_sqlite_to_pg.py

  # 3. ドライラン（データ確認のみ、書き込まない）
  python scripts/migrate_sqlite_to_pg.py --dry-run
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime

# プロジェクトルートをsys.pathに追加
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def get_sqlite_tables(conn: sqlite3.Connection) -> list[str]:
    """SQLite内の全ユーザーテーブルを取得"""
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )
    return [row[0] for row in cursor.fetchall()]


def get_table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    """テーブルのカラム名一覧を取得"""
    cursor = conn.execute(f"PRAGMA table_info(\"{table}\")")
    return [row[1] for row in cursor.fetchall()]


def get_row_count(conn: sqlite3.Connection, table: str) -> int:
    """テーブルの行数を取得"""
    return conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]


def migrate_table(
    sqlite_conn: sqlite3.Connection,
    pg_conn,
    table: str,
    dry_run: bool = False,
) -> int:
    """1テーブルのデータを移行。移行行数を返す。"""
    import psycopg2.extras

    columns = get_table_columns(sqlite_conn, table)
    if not columns:
        return 0

    row_count = get_row_count(sqlite_conn, table)
    if row_count == 0:
        return 0

    # SQLiteからデータ読み出し
    cursor = sqlite_conn.execute(f'SELECT * FROM "{table}"')
    rows = cursor.fetchall()

    if dry_run:
        print(f"  [DRY RUN] {table}: {len(rows)} rows, columns: {columns}")
        return len(rows)

    # PostgreSQLへの挿入
    pg_cursor = pg_conn.cursor()

    # まず既存データをクリア（冪等な移行のため）
    pg_cursor.execute(f'DELETE FROM "{table}"')

    col_list = ", ".join(f'"{c}"' for c in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    insert_sql = f'INSERT INTO "{table}" ({col_list}) VALUES ({placeholders})'

    # JSON列の値を文字列に変換（SQLiteはTEXT、PGはJSONB）
    converted_rows = []
    for row in rows:
        new_row = []
        for val in row:
            if isinstance(val, (dict, list)):
                new_row.append(json.dumps(val, ensure_ascii=False))
            else:
                new_row.append(val)
        converted_rows.append(tuple(new_row))

    try:
        psycopg2.extras.execute_batch(pg_cursor, insert_sql, converted_rows, page_size=100)
        print(f"  {table}: {len(converted_rows)} rows migrated")
    except Exception as e:
        print(f"  {table}: ERROR - {e}")
        pg_conn.rollback()
        # リトライ: 1行ずつ挿入してエラー箇所を特定
        success = 0
        for i, row in enumerate(converted_rows):
            try:
                pg_cursor.execute(insert_sql, row)
                success += 1
            except Exception as row_err:
                print(f"    Row {i} failed: {row_err}")
                pg_conn.rollback()
        print(f"  {table}: {success}/{len(converted_rows)} rows migrated (with errors)")
        return success

    return len(converted_rows)


def reset_sequences(pg_conn, tables: list[str]):
    """PostgreSQLのシーケンス（auto-increment）をリセット"""
    pg_cursor = pg_conn.cursor()
    for table in tables:
        try:
            # SERIAL/IDENTITY列のシーケンスを最大値に設定
            pg_cursor.execute(f"""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = '{table}'
                AND column_default LIKE 'nextval%%'
            """)
            seq_cols = pg_cursor.fetchall()
            for (col,) in seq_cols:
                pg_cursor.execute(f"""
                    SELECT setval(
                        pg_get_serial_sequence('{table}', '{col}'),
                        COALESCE((SELECT MAX("{col}") FROM "{table}"), 1)
                    )
                """)
                print(f"  Sequence reset: {table}.{col}")
        except Exception as e:
            print(f"  Sequence reset skipped for {table}: {e}")
            pg_conn.rollback()


def main():
    parser = argparse.ArgumentParser(description="SQLite → PostgreSQL データ移行")
    parser.add_argument("--sqlite", default="./aixis.db", help="SQLiteファイルパス")
    parser.add_argument("--pg", default=None, help="PostgreSQL接続URL (postgresql://user:pass@host:port/db)")
    parser.add_argument("--dry-run", action="store_true", help="データ確認のみ（書き込まない）")
    args = parser.parse_args()

    # PostgreSQL URL の解決
    pg_url = args.pg or os.environ.get("DATABASE_URL", "")
    if not pg_url and not args.dry_run:
        print("ERROR: PostgreSQL接続先を指定してください")
        print("  --pg 'postgresql://user:pass@host:port/db'")
        print("  または DATABASE_URL 環境変数を設定")
        sys.exit(1)

    # asyncpg URL を psycopg2 用に変換
    if pg_url:
        pg_url = pg_url.replace("postgresql+asyncpg://", "postgresql://")
        pg_url = pg_url.replace("postgres+asyncpg://", "postgresql://")
        if pg_url.startswith("postgres://"):
            pg_url = pg_url.replace("postgres://", "postgresql://", 1)

    # SQLite接続
    if not os.path.exists(args.sqlite):
        print(f"ERROR: SQLiteファイルが見つかりません: {args.sqlite}")
        sys.exit(1)

    sqlite_conn = sqlite3.connect(args.sqlite)
    sqlite_conn.row_factory = sqlite3.Row
    tables = get_sqlite_tables(sqlite_conn)

    print(f"{'='*60}")
    print(f"SQLite → PostgreSQL 移行ツール")
    print(f"{'='*60}")
    print(f"SQLite: {args.sqlite}")
    print(f"PostgreSQL: {'(dry run)' if args.dry_run else pg_url.split('@')[-1] if '@' in pg_url else pg_url}")
    print(f"テーブル数: {len(tables)}")
    print(f"モード: {'ドライラン（読み取りのみ）' if args.dry_run else '本番移行'}")
    print()

    # データ概要
    print("テーブル別行数:")
    total_rows = 0
    nonempty_tables = []
    for table in tables:
        count = get_row_count(sqlite_conn, table)
        total_rows += count
        if count > 0:
            nonempty_tables.append(table)
        marker = f" ← {count} rows" if count > 0 else ""
        print(f"  {table}: {count}{marker}")
    print(f"\n合計: {total_rows} rows in {len(nonempty_tables)} non-empty tables")
    print()

    if args.dry_run:
        print("ドライラン完了。実行するには --dry-run を外してください。")
        sqlite_conn.close()
        return

    # 確認プロンプト
    print(f"⚠️  PostgreSQL上の全テーブルデータを上書きします。")
    confirm = input("続行しますか？ (yes/no): ").strip().lower()
    if confirm != "yes":
        print("中止しました。")
        sqlite_conn.close()
        return

    # PostgreSQL接続
    try:
        import psycopg2
    except ImportError:
        print("ERROR: psycopg2がインストールされていません")
        print("  pip install psycopg2-binary")
        sys.exit(1)

    pg_conn = psycopg2.connect(pg_url)
    pg_conn.autocommit = False

    print(f"\n移行開始: {datetime.now().isoformat()}")
    print("-" * 40)

    migrated_total = 0
    # 外部キー制約の問題を避けるため、依存関係の順序で移行
    # まずは全テーブルで制約を一時無効化
    pg_cursor = pg_conn.cursor()
    pg_cursor.execute("SET session_replication_role = 'replica';")

    for table in tables:
        migrated = migrate_table(sqlite_conn, pg_conn, table, dry_run=False)
        migrated_total += migrated

    # 制約を再有効化
    pg_cursor.execute("SET session_replication_role = 'origin';")

    # シーケンスリセット
    print("\nシーケンスリセット:")
    reset_sequences(pg_conn, tables)

    # コミット
    pg_conn.commit()
    print(f"\n{'='*60}")
    print(f"移行完了: {migrated_total} rows migrated")
    print(f"完了時刻: {datetime.now().isoformat()}")
    print(f"{'='*60}")

    # 検証
    print("\n検証中...")
    pg_cursor = pg_conn.cursor()
    mismatches = []
    for table in nonempty_tables:
        sqlite_count = get_row_count(sqlite_conn, table)
        pg_cursor.execute(f'SELECT COUNT(*) FROM "{table}"')
        pg_count = pg_cursor.fetchone()[0]
        status = "OK" if sqlite_count == pg_count else "MISMATCH"
        if status == "MISMATCH":
            mismatches.append(table)
        print(f"  {table}: SQLite={sqlite_count}, PG={pg_count} [{status}]")

    if mismatches:
        print(f"\n⚠️  不一致のあるテーブル: {', '.join(mismatches)}")
    else:
        print(f"\n全テーブルの行数が一致しています。移行成功！")

    sqlite_conn.close()
    pg_conn.close()


if __name__ == "__main__":
    main()
