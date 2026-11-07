#!/usr/bin/env python3
"""Run Docling's existing verification at an exact Git commit, locally.

This is an orchestrator, not an extractor, reference generator, or CI service.
Its JSON receipt records what ran and what failed. Never prints PASS if any
required step fails, a source differs, or private benchmarks are unavailable.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time

MODEL_FILES = (
    'layout_heron.onnx', 'ocr_rec.onnx', 'ocr_det.onnx', 'ppocr_keys_v1.txt',
    'tableformer/encoder.onnx', 'tableformer/decoder.onnx', 'tableformer/bbox.onnx',
)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def source_identity(repo: Path, expected: str) -> dict:
    if not re.fullmatch(r'[0-9a-f]{40}', expected):
        raise ValueError('Expected source must be an immutable 40-character lowercase Git SHA')
    def git(*args):
        return subprocess.run(['git', '-C', str(repo), *args], capture_output=True,
                              text=True, check=True).stdout.strip()
    actual = git('rev-parse', 'HEAD')
    tree = git('rev-parse', 'HEAD^{tree}')
    dirty = git('status', '--porcelain', '--untracked-files=normal')
    if actual != expected:
        raise ValueError(f'Wrong source commit: expected {expected}, got {actual}')
    if dirty:
        raise ValueError('Source working tree is not clean; refuse ambiguous proof')
    return {'commit': actual, 'tree': tree, 'clean': True}


def model_identity(repo: Path, manifest_path: Path) -> dict:
    modelroot = repo / '.models'
    manifest = {}
    for relative in MODEL_FILES:
        file = modelroot / relative
        if not file.is_file() or file.stat().st_size == 0:
            raise ValueError(f'Pinned PDF model missing: {file}')
        manifest[relative] = {'sha256': digest(file), 'bytes': file.stat().st_size}
    render = sorted(p for p in (repo / '.docling-parse' / 'lib').glob('libdparse_render.*')
                    if p.is_file() and p.stat().st_size > 0)
    if not render:
        raise ValueError('Pinned docling-parse renderer library missing')
    manifest['renderer'] = [{'path': str(p), 'sha256': digest(p), 'bytes': p.stat().st_size}
                            for p in render]
    trusted = json.loads(manifest_path.read_text(encoding='utf-8'))
    if trusted.get('schema') != 'docling-pdf-model-set/1.0':
        raise ValueError('Unrecognized pinned-model manifest schema')
    expected = trusted.get('model_sha256')
    if not isinstance(expected, dict) or set(expected) != set(MODEL_FILES):
        raise ValueError('Pinned-model manifest must cover exactly every required model')
    for model in MODEL_FILES:
        if expected[model] != manifest[model]['sha256']:
            raise ValueError(f'Model does not match pinned reference: {model}')
    render_expected = trusted.get('renderer_sha256')
    if not isinstance(render_expected, str) or not any(
            item['sha256'] == render_expected for item in manifest['renderer']):
        raise ValueError('Renderer does not match pinned reference')
    manifest['trusted_manifest_sha256'] = digest(manifest_path)
    return manifest


def record_step(repo: Path, out: Path, label: str, argv: list[str], env: dict, timeout: int = 7200) -> dict:
    path = out / f'{len(list(out.glob("[0-9][0-9]-*.log"))) + 1:02d}-{label}.log'
    started = time.monotonic()
    with path.open('wb') as stream:
        stream.write(('$ ' + shlex.join(argv) + '\n').encode())
        stream.flush()
        try:
            result = subprocess.run(argv, cwd=repo, env=env, stdout=stream,
                                    stderr=subprocess.STDOUT, check=False, timeout=timeout)
            code = result.returncode
        except subprocess.TimeoutExpired as err:
            code = 124
            stream.write((f'\nTIMEOUT after {timeout}s: {err}\n').encode())
        except OSError as err:
            code = 127
            stream.write((f'\nEXECUTION ERROR: {err}\n').encode())
    return {'name': label, 'command': argv, 'exit_code': code,
            'duration_seconds': round(time.monotonic() - started, 3),
            'log': path.name, 'log_sha256': digest(path)}


def self_test():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        f = root / 'source.bin'
        f.write_bytes(b'unchanged original')
        assert digest(f) == hashlib.sha256(b'unchanged original').hexdigest()
        s = record_step(root, root, 'success', [sys.executable, '-c', 'print("PASS")'], os.environ.copy())
        assert s['exit_code'] == 0 and s['log_sha256'] == digest(root / s['log'])
        e = record_step(root, root, 'failure', [sys.executable, '-c', 'raise RuntimeError("error")'], os.environ.copy())
        assert e['exit_code'] != 0
        try:source_identity(root, 'moving-branch')
        except ValueError:pass
        else:raise AssertionError('moving refs must be refused')
        try:model_identity(root, root / 'manifest.json')
        except ValueError:pass
        else:raise AssertionError('missing models must fail')
        modelhashes = {}
        for relative in MODEL_FILES:
            file = root / '.models' / relative
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(('mock:' + relative).encode())
            modelhashes[relative] = digest(file)
        renderer = root / '.docling-parse' / 'lib' / 'libdparse_render.so'
        renderer.parent.mkdir(parents=True, exist_ok=True)
        renderer.write_bytes(b'mock-renderer')
        manifest = root / 'manifest.json'
        manifest.write_text(json.dumps({'schema':'docling-pdf-model-set/1.0',
                                        'model_sha256':modelhashes,
                                        'renderer_sha256':digest(renderer)}))
        assert model_identity(root, manifest)['trusted_manifest_sha256'] == digest(manifest)
        (root/'.models'/'tableformer'/'bbox.onnx').write_bytes(b'tampered')
        try:model_identity(root,manifest)
        except ValueError:pass
        else:raise AssertionError('changed model must fail')
    print('ORCHESTRATOR_SELF_TEST_PASSED (not a Rust or PDF verification)')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', type=Path, default=Path('.'))
    p.add_argument('--expected-sha', required=False)
    p.add_argument('--out', type=Path)
    p.add_argument('--mode', choices=('focused', 'full'), default='focused')
    p.add_argument('--source-root', type=Path, help='Private PDF directory; required in full mode')
    p.add_argument('--benchmark-root', type=Path, help='Private reviewed table facts; required in full mode')
    p.add_argument('--model-manifest', type=Path, help='Trusted model SHA-256 list; required in full mode')
    p.add_argument('--self-test', action='store_true')
    args = p.parse_args()
    if args.self_test:
        self_test(); return 0
    if not args.expected_sha or not args.out:
        p.error('--expected-sha and --out are required')
    repo = args.repo.resolve()
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        p.error('--out must be an empty or nonexistent directory to avoid mixing evidence')
    out.mkdir(parents=True, exist_ok=True)
    report = {'schema':'docling-direct-verification/1.0', 'status':'BLOCKED',
              'mode':args.mode, 'expected_sha':args.expected_sha,
              'started_at':dt.datetime.now(dt.timezone.utc).isoformat(),
              'steps':[], 'models':None, 'private_source_checks':None}
    def run(label, cmd, env):
        rec = record_step(repo, out, label, cmd, env)
        report['steps'].append(rec)
        if rec['exit_code'] != 0:
            raise RuntimeError(f'{label} failed (exit {rec["exit_code"]}); see {rec["log"]}')
    env = os.environ.copy()
    env.update({'CARGO_BUILD_JOBS':'2', 'CARGO_INCREMENTAL':'0',
                'DOCLING_RS_MODELS_VERIFY':'require', 'DOCLING_RS_PDF_THREADS':'1',
                'DOCLING_RS_FP32':'1', 'DOCLING_RS_SLOW_RESIZE':'1',
                'DOCLING_RS_RENDERER':'docling-parse',
                'DOCLING_PARSE_RENDER_LIB':str(repo/'.docling-parse'/'lib')})
    try:
        report['source'] = source_identity(repo,args.expected_sha)
        run('rustfmt', ['cargo','+1.96.0','fmt','--all','--check'], env)
        # Focused negative tests and the real full Rust PDF library contract.
        run('geometry', ['cargo','+1.96.0','test','--locked','-p','docling-pdf','--lib',
                         'malformed_predicted_boxes_are_rejected_before_matching'], env)
        run('ownership', ['cargo','+1.96.0','test','--locked','-p','docling-pdf','--lib',
                          'table_grid_contract_tests'], env)
        run('source-words', ['cargo','+1.96.0','test','--locked','-p','docling-pdf','--lib',
                             'source_word_partition_rejects_invented_cell_text_and_lost_words'], env)
        run('ambiguity', ['cargo','+1.96.0','test','--locked','-p','docling-pdf','--lib',
                          'cross_slot_tie_preserves_docling_first_maximum_with_separate_evidence'], env)
        run('rust-lib', ['cargo','+1.96.0','test','--locked','-p','docling-pdf','--lib'],env)
        run('clippy', ['cargo','+1.96.0','clippy','--locked','-p','docling-pdf',
                       '--all-targets','--','-D','warnings'],env)
        if args.mode == 'full':
            if not args.source_root or not args.benchmark_root or not args.model_manifest:
                raise ValueError('Full verification requires private sources, reviewed references, and a trusted model manifest')
            report['models'] = model_identity(repo, args.model_manifest.resolve())
            run('pdf-groundtruth', ['bash','scripts/conformance/pdf_groundtruth.sh'], env)
            run('pdf-snapshots', ['bash','scripts/conformance/pdf_conformance.sh'], env)
            # Real model-backed outputs; never fabricate a table or use Python as extractor.
            src = args.source_root.resolve(); bench = args.benchmark_root.resolve()
            cases = (
                ('nec', 'nec-2017.pdf', '153', 'nec_ampacity_full30_benchmark.py',
                 'nec2017_table310_15_b16_full30_reference.json', '--benchmark'),
                ('ibc', 'ibc-2018.pdf', '286', 'ibc_table_benchmark.py',
                 'ibc2018_table1004_5_reference.json', '--reference'),
            )
            checks = []
            run('build-cli',['cargo','+1.96.0','build','--release','--locked','-p','docling-cli'],env)
            binary = repo/'target'/'release'/'docling-rs'
            if not binary.is_file():
                raise ValueError('Rust CLI binary missing after successful Cargo build')
            report['converter_sha256'] = digest(binary)
            for label, pdf, page, checker, reference, reference_flag in cases:
                source=src/pdf; expected=bench/reference; check=bench/checker
                if not all(f.is_file() for f in (source,expected,check,bench/'check_docling_table_evidence.py')):
                    raise ValueError(f'Private source or reviewed reference missing for {label}')
                report['private_source_checks'] = checks
                checks.append({'label':label,'pdf_sha256':digest(source),
                               'reference_sha256':digest(expected),'page':int(page)})
                case_dir=out/label; case_dir.mkdir()
                run(f'{label}-docling', [str(binary),str(source),'--pages',page,
                                       '--to','json','--output',str(case_dir),'--output-dirs','flat'], env)
                json_path=case_dir/(source.stem+'.json')
                if not json_path.is_file():
                    raise ValueError(f'No real Docling JSON from {label} page {page}')
                checks[-1]['json_sha256']=digest(json_path)
                run(f'{label}-bindings', [sys.executable,str(check),'--source-pdf',str(source),
                                           '--docling-json',str(json_path),reference_flag,str(expected)],env)
        # Do not certify if a command mutated a tracked or untracked source file.
        report['final_source'] = source_identity(repo,args.expected_sha)
        report['status'] = 'PASS_FOCUSED' if args.mode == 'focused' else 'PASS_MODEL_AND_SOURCE'
        return 0
    except Exception as e:
        report['status']='FAIL_OR_BLOCKED'
        report['failure']=str(e)
        return 2
    finally:
        report['finished_at']=dt.datetime.now(dt.timezone.utc).isoformat()
        tmp=out/'receipt.json.tmp'
        tmp.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n',encoding='utf-8')
        tmp.rename(out/'receipt.json')
        print(f"VERIFICATION={report['status']} receipt={out/'receipt.json'}",file=sys.stderr)

if __name__=='__main__':
    raise SystemExit(main())
