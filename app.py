import os
import json
import queue
import uuid
import zipfile
import tempfile
import threading

from flask import Flask, render_template, request, jsonify, send_file, Response, stream_with_context

from analyzer import CodeAnalyzer

app = Flask(__name__)

# ─── Env API key (support both hyphen and underscore variants) ────────────────
ENV_API_KEY: str = (
    os.environ.get('CODESLICE-DEEPSEEK-API-KEY', '') or
    os.environ.get('CODESLICE_DEEPSEEK_API_KEY', '')
).strip()

# ─── In-memory stores (single-user local tool) ────────────────────────────────
_downloads: dict[str, dict] = {}
_downloads_lock = threading.Lock()

_sse_queues: dict[str, queue.Queue] = {}
_sse_lock = threading.Lock()


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/config')
def config():
    """Tell the frontend whether an env API key is available."""
    return jsonify({'has_env_key': bool(ENV_API_KEY)})


@app.route('/analyze', methods=['POST'])
def analyze():
    data = request.get_json(force=True)

    repo_path       = data.get('repo_path', '').strip()
    question        = data.get('question', '').strip()
    screenshot_b64  = data.get('screenshot') or None
    screenshot_mime = data.get('screenshot_mime', 'image/png')
    use_import      = bool(data.get('use_import_chain', False))
    model           = data.get('model', 'deepseek-v4-pro').strip()
    job_id          = data.get('job_id', str(uuid.uuid4()))

    # API key: prefer user-supplied, fall back to env var
    api_key = data.get('api_key', '').strip() or ENV_API_KEY

    errors = []
    if not repo_path: errors.append('请输入仓库路径')
    if not question:  errors.append('请输入问题描述')
    if not api_key:   errors.append('请输入 API Key 或配置环境变量 CODESLICE_DEEPSEEK_API_KEY')
    if errors:
        return jsonify({'error': '；'.join(errors)}), 400

    q: queue.Queue = queue.Queue()
    with _sse_lock:
        _sse_queues[job_id] = q

    def run():
        try:
            analyzer = CodeAnalyzer(api_key=api_key, model=model)

            def progress_cb(stage: str, message: str):
                q.put({'type': 'progress', 'stage': stage, 'message': message})

            result = analyzer.analyze(
                repo_path=repo_path,
                question=question,
                screenshot_b64=screenshot_b64,
                screenshot_mime=screenshot_mime,
                use_import_chain=use_import,
                progress_cb=progress_cb,
            )

            dl_id = str(uuid.uuid4())
            with _downloads_lock:
                _downloads[dl_id] = {
                    'repo_path': result['repo_path'],
                    'files':     result['final_files'],
                }

            result['download_id'] = dl_id
            q.put({'type': 'done', 'result': result})

        except Exception as e:
            q.put({'type': 'error', 'message': str(e)})
        finally:
            q.put(None)

    threading.Thread(target=run, daemon=True).start()
    return jsonify({'job_id': job_id})


@app.route('/progress/<job_id>')
def progress_stream(job_id: str):
    with _sse_lock:
        q = _sse_queues.get(job_id)

    if q is None:
        return Response('data: {"type":"error","message":"job not found"}\n\n',
                        mimetype='text/event-stream')

    def generate():
        while True:
            item = q.get()
            if item is None:
                with _sse_lock:
                    _sse_queues.pop(job_id, None)
                yield 'data: {"type":"end"}\n\n'
                break
            yield f'data: {json.dumps(item, ensure_ascii=False)}\n\n'

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@app.route('/download/<dl_id>')
def download(dl_id: str):
    with _downloads_lock:
        data = _downloads.get(dl_id)
    if not data:
        return 'Download not found or expired', 404
    return _make_zip_response(data['repo_path'], data['files'])


@app.route('/redownload', methods=['POST'])
def redownload():
    data      = request.get_json(force=True)
    repo_path = data.get('repo_path', '').strip()
    files     = data.get('files', [])
    if not repo_path or not files:
        return jsonify({'error': '缺少 repo_path 或 files'}), 400
    return _make_zip_response(repo_path, files)


def _make_zip_response(repo_path: str, files: list[str]):
    import re
    parts = re.split(r'[/\\\\]+', repo_path.strip('/\\\\'))
    repo_name = next((p for p in reversed(parts) if p), 'codeslice')
    download_name = f'{repo_name}.zip'

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
    tmp.close()

    with zipfile.ZipFile(tmp.name, 'w', zipfile.ZIP_DEFLATED) as zf:
        for rel_path in files:
            abs_path = os.path.join(repo_path, rel_path)
            if os.path.isfile(abs_path):
                zf.write(abs_path, rel_path)

    return send_file(
        tmp.name,
        as_attachment=True,
        download_name=download_name,
        mimetype='application/zip',
    )


# ─── Entry ────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    if ENV_API_KEY:
        print('🔑  已从环境变量加载 API Key')
    print('🔪  Codeslice 已启动')
    print('📌  访问 http://localhost:5000')
    app.run(host='127.0.0.1', port=5000, debug=False, threaded=True)
