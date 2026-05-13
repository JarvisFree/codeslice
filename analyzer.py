import os
import re
import json
from openai import OpenAI

# ─── Constants ────────────────────────────────────────────────────────────────

IGNORE_DIRS = {
    '.git', '.svn', '.hg', '.bzr',
    'node_modules', '__pycache__', '.venv', 'venv', 'env', '.env',
    'dist', 'build', '.next', '.nuxt', '.output', '.svelte-kit',
    'coverage', '.nyc_output', '.pytest_cache', '.cache',
    'vendor', 'target', '.gradle', 'Pods',
    '.idea', '.vscode', '.vs',
    'logs', 'tmp', 'temp', '.temp',
    'public', 'static',  # usually not code
}

IGNORE_EXTENSIONS = {
    # Compiled / binary
    '.pyc', '.pyo', '.pyd', '.class', '.o', '.obj',
    '.exe', '.dll', '.so', '.dylib', '.lib', '.a',
    # Images / media
    '.jpg', '.jpeg', '.png', '.gif', '.ico', '.bmp', '.webp', '.avif', '.tiff',
    '.mp4', '.mp3', '.wav', '.ogg', '.webm', '.avi', '.mov', '.flv',
    # Fonts
    '.woff', '.woff2', '.ttf', '.eot', '.otf',
    # Archives
    '.zip', '.tar', '.gz', '.bz2', '.7z', '.rar',
    # Binary docs
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
    # Build artifacts
    '.lock', '.log', '.map', '.snap',
    # Misc binary
    '.db', '.sqlite', '.sqlite3', '.bin', '.dat',
}

JS_EXTENSIONS  = {'.js', '.ts', '.jsx', '.tsx', '.vue', '.mjs', '.cjs', '.svelte'}
PY_EXTENSIONS  = {'.py', '.pyw'}

MAX_FILE_SIZE       = 150 * 1024   # 150 KB per file
MAX_CANDIDATES      = 50           # files read in phase 2
MAX_CONTENT_CHARS   = 400_000      # ~100 K tokens safety cap


# ─── Analyzer ─────────────────────────────────────────────────────────────────

class CodeAnalyzer:
    def __init__(self, api_key: str, model: str = 'deepseek-chat'):
        self.client = OpenAI(
            api_key=api_key,
            base_url='https://api.deepseek.com',
        )
        self.model = model

    # ── File Tree ─────────────────────────────────────────────────────────────

    def scan_file_tree(self, repo_path: str) -> list[str]:
        """Walk repo and return sorted list of relative POSIX paths."""
        files = []
        for root, dirs, filenames in os.walk(repo_path, topdown=True):
            dirs[:] = sorted(
                d for d in dirs
                if d not in IGNORE_DIRS and not d.startswith('.')
            )
            for filename in sorted(filenames):
                if filename.startswith('.'):
                    continue
                ext = os.path.splitext(filename)[1].lower()
                if ext in IGNORE_EXTENSIONS:
                    continue
                abs_path = os.path.join(root, filename)
                rel = os.path.relpath(abs_path, repo_path).replace(os.sep, '/')
                files.append(rel)
        return files

    # ── File Reading ──────────────────────────────────────────────────────────

    def _read_file(self, repo_path: str, rel_path: str) -> str:
        abs_path = os.path.join(repo_path, rel_path)
        try:
            size = os.path.getsize(abs_path)
            with open(abs_path, 'r', encoding='utf-8', errors='replace') as f:
                if size > MAX_FILE_SIZE:
                    return f.read(MAX_FILE_SIZE) + f'\n... [truncated — {size} bytes total]'
                return f.read()
        except Exception as e:
            return f'[Cannot read: {e}]'

    # ── Phase 1 — Candidate Selection (File Tree) ─────────────────────────────

    def phase1_select_candidates(
        self,
        files: list[str],
        question: str,
        screenshot_b64: str | None,
        screenshot_mime: str,
        progress_cb=None,
    ) -> list[str]:
        if progress_cb:
            progress_cb('phase1_start', f'正在让 AI 分析文件树（{len(files)} 个文件）...')

        tree_text = '\n'.join(files)

        system = (
            'You are a code file relevance analyzer.\n'
            'Given a repository file tree and a developer\'s question, identify which files are likely relevant.\n'
            'RULES:\n'
            '- Output ONLY a valid JSON array of relative file paths. No explanation, no markdown fences.\n'
            '- Be generous: include 20–50 candidates rather than being too strict.\n'
            '- Use the EXACT paths from the file tree.'
        )

        user_parts: list[dict] = []

        if screenshot_b64:
            user_parts.append({'type': 'text', 'text': 'Here is a screenshot related to the question:'})
            user_parts.append({
                'type': 'image_url',
                'image_url': {'url': f'data:{screenshot_mime};base64,{screenshot_b64}'},
            })

        user_parts.append({
            'type': 'text',
            'text': (
                f'Developer question:\n{question}\n\n'
                f'Repository file tree ({len(files)} files):\n{tree_text}\n\n'
                'Return a JSON array of candidate file paths.'
            ),
        })

        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {'role': 'system', 'content': system},
                {'role': 'user',   'content': user_parts},
            ],
            temperature=0.1,
            max_tokens=2000,
        )

        raw = resp.choices[0].message.content.strip()
        result = self._parse_json_array(raw, valid_set=set(files))

        if progress_cb:
            progress_cb('phase1_done', f'第一阶段完成，筛出 {len(result)} 个候选文件')

        return result

    # ── Phase 2 — Final Selection (File Contents) ─────────────────────────────

    def phase2_final_selection(
        self,
        repo_path: str,
        candidates: list[str],
        question: str,
        progress_cb=None,
    ) -> list[str]:
        if progress_cb:
            progress_cb('phase2_start', f'正在读取 {len(candidates[:MAX_CANDIDATES])} 个候选文件内容...')

        parts: list[str] = []
        total_chars = 0
        included: list[str] = []

        for rel_path in candidates[:MAX_CANDIDATES]:
            content = self._read_file(repo_path, rel_path)
            block = f'=== {rel_path} ===\n{content}\n'
            if total_chars + len(block) > MAX_CONTENT_CHARS:
                break
            parts.append(block)
            total_chars += len(block)
            included.append(rel_path)

        files_text = '\n'.join(parts)

        system = (
            'You are a precise code file relevance analyzer.\n'
            'Given file contents and a developer\'s question, select ONLY the files '
            'that are directly relevant to understanding or solving the problem.\n'
            'RULES:\n'
            '- Output ONLY a valid JSON array of file paths. No explanation, no markdown fences.\n'
            '- Be precise: omit files that are only tangentially related.\n'
            '- Use the EXACT paths shown in the === headers.'
        )

        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {'role': 'system', 'content': system},
                {
                    'role': 'user',
                    'content': (
                        f'Question:\n{question}\n\n'
                        f'Files:\n{files_text}\n'
                        'Return a JSON array of the relevant file paths.'
                    ),
                },
            ],
            temperature=0.1,
            max_tokens=2000,
        )

        raw = resp.choices[0].message.content.strip()
        result = self._parse_json_array(raw, valid_set=set(included))

        if progress_cb:
            progress_cb('phase2_done', f'第二阶段完成，最终确定 {len(result)} 个相关文件')

        return result if result else included  # fallback

    # ── Phase 3 — Import Chain ────────────────────────────────────────────────

    def scan_import_chain(
        self,
        repo_path: str,
        seed_files: list[str],
        all_files: list[str],
        progress_cb=None,
    ) -> list[str]:
        if progress_cb:
            progress_cb('phase3_start', '正在扫描 import 引用链...')

        all_set    = set(all_files)
        result_set = set(seed_files)
        queue      = list(seed_files)
        added: list[str] = []

        while queue:
            rel_path = queue.pop(0)
            try:
                with open(os.path.join(repo_path, rel_path), 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
            except Exception:
                continue

            for imp in self._extract_imports(content, rel_path):
                resolved = self._resolve_import(imp, rel_path, all_set)
                if resolved and resolved not in result_set:
                    result_set.add(resolved)
                    queue.append(resolved)
                    added.append(resolved)

        if progress_cb:
            progress_cb('phase3_done', f'引用链扫描完成，新增 {len(added)} 个依赖文件')

        return sorted(result_set)

    def _extract_imports(self, content: str, file_path: str) -> list[str]:
        ext = os.path.splitext(file_path)[1].lower()
        imports: list[str] = []

        if ext in PY_EXTENSIONS:
            for m in re.finditer(r'^(?:from|import)\s+([\w.]+)', content, re.MULTILINE):
                imports.append(m.group(1).replace('.', '/'))

        elif ext in JS_EXTENSIONS:
            # import ... from '...'  /  require('...')  /  import('...')
            for m in re.finditer(
                r"""(?:from|require|import)\s*\(?\s*['"](\.[^'"]+)['"]""",
                content,
            ):
                imports.append(m.group(1))

        return imports

    def _resolve_import(self, import_str: str, from_file: str, all_set: set) -> str | None:
        from_dir = os.path.dirname(from_file)

        if import_str.startswith('.'):
            base = os.path.normpath(os.path.join(from_dir, import_str)).replace(os.sep, '/')
            candidates = [
                base,
                base + '.ts',  base + '.tsx',
                base + '.js',  base + '.jsx',
                base + '.vue', base + '.py',
                base + '/index.ts', base + '/index.js',
                base + '/index.vue', base + '/index.tsx',
            ]
            for c in candidates:
                if c in all_set:
                    return c

        else:
            # Absolute / aliased — try to fuzzy match last 1-2 segments
            parts  = import_str.split('/')
            suffix = '/'.join(parts[-2:]) if len(parts) >= 2 else parts[-1]
            for f in all_set:
                for ext in ('.ts', '.tsx', '.js', '.jsx', '.vue', '.py', ''):
                    if f.endswith(suffix + ext):
                        return f

        return None

    # ── JSON Helper ───────────────────────────────────────────────────────────

    def _parse_json_array(self, text: str, valid_set: set) -> list[str]:
        text = re.sub(r'```(?:json)?', '', text).strip('` \n')
        m = re.search(r'\[.*?\]', text, re.DOTALL)
        if not m:
            return []
        try:
            arr = json.loads(m.group())
            return [f for f in arr if isinstance(f, str) and f in valid_set]
        except json.JSONDecodeError:
            return []

    # ── Main Entry ────────────────────────────────────────────────────────────

    def analyze(
        self,
        repo_path: str,
        question: str,
        screenshot_b64: str | None = None,
        screenshot_mime: str = 'image/png',
        use_import_chain: bool = False,
        progress_cb=None,
    ) -> dict:
        repo_path = os.path.abspath(repo_path)

        if not os.path.isdir(repo_path):
            raise ValueError(f'路径不存在或不是目录: {repo_path}')

        if progress_cb:
            progress_cb('scan_start', '正在扫描仓库文件树...')

        all_files = self.scan_file_tree(repo_path)
        if not all_files:
            raise ValueError('仓库中未找到任何可分析的文件')

        if progress_cb:
            progress_cb('scan_done', f'扫描完成，共 {len(all_files)} 个文件')

        # Phase 1
        candidates = self.phase1_select_candidates(
            all_files, question, screenshot_b64, screenshot_mime, progress_cb
        )
        if not candidates:
            raise ValueError('AI 未能识别任何候选文件，请检查问题描述是否清晰')

        # Phase 2
        final_files = self.phase2_final_selection(
            repo_path, candidates, question, progress_cb
        )

        # Phase 3 (optional)
        if use_import_chain and final_files:
            final_files = self.scan_import_chain(
                repo_path, final_files, all_files, progress_cb
            )

        return {
            'repo_path':        repo_path,
            'all_files':        all_files,           # full repo tree for manual selection
            'total_files':      len(all_files),
            'candidates_count': len(candidates),
            'candidates':       candidates,
            'final_files':      sorted(final_files),
        }
