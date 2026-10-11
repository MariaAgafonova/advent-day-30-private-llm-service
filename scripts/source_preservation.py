"""Read-only content manifest for source projects and local model stat inventory."""
from pathlib import Path
import hashlib
import json
import sys

root = Path(__file__).resolve().parents[1]
out = root / 'reports/source-preservation.json'
projects = [Path(r'D:\AIAdvent\advent-day-28-local-rag'), Path(r'D:\AIAdvent\advent-day-29-optimized-local-rag')]

def snapshot():
    result = {}
    for project in projects:
        paths = list(project.glob('*.*'))
        for directory in ('src', 'config', 'prompts', 'scripts', 'tests', 'ui', 'models'):
            paths += [p for p in (project / directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts]
        paths += list((project / 'output').glob('structural.sqlite'))
        for path in paths:
            if path.is_file() and path.suffix not in ('.log', '.pyc'):
                result[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    weights = {str(p): {'bytes': p.stat().st_size, 'mtime_ns': p.stat().st_mtime_ns}
               for p in Path(r'F:\LocalLLM\models').rglob('*') if p.is_file()}
    return {'sha256_files': result, 'model_file_stats': weights}

if '--before' in sys.argv:
    if out.exists():
        raise SystemExit('Baseline already exists; refusing to overwrite')
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({'before': snapshot()}, ensure_ascii=False, indent=2), encoding='utf-8')
else:
    data = json.loads(out.read_text(encoding='utf-8'))
    data['after'] = snapshot()
    data['unchanged'] = data['before'] == data['after']
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'unchanged': data['unchanged'], 'hashed_files': len(data['before']['sha256_files']),
                      'weight_files_stat_checked': len(data['before']['model_file_stats'])}))
    raise SystemExit(0 if data['unchanged'] else 1)
