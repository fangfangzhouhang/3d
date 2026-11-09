"""Fetch pinned official inference sources/weights without installing packages."""
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.request

ROOT = Path(__file__).resolve().parent


def sha256(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def main():
    sources = json.loads((ROOT / 'stage2_sources.json').read_text(encoding='utf-8'))
    records = []
    for repo in sources['repositories']:
        path = ROOT / repo['path']
        if not path.exists():
            subprocess.run(['git', 'init', str(path)], check=True)
            subprocess.run(['git', 'remote', 'add', 'origin', repo['url']], cwd=path, check=True)
            subprocess.run(['git', 'fetch', '--depth', '1', 'origin', repo['commit']], cwd=path, check=True)
            subprocess.run(['git', 'checkout', '--detach', 'FETCH_HEAD'], cwd=path, check=True)
        actual = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=path, text=True).strip()
        if actual != repo['commit'] or subprocess.check_output(['git', 'status', '--porcelain'], cwd=path, text=True).strip():
            raise RuntimeError(f'Existing vendor revision/contents differ: {path}; preserved')
        records.append({'path': repo['path'], 'commit': actual, 'status': 'verified'})
    for item in sources['checkpoints']:
        path = ROOT / item['path']
        path.parent.mkdir(exist_ok=True)
        if not path.exists():
            temporary = path.with_suffix(path.suffix + '.download')
            # Interrupted bytes stay visible. curl resumes HTTP Range downloads.
            subprocess.run(['curl.exe', '-L', '--fail', '--retry', '2', '--connect-timeout', '30',
                            '--max-time', '900', '-C', '-', '-o', str(temporary), item['url']], check=True)
            if sha256(temporary) != item['sha256']:
                raise RuntimeError(f'Download SHA256 mismatch; partial file preserved: {temporary}')
            temporary.rename(path)
        actual = sha256(path)
        if actual != item['sha256'] or path.stat().st_size != item['bytes']:
            raise RuntimeError(f'Existing checkpoint differs: {path}; preserved')
        records.append({'path': item['path'], 'sha256': actual, 'bytes': path.stat().st_size, 'status': 'verified'})
    out = ROOT / 'logs/stage2'
    out.mkdir(parents=True, exist_ok=True)
    # These reference files are evidence only, not imported executable code.
    references = {
        'microsam_rdf.yaml': sources['micro_sam']['model_card_url'],
        'micro_sam_LICENSE': 'https://raw.githubusercontent.com/computational-cell-analytics/micro-sam/' + sources['micro_sam']['source_commit'] + '/LICENSE',
        'micro_sam_util.py': 'https://raw.githubusercontent.com/computational-cell-analytics/micro-sam/' + sources['micro_sam']['source_commit'] + '/micro_sam/util.py',
        'microsam_predictor_adaptor.py': 'https://uk1s3.embassy.ebi.ac.uk/public-datasets/bioimage.io/diplomatic-bug/1.2/files/predictor_adaptor.py',
    }
    for name, url in references.items():
        path = out / name
        if not path.exists():
            with urllib.request.urlopen(url, timeout=30) as response:
                path.write_bytes(response.read())
        records.append({'path': str(path.relative_to(ROOT)), 'url': url, 'sha256': sha256(path)})
    architecture = out / 'microsam_predictor_adaptor.py'
    if sha256(architecture) != sources['micro_sam']['architecture_sha256']:
        raise RuntimeError('Official model architecture differs from the model-card hash')
    (out / 'setup_verified.json').write_text(json.dumps(records, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': 'verified', 'records': len(records), 'environment_installations': 0}))


if __name__ == '__main__':
    main()
