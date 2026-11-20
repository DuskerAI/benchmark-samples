import json
import math
import struct
import subprocess
from pathlib import Path

APP = Path('/app/qrun')
BIN = APP / 'target/release/qrun'
MODEL = APP / 'artifacts/model.qtc'
VOCAB = APP / 'artifacts/vocab.txt'
FIXTURE_DIR = Path('/tests/fixtures')
REF_MODEL = FIXTURE_DIR / 'model.qtc'
REF_VOCAB = FIXTURE_DIR / 'vocab.txt'


def build_binary():
    subprocess.run(['cargo', 'build', '--release', '--locked'], cwd=APP, check=True)


def read_vocab(path=REF_VOCAB):
    pieces = []
    for line in path.read_text().splitlines():
        i, p = line.split('\t', 1)
        i = int(i)
        while len(pieces) <= i:
            pieces.append(None)
        pieces[i] = p
    return pieces


def read_model(path=REF_MODEL):
    b = path.read_bytes()
    off = 0
    if b[:4] != b'QTC1':
        raise ValueError('bad magic')
    off = 4
    vocab, d, max_seq, eos = struct.unpack_from('<IIII', b, off)
    off += 16
    def tensor(rows, cols):
        nonlocal off
        r, c, scale, zero = struct.unpack_from('<II f b 3x', b, off)
        off += 16
        assert (r, c) == (rows, cols)
        raw = struct.unpack_from('<' + 'b' * (rows * cols), b, off)
        off += rows * cols
        vals = [[(raw[i * cols + j] - zero) * scale for j in range(cols)] for i in range(rows)]
        return vals
    model = {'vocab': vocab, 'd': d, 'max_seq': max_seq, 'eos': eos}
    model['emb'] = tensor(vocab, d)
    model['wq'] = tensor(d, d)
    model['wk'] = tensor(d, d)
    model['wv'] = tensor(d, d)
    model['wo'] = tensor(d, d)
    model['out'] = tensor(vocab, d)
    model['wr'] = tensor(d, d)
    model['wg'] = tensor(vocab, d)
    assert off == len(b)
    return model


def norm(s):
    return s.replace('\r\n', '\n').replace('\r', '\n').lower()


def tokenize(text, vocab):
    s = norm(text)
    out = []
    i = 0
    while i < len(s):
        best = None
        for tid, piece in enumerate(vocab[2:], start=2):
            if s.startswith(piece, i) and (best is None or len(piece) > best[1]):
                best = (tid, len(piece))
        if best is None:
            out.append(1)
            i += 1
        else:
            out.append(best[0])
            i += best[1]
    return out


def matvec(m, x):
    return [sum(a*b for a, b in zip(row, x)) for row in m]


def dot(a, b):
    return sum(x*y for x, y in zip(a, b))


def step(model, token, pos, cache):
    x0 = model['emb'][token if token < model['vocab'] else model['eos']]
    memory = [0.0] * model['d']
    if cache['values']:
        for row in cache['values']:
            for i in range(model['d']):
                memory[i] += row[i]
        memory = [x / len(cache['values']) for x in memory]
    route = matvec(model['wr'], x0)
    x = [math.tanh(x0[i] + route[i] + memory[i] * 0.1) for i in range(model['d'])]
    q = matvec(model['wq'], x)
    k = matvec(model['wk'], x)
    v = matvec(model['wv'], x)
    for i in range(model['d']):
        phase = (pos + i) * 0.017
        k[i] += math.sin(phase) * 0.05 + memory[i] * 0.02
        v[i] += math.cos(phase) * 0.03 + route[i] * 0.01
    cache['keys'].append(k)
    cache['values'].append(v)
    scores = [dot(q, kk) / math.sqrt(model['d']) for kk in cache['keys']]
    mx = max(scores)
    weights = [math.exp(s - mx) for s in scores]
    z = sum(weights)
    ctx = [0.0] * model['d']
    for w, val in zip(weights, cache['values']):
        for i in range(model['d']):
            ctx[i] += (w / z) * val[i]
    o = matvec(model['wo'], ctx)
    state = [math.tanh(a+b) for a, b in zip(x, o)]
    logits = []
    for r in range(model['vocab']):
        s = sum(model['out'][r][c] * state[c] + model['wg'][r][c] * memory[c] * 0.05 for c in range(model['d']))
        s += ((pos + r) % 3) * 0.001
        logits.append(s)
    return logits


def constrained_logits(logits, req, vocab_size):
    out = list(logits)
    suppressed = set(req.get('suppress', []))
    assert all(isinstance(x, int) and 0 <= x < vocab_size for x in suppressed)
    assert len(suppressed) < vocab_size
    stops = req.get('stop', [])
    assert all(isinstance(x, int) and 0 <= x < vocab_size for x in stops)
    assert len(set(stops)) == len(stops)
    assert not (set(stops) & suppressed)
    for token_id in suppressed:
        out[token_id] = float('-inf')
    for token_s, amount in req.get('bias', {}).items():
        token_id = int(token_s)
        assert 0 <= token_id < vocab_size
        if token_id not in suppressed:
            out[token_id] += float(amount)
    temperature = float(req.get('temperature', 1.0))
    assert math.isfinite(temperature) and temperature > 0
    out = [x / temperature if math.isfinite(x) else x for x in out]
    assert req['rank_k'] <= vocab_size - len(suppressed)
    return out


def expected_for(req, resume_snapshot=None):
    vocab = read_vocab()
    model = read_model()
    prompt = tokenize(req['prompt'], vocab)
    cache = {'keys': [], 'values': []}
    generated = []
    start = 0
    if resume_snapshot:
        ss = json.loads(Path(resume_snapshot).read_text(encoding='utf-8'))
        cache = {'keys': ss['keys'], 'values': ss['values']}
        generated = ss['generated'][:]
        start = len(generated)
        if any(token_id in req.get('stop', []) for token_id in generated):
            text = ''.join(vocab[t] for t in generated if t > 1)
            return {'id': req['id'], 'tokens': generated, 'text': text}, []
        cur = generated[-1] if generated else (prompt[-1] if prompt else model['eos'])
        pos = ss['next_pos']
    else:
        for i, tok in enumerate(prompt[:-1]):
            step(model, tok, i, cache)
        cur = prompt[-1] if prompt else model['eos']
        pos = max(len(prompt) - 1, 0)
    ranks = []
    for step_idx in range(start, req['max_new']):
        logits = constrained_logits(step(model, cur, pos, cache), req, model['vocab'])
        top = sorted(range(len(logits)), key=lambda i: (-logits[i], i))[:req['rank_k']]
        ranks.append({'id': req['id'], 'step': step_idx, 'top': [[i, logits[i]] for i in top]})
        cur = top[0]
        generated.append(cur)
        pos += 1
        if cur in req.get('stop', []):
            break
    text = ''.join(vocab[t] for t in generated if t > 1)
    return {'id': req['id'], 'tokens': generated, 'text': text}, ranks


def read_jsonl(path):
    if not Path(path).exists():
        return []
    return [json.loads(x) for x in Path(path).read_text(encoding='utf-8').splitlines() if x.strip()]


def run_cli(requests, out, resume=None, model=MODEL, vocab=VOCAB):
    req_file = out.parent / (out.name + '.jsonl')
    req_file.write_text('\n'.join(json.dumps(r) for r in requests) + '\n')
    cmd = [str(BIN), '--model', str(model), '--vocab', str(vocab), '--requests', str(req_file), '--out', str(out)]
    if resume:
        cmd += ['--resume-cache', str(resume)]
    return subprocess.run(cmd, cwd=APP, text=True, capture_output=True, check=False)


def assert_rankings_close(actual, expected):
    assert len(actual) == len(expected)
    for a, e in zip(actual, expected):
        assert a['id'] == e['id']
        assert a['step'] == e['step']
        assert [x[0] for x in a['top']] == [x[0] for x in e['top']]
        for av, ev in zip([x[1] for x in a['top']], [x[1] for x in e['top']]):
            assert abs(av - ev) < 2e-6


def test_reference_cases_and_rejections(tmp_path):
    """Validates deterministic token generation, top-k rankings, rejected requests, and checkpoint snapshots."""
    build_binary()
    out = tmp_path / 'fresh'
    requests = [
        {'id': 'case-a', 'prompt': 'Quant cache deterministic', 'max_new': 5, 'rank_k': 4, 'checkpoint_after': 2},
        {'id': 'case-b', 'prompt': 'edge\r\ncache cache', 'max_new': 4, 'rank_k': 3},
        {'id': 'case-empty', 'prompt': 'KV', 'max_new': 0, 'rank_k': 2},
        {'id': 'case-rank', 'prompt': 'resume cache', 'max_new': 2, 'rank_k': 0},
        {'id': 'case-late-checkpoint', 'prompt': 'cache', 'max_new': 2, 'rank_k': 2, 'checkpoint_after': 3},
    ]
    cp = run_cli(requests, out, model=REF_MODEL, vocab=REF_VOCAB)
    assert cp.returncode == 0, cp.stderr

    # Valid requests must produce exactly the rows dictated by the verifier-only model and vocabulary.
    generated = {row['id']: row for row in read_jsonl(out / 'generated.jsonl')}
    rankings = read_jsonl(out / 'rankings.jsonl')
    expected_gen = []
    expected_ranks = []
    for req in requests[:2]:
        gen, ranks = expected_for(req)
        expected_gen.append(gen)
        expected_ranks.extend(ranks)
    assert set(generated) == {'case-a', 'case-b'}
    for gen in expected_gen:
        assert generated[gen['id']] == gen
    assert_rankings_close(rankings, expected_ranks)

    # Invalid requests remain isolated in rejected.jsonl with the public id/reason schema.
    rejected = {row['id']: row['reason'] for row in read_jsonl(out / 'rejected.jsonl')}
    assert set(rejected) == {'case-empty', 'case-rank', 'case-late-checkpoint'}
    assert 'invalid' in rejected['case-empty'].lower() or 'max_new' in rejected['case-empty'].lower()
    assert 'invalid' in rejected['case-rank'].lower() or 'rank_k' in rejected['case-rank'].lower()
    assert 'invalid' in rejected['case-late-checkpoint'].lower() or 'checkpoint' in rejected['case-late-checkpoint'].lower()

    # Checkpoint snapshots expose the exact continuation fields and keep key/value caches aligned.
    snap = out / 'snapshots/case-a.json'
    assert snap.exists()
    snap_obj = json.loads(snap.read_text())
    assert set(snap_obj) == {'id', 'generated', 'next_pos', 'keys', 'values'}
    assert snap_obj['id'] == 'case-a'
    assert snap_obj['generated'] == generated['case-a']['tokens'][:2]
    assert snap_obj['next_pos'] == 2 + len(tokenize(requests[0]['prompt'], read_vocab())) - 1
    assert len(snap_obj['keys']) == len(snap_obj['values'])


def test_resume_matches_uninterrupted_decode(tmp_path):
    """Checks that saved keys, values, generated tokens, and decode position resume the same final result."""
    build_binary()
    fresh_out = tmp_path / 'fresh'
    req = {'id': 'resume-a', 'prompt': 'Quant cache deterministic', 'max_new': 6, 'rank_k': 5, 'checkpoint_after': 3}
    cp = run_cli([req], fresh_out, model=REF_MODEL, vocab=REF_VOCAB)
    assert cp.returncode == 0, cp.stderr
    snap = fresh_out / 'snapshots/resume-a.json'
    resume_out = tmp_path / 'resume'
    cp = run_cli([{k: v for k, v in req.items() if k != 'checkpoint_after'}], resume_out, snap, model=REF_MODEL, vocab=REF_VOCAB)
    assert cp.returncode == 0, cp.stderr
    fresh_gen = read_jsonl(fresh_out / 'generated.jsonl')[0]
    resume_gen = read_jsonl(resume_out / 'generated.jsonl')[0]
    assert resume_gen == fresh_gen
    _, expected_resume_ranks = expected_for(req, snap)
    assert_rankings_close(read_jsonl(resume_out / 'rankings.jsonl'), expected_resume_ranks)


def test_resume_rejects_inconsistent_snapshot_state(tmp_path):
    """Requires resume snapshots to match the current request id, prompt-derived cache length, and next position."""
    build_binary()
    req = {'id': 'resume-check', 'prompt': 'Quant cache deterministic', 'max_new': 5, 'rank_k': 4, 'checkpoint_after': 2}
    fresh_out = tmp_path / 'fresh'
    cp = run_cli([req], fresh_out, model=REF_MODEL, vocab=REF_VOCAB)
    assert cp.returncode == 0, cp.stderr

    snap_obj = json.loads((fresh_out / 'snapshots/resume-check.json').read_text())
    snap_obj['next_pos'] += 2
    bad_pos = tmp_path / 'bad-pos.json'
    bad_pos.write_text(json.dumps(snap_obj))
    out = tmp_path / 'bad-pos-out'
    cp = run_cli([{k: v for k, v in req.items() if k != 'checkpoint_after'}], out, bad_pos, model=REF_MODEL, vocab=REF_VOCAB)
    assert cp.returncode != 0

    prompt_changed_out = tmp_path / 'prompt-changed-out'
    changed_prompt_req = {k: v for k, v in req.items() if k != 'checkpoint_after'}
    changed_prompt_req['prompt'] = 'edge cache'
    cp = run_cli([changed_prompt_req], prompt_changed_out, fresh_out / 'snapshots/resume-check.json', model=REF_MODEL, vocab=REF_VOCAB)
    assert cp.returncode != 0


def test_token_constraints_affect_generation_and_resume_validation(tmp_path):
    """Exercises per-token suppression, additive logit bias, and resume rejection when constraints change."""
    build_binary()
    constrained = {
        'id': 'guided',
        'prompt': 'quant cache',
        'max_new': 5,
        'rank_k': 4,
        'checkpoint_after': 3,
        'suppress': [0, 1, 9],
        'bias': {'14': 0.75, '3': -0.5, '10': 0.125},
    }
    plain = {'id': 'plain', 'prompt': 'quant cache', 'max_new': 5, 'rank_k': 4}
    out = tmp_path / 'constraints'
    cp = run_cli([constrained, plain], out, model=REF_MODEL, vocab=REF_VOCAB)
    assert cp.returncode == 0, cp.stderr

    generated = {row['id']: row for row in read_jsonl(out / 'generated.jsonl')}
    rankings = read_jsonl(out / 'rankings.jsonl')
    expected_constrained, expected_ranks = expected_for(constrained)
    expected_plain, plain_ranks = expected_for(plain)
    assert generated['guided'] == expected_constrained
    assert generated['plain'] == expected_plain
    assert generated['guided']['tokens'] != generated['plain']['tokens']
    assert all(token not in constrained['suppress'] for token in generated['guided']['tokens'])
    assert_rankings_close(rankings, expected_ranks + plain_ranks)

    resume_out = tmp_path / 'resume-guided'
    resumed = {k: v for k, v in constrained.items() if k != 'checkpoint_after'}
    snap = out / 'snapshots/guided.json'
    cp = run_cli([resumed], resume_out, snap, model=REF_MODEL, vocab=REF_VOCAB)
    assert cp.returncode == 0, cp.stderr
    assert read_jsonl(resume_out / 'generated.jsonl')[0] == expected_constrained
    _, expected_resume_ranks = expected_for(constrained, snap)
    assert_rankings_close(read_jsonl(resume_out / 'rankings.jsonl'), expected_resume_ranks)

    changed = dict(resumed)
    changed['bias'] = {'14': -0.75, '3': -0.5, '10': 0.125}
    cp = run_cli([changed], tmp_path / 'resume-changed-constraints', snap, model=REF_MODEL, vocab=REF_VOCAB)
    assert cp.returncode != 0


def test_temperature_and_stop_tokens_bound_resumed_generation(tmp_path):
    """Verifies temperature-scaled rankings, early stop handling, and completed-stop snapshots on resume."""
    build_binary()
    req = {
        'id': 'stop-temp',
        'prompt': 'quant cache deterministic',
        'max_new': 7,
        'rank_k': 5,
        'checkpoint_after': 2,
        'temperature': 0.55,
        'bias': {'4': 0.4, '7': -0.2},
    }
    expected_gen, expected_ranks = expected_for(req)
    assert len(expected_gen['tokens']) >= 3
    stopping_token = next(
        token for idx, token in enumerate(expected_gen['tokens'][2:], start=2)
        if token not in expected_gen['tokens'][:idx]
    )
    req['stop'] = [stopping_token]
    expected_gen, expected_ranks = expected_for(req)

    out = tmp_path / 'stop-temp'
    cp = run_cli([req], out, model=REF_MODEL, vocab=REF_VOCAB)
    assert cp.returncode == 0, cp.stderr
    generated = read_jsonl(out / 'generated.jsonl')
    assert generated == [expected_gen]
    assert generated[0]['tokens'][-1] == stopping_token
    assert len(generated[0]['tokens']) < req['max_new']
    assert_rankings_close(read_jsonl(out / 'rankings.jsonl'), expected_ranks)

    resume_out = tmp_path / 'stop-temp-resume'
    snap = out / 'snapshots/stop-temp.json'
    cp = run_cli([{k: v for k, v in req.items() if k != 'checkpoint_after'}], resume_out, snap, model=REF_MODEL, vocab=REF_VOCAB)
    assert cp.returncode == 0, cp.stderr
    assert read_jsonl(resume_out / 'generated.jsonl') == [expected_gen]
    _, resume_ranks = expected_for(req, snap)
    assert_rankings_close(read_jsonl(resume_out / 'rankings.jsonl'), resume_ranks)


def test_recurrent_router_uses_prompt_memory_for_late_logits(tmp_path):
    """Checks prompt-cache memory and routing tensors influence rankings across later decode steps."""
    build_binary()
    reqs = [
        {'id': 'memory-a', 'prompt': 'quant cache deterministic edge cache', 'max_new': 6, 'rank_k': 6, 'checkpoint_after': 4},
        {'id': 'memory-b', 'prompt': 'edge deterministic quant cache edge', 'max_new': 5, 'rank_k': 5},
    ]
    out = tmp_path / 'memory'
    cp = run_cli(reqs, out, model=REF_MODEL, vocab=REF_VOCAB)
    assert cp.returncode == 0, cp.stderr
    expected_gen = []
    expected_ranks = []
    for req in reqs:
        gen, ranks = expected_for(req)
        expected_gen.append(gen)
        expected_ranks.extend(ranks)
    generated = read_jsonl(out / 'generated.jsonl')
    assert generated == expected_gen
    assert_rankings_close(read_jsonl(out / 'rankings.jsonl'), expected_ranks)
    early_top = expected_ranks[0]['top'][0][0]
    later_top = next(row for row in expected_ranks if row['id'] == 'memory-a' and row['step'] == 4)['top'][0][0]
    assert early_top != later_top
    snap = out / 'snapshots/memory-a.json'
    resume_out = tmp_path / 'memory-resume'
    cp = run_cli([{k: v for k, v in reqs[0].items() if k != 'checkpoint_after'}], resume_out, snap, model=REF_MODEL, vocab=REF_VOCAB)
    assert cp.returncode == 0, cp.stderr
    assert read_jsonl(resume_out / 'generated.jsonl') == [expected_gen[0]]


def test_invalid_artifact_stops_before_rankings(tmp_path):
    """Requires malformed model and vocabulary artifacts to fail non-zero without leaving ranking output."""
    build_binary()
    bad_model = tmp_path / 'bad.qtc'
    data = bytearray(REF_MODEL.read_bytes())
    data[:4] = b'BAD!'
    bad_model.write_bytes(data)
    out = tmp_path / 'bad-out'
    cp = run_cli([{'id': 'x', 'prompt': 'cache', 'max_new': 2, 'rank_k': 2}], out, model=bad_model, vocab=REF_VOCAB)
    assert cp.returncode != 0
    assert not (out / 'rankings.jsonl').exists() or (out / 'rankings.jsonl').read_text() == ''

    bad_vocab = tmp_path / 'bad-vocab.txt'
    lines = REF_VOCAB.read_text().splitlines()
    pieces = [line.split('\t', 1) for line in lines]
    pieces[4][1] = pieces[3][1]
    bad_vocab.write_text('\n'.join(f'{idx}\t{piece}' for idx, piece in pieces) + '\n')
    out = tmp_path / 'bad-vocab-out'
    cp = run_cli([{'id': 'v', 'prompt': 'cache', 'max_new': 2, 'rank_k': 2}], out, model=REF_MODEL, vocab=bad_vocab)
    assert cp.returncode != 0
    assert not (out / 'rankings.jsonl').exists() or (out / 'rankings.jsonl').read_text() == ''


def test_invalid_constraint_rows_are_rejected_without_stopping_batch(tmp_path):
    """Rejects malformed decoding constraints while later valid requests still produce outputs."""
    build_binary()
    requests = [
        {'id': 'duplicate-suppress', 'prompt': 'cache', 'max_new': 2, 'rank_k': 2, 'suppress': [3, 3]},
        {'id': 'bad-bias-token', 'prompt': 'cache', 'max_new': 2, 'rank_k': 2, 'bias': {'99': 1.0}},
        {'id': 'too-few-left', 'prompt': 'cache', 'max_new': 2, 'rank_k': 20, 'suppress': [0]},
        {'id': 'bad-temperature', 'prompt': 'cache', 'max_new': 2, 'rank_k': 2, 'temperature': 0},
        {'id': 'suppressed-stop', 'prompt': 'cache', 'max_new': 2, 'rank_k': 2, 'suppress': [4], 'stop': [4]},
        {'id': 'valid-after-errors', 'prompt': 'edge cache', 'max_new': 3, 'rank_k': 3, 'suppress': [1], 'bias': {'4': 0.25}},
    ]
    out = tmp_path / 'invalid-constraints'
    cp = run_cli(requests, out, model=REF_MODEL, vocab=REF_VOCAB)
    assert cp.returncode == 0, cp.stderr
    rejected = {row['id']: row['reason'] for row in read_jsonl(out / 'rejected.jsonl')}
    assert set(rejected) == {'duplicate-suppress', 'bad-bias-token', 'too-few-left', 'bad-temperature', 'suppressed-stop'}
    assert all('invalid' in reason.lower() or 'constraint' in reason.lower() or 'rank_k' in reason.lower() for reason in rejected.values())
    generated = read_jsonl(out / 'generated.jsonl')
    assert [row['id'] for row in generated] == ['valid-after-errors']
    expected_gen, expected_ranks = expected_for(requests[-1])
    assert generated[0] == expected_gen
    assert_rankings_close(read_jsonl(out / 'rankings.jsonl'), expected_ranks)
