import sqlite3
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory

BASE_DIR = Path(__file__).parent
PROJECT_ROOT = BASE_DIR.parent
DATA_DIR = PROJECT_ROOT / 'data'
DB_PATH = DATA_DIR / 'annotations.db'

app = Flask(__name__)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS annotations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                annotator TEXT NOT NULL,
                trace_id TEXT NOT NULL,
                step_number INTEGER NOT NULL,
                domain TEXT,
                label TEXT,
                confidence TEXT,
                note TEXT,
                data_quality_flag INTEGER DEFAULT 0,
                planning_flag INTEGER DEFAULT 0,
                timestamp TEXT,
                UNIQUE(annotator, trace_id, step_number)
            )
        ''')


@app.route('/')
def index():
    return send_from_directory(str(BASE_DIR), 'index.html')


@app.route('/policies/<path:filename>')
def policy_files(filename):
    return send_from_directory(str(BASE_DIR / 'policies'), filename)


@app.route('/data/<path:filename>')
def data_files(filename):
    return send_from_directory(str(DATA_DIR), filename)


@app.route('/<path:filename>')
def static_files(filename):
    return send_from_directory(str(BASE_DIR), filename)


@app.route('/api/annotate', methods=['POST'])
def annotate():
    data = request.get_json()
    items = data if isinstance(data, list) else [data]
    with get_db() as conn:
        for ann in items:
            conn.execute('''
                INSERT INTO annotations
                    (annotator, trace_id, step_number, domain, label, confidence, note, data_quality_flag, planning_flag, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(annotator, trace_id, step_number) DO UPDATE SET
                    domain=excluded.domain, label=excluded.label,
                    confidence=excluded.confidence, note=excluded.note,
                    data_quality_flag=excluded.data_quality_flag,
                    planning_flag=excluded.planning_flag, timestamp=excluded.timestamp
            ''', (
                ann.get('annotator'), ann.get('trace_id'), ann.get('step_number'),
                ann.get('domain'), ann.get('label'), ann.get('confidence'),
                ann.get('note', ''), 1 if ann.get('data_quality_flag') else 0,
                1 if ann.get('planning_flag') else 0,
                ann.get('timestamp'),
            ))
    return jsonify({'ok': True, 'count': len(items)})


@app.route('/api/progress')
def progress():
    annotator = request.args.get('annotator', '')
    with get_db() as conn:
        rows = conn.execute(
            'SELECT * FROM annotations WHERE annotator = ? ORDER BY id', (annotator,)
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d['data_quality_flag'] = bool(d['data_quality_flag'])
        d['planning_flag'] = bool(d.get('planning_flag', 0))
        result.append(d)
    return jsonify(result)


@app.route('/api/export')
def export():
    annotator = request.args.get('annotator', '')
    with get_db() as conn:
        rows = conn.execute(
            'SELECT annotator, trace_id, step_number, domain, label, confidence, note, data_quality_flag, timestamp '
            'FROM annotations WHERE annotator = ? ORDER BY id', (annotator,)
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d['data_quality_flag'] = bool(d['data_quality_flag'])
        d['planning_flag'] = bool(d.get('planning_flag', 0))
        result.append(d)
    from flask import Response
    import json
    filename = f'auditk_annotations_{annotator.replace(" ", "_")}.json'
    return Response(
        json.dumps(result, indent=2),
        mimetype='application/json',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


if __name__ == '__main__':
    init_db()
    print(f'\n  AuditK Annotation Tool')
    print(f'  URL:      http://localhost:8765/')
    print(f'  Progress: {DB_PATH}\n')
    app.run(port=8765, debug=False)
