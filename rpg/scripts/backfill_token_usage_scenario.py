"""backfill_token_usage_scenario — 把 v30 之前的 token_usage 历史按模型/api/feature 推断 scenario。

用法:
  cd rpg && ./.venv/bin/python scripts/backfill_token_usage_scenario.py --dry-run
  cd rpg && ./.venv/bin/python scripts/backfill_token_usage_scenario.py --apply

策略(best-effort,基于模型名 + api_id):
  - model_real_name 含 "embedding" / "text-embedding" → scenario="embedding"
  - api_id 已知是 embedding-only api → scenario="embedding"
  - feature 列(如有)是 'extract' / 'extractor' → scenario="extract"
  - feature 是 'assistant' / 'console_assistant' → scenario="assistant"
  - feature 是 'verifier' / 'tool' / 'acceptance' → scenario="tool"
  - feature 是 'opening' → scenario="opening"
  - 其余保持 scenario='chat' (默认值正确)

只回填 scenario='chat' 的行(避免覆盖新数据的正确标签)。
"""
import argparse
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from platform_app.db import connect, init_db


def classify(row: dict) -> str | None:
    """根据行字段推断真实 scenario。返回 None 表示不改。"""
    model = (row.get('model_real_name') or '').lower()
    feature = (row.get('feature') or '').lower()  # 如果有 feature 列

    # 1. 模型名直接判 embedding
    if any(k in model for k in ['embedding', 'embed-', 'text-embedding']):
        return 'embedding'

    # 2. feature 字段(如果存在)
    feature_map = {
        'extract': 'extract', 'extractor': 'extract',
        'assistant': 'assistant', 'console_assistant': 'assistant',
        'verifier': 'tool', 'tool': 'tool', 'acceptance': 'tool',
        'opening': 'opening',
    }
    if feature in feature_map:
        return feature_map[feature]

    # 3. 默认不改(已经是 'chat')
    return None


def main():
    parser = argparse.ArgumentParser(description='Backfill token_usage.scenario for v30 history')
    parser.add_argument('--apply', action='store_true', help='实际写入,否则 dry-run')
    parser.add_argument('--limit', type=int, default=10000, help='单次扫描行数上限,防内存')
    args = parser.parse_args()

    init_db()
    with connect() as db:
        # 先看现有 schema 有哪些列(避免假设 feature 列存在)
        cols_row = db.execute("""
            select column_name from information_schema.columns
            where table_name = 'token_usage' order by ordinal_position
        """).fetchall()
        existing_cols = {r['column_name'] for r in cols_row}
        has_feature = 'feature' in existing_cols

        # 只扫 scenario='chat' 的行
        select_cols = "id, model_real_name, api_id, scenario"
        if has_feature:
            select_cols += ", feature"
        rows = db.execute(f"""
            select {select_cols}
            from token_usage
            where scenario = 'chat'
            limit %s
        """, (args.limit,)).fetchall()

        print(f'扫描 {len(rows)} 行 scenario="chat" 的历史数据 (limit {args.limit})')

        # 分类
        updates = {}  # id -> new_scenario
        for row in rows:
            new = classify(row)
            if new and new != 'chat':
                updates[row['id']] = new

        # 统计
        from collections import Counter
        stats = Counter(updates.values())
        print('\n推断结果:')
        for sc, n in stats.most_common():
            print(f'  {sc}: {n} 行')
        print(f'  保持 chat: {len(rows) - len(updates)} 行')

        if not args.apply:
            print('\n[DRY-RUN] 加 --apply 实际执行')
            return

        # 实际写入
        print(f'\n[APPLY] 写入 {len(updates)} 行...')
        for new_scenario in stats.keys():
            ids = [i for i, s in updates.items() if s == new_scenario]
            if not ids:
                continue
            db.execute(
                "update token_usage set scenario = %s where id = any(%s)",
                (new_scenario, ids),
            )
            print(f'  {new_scenario}: {len(ids)} 行 OK')
        db.commit()
        print('完成')


if __name__ == '__main__':
    main()
