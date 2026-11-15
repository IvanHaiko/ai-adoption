"""Assess historical top-50 traffic using same-day explicit catalogue HF declarations."""
from __future__ import annotations

import gzip
import json
from collections import defaultdict
from pathlib import Path


def assess(root: Path):
    paths = list((root / 'data/external/openrouter').glob('*/*/manifest.json'))
    path = max(paths, key=lambda p: json.loads(p.read_text())['fetched_at'])
    meta = json.loads(path.read_text())
    payload = json.loads((path.parent / 'response.json').read_bytes())
    by_day = defaultdict(list)
    for row in payload['data']:
        by_day[row['date']].append(row)
    totals = defaultdict(int)
    models = defaultdict(lambda: {'tokens': 0, 'days': 0, 'hf_ids': set()})
    daily = []
    links = []
    for day, rows in sorted(by_day.items()):
        catalog = root / f'data/raw/{day}/openrouter_models.json.gz'
        with gzip.open(catalog, 'rt', encoding='utf-8') as handle:
            routes = json.load(handle)['data']
        candidates = defaultdict(set)
        for route in routes:
            if route.get('canonical_slug'):
                candidates[route['canonical_slug']].add(route.get('hugging_face_id') or '')
        counts = defaultdict(int)
        for row in rows:
            slug, tokens = row['model_permaslug'], int(row['total_tokens'])
            counts['tokens'] += tokens
            if slug == 'other':
                counts['other_tokens'] += tokens
                continue
            counts['named_tokens'] += tokens
            counts['named_rows'] += 1
            refs = candidates.get(slug, set())
            hf = next(iter(refs)) if len(refs) == 1 and '' not in refs else None
            if refs:
                counts['catalogue_matched_tokens'] += tokens
                counts['catalogue_matched_rows'] += 1
            if hf:
                counts['hf_linked_tokens'] += tokens
                counts['hf_linked_rows'] += 1
                links.append({'date': day, 'source_model_id': slug, 'hf_repo_id': hf,
                              'tokens': tokens, 'evidence': catalog.relative_to(root).as_posix()})
            model = models[slug]
            model['tokens'] += tokens
            model['days'] += 1
            if hf:
                model['hf_ids'].add(hf)
        daily.append({'date': day, **counts})
        for key, value in counts.items():
            totals[key] += value
    ranked = [{'model_permaslug': slug, **{k: v for k, v in item.items() if k != 'hf_ids'},
               'hf_ids': sorted(item['hf_ids'])} for slug, item in models.items()]
    ranked.sort(key=lambda r: -r['tokens'])
    return {'source': 'OpenRouter', 'start': min(by_day), 'end': max(by_day),
            'as_of': payload['meta']['as_of'], 'source_sha256': meta['sha256'],
            'days': len(by_day), 'rows': len(payload['data']), 'named_models': len(models),
            'linked_models': sum(bool(r['hf_ids']) for r in ranked),
            'totals': dict(totals), 'daily': daily, 'models': ranked, 'links': links}


def main():
    root = Path(__file__).resolve().parents[1]
    result = assess(root)
    out = root / 'data/analytics/openrouter_assessment.json'
    out.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k not in ('daily', 'models', 'links')},
                     indent=2))
    print(json.dumps(result['models'][:8], indent=2))


if __name__ == '__main__':
    main()
