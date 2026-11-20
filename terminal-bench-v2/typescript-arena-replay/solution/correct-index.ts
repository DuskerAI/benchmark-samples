import { createHash } from 'node:crypto';
import { mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';

type Action = 'wait' | 'move' | 'strike' | 'blast';
type EventRec = { round: number; actor: string; action: Action; target?: string; dx?: number; dy?: number; x?: number; y?: number };
type UnitInput = { id: string; team: string; hp: number; attack: number; speed: number; vision: number; x: number; y: number };
type Match = { matchId: string; arena: { width: number; height: number; walls?: number[][] }; teams: string[]; units: UnitInput[]; events: EventRec[] };
type UnitState = UnitInput & { alive: boolean };
type Effect = { unit: string; amount: number; hpBefore: number; hpAfter: number };
type TranscriptRow = { round: number; band: number; actor: string; action: string; valid: boolean; note: string; effects: Effect[] };
type VisibilityRow = { round: number; band: number; team: string; visible: string[] };
type RoundRow = { round: number; actions: number; eliminated: string[] };
type Engine = {
  matchId: string;
  arena: Match['arena'];
  teams: string[];
  units: Record<string, UnitState>;
  transcript: TranscriptRow[];
  visibility: VisibilityRow[];
  eliminated: Record<string, { id: string; round: number; band: number }>;
  rounds: Record<string, RoundRow>;
};

function canonical(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  const obj = value as Record<string, unknown>;
  return '{' + Object.keys(obj).sort().map(k => JSON.stringify(k) + ':' + canonical(obj[k])).join(',') + '}';
}

function digestPrefix(match: Match, count: number): string {
  return createHash('sha256').update(canonical({
    matchId: match.matchId,
    arena: match.arena,
    teams: match.teams,
    units: match.units,
    events: match.events.slice(0, count),
  })).digest('hex');
}

function cheb(a: { x: number; y: number }, b: { x: number; y: number }): number {
  return Math.max(Math.abs(a.x - b.x), Math.abs(a.y - b.y));
}

function init(match: Match): Engine {
  const units: Record<string, UnitState> = {};
  for (const unit of match.units) units[unit.id] = { ...unit, alive: unit.hp > 0 };
  const engine: Engine = { matchId: match.matchId, arena: match.arena, teams: match.teams, units, transcript: [], visibility: [], eliminated: {}, rounds: {} };
  logVisibility(engine, 0, 0);
  return engine;
}

function living(engine: Engine): UnitState[] {
  return Object.values(engine.units).filter(u => u.alive).sort((a, b) => a.id.localeCompare(b.id));
}

function logVisibility(engine: Engine, round: number, band: number): void {
  for (const team of engine.teams) {
    const viewers = living(engine).filter(u => u.team === team);
    const visible = living(engine)
      .filter(unit => viewers.some(viewer => cheb(viewer, unit) <= viewer.vision))
      .map(unit => unit.id)
      .sort();
    engine.visibility.push({ round, band, team, visible });
  }
}

function roundRow(engine: Engine, round: number): RoundRow {
  const key = String(round);
  if (!engine.rounds[key]) engine.rounds[key] = { round, actions: 0, eliminated: [] };
  return engine.rounds[key];
}

function processEvents(engine: Engine, events: EventRec[]): void {
  const byRound = new Map<number, EventRec[]>();
  for (const event of events) {
    if (!byRound.has(event.round)) byRound.set(event.round, []);
    byRound.get(event.round)!.push(event);
  }
  const walls = new Set((engine.arena.walls || []).map(w => `${w[0]},${w[1]}`));

  for (const round of [...byRound.keys()].sort((a, b) => a - b)) {
    const ordered = byRound.get(round)!.sort((a, b) => {
      const speedA = engine.units[a.actor]?.speed ?? -999;
      const speedB = engine.units[b.actor]?.speed ?? -999;
      if (speedA !== speedB) return speedB - speedA;
      return a.actor.localeCompare(b.actor);
    });
    const rr = roundRow(engine, round);
    let index = 0;
    let band = 0;
    while (index < ordered.length) {
      const speed = engine.units[ordered[index].actor]?.speed ?? -999;
      const group: EventRec[] = [];
      while (index < ordered.length && (engine.units[ordered[index].actor]?.speed ?? -999) === speed) {
        group.push(ordered[index]);
        index += 1;
      }
      band += 1;
      processBand(engine, rr, group, round, band, walls);
      logVisibility(engine, round, band);
    }
  }
}

function processBand(engine: Engine, rr: RoundRow, events: EventRec[], round: number, band: number, walls: Set<string>): void {
  const startUnits = JSON.parse(JSON.stringify(engine.units)) as Record<string, UnitState>;
  const startOcc = new Map<string, string>();
  for (const unit of Object.values(startUnits)) {
    if (unit.alive) startOcc.set(`${unit.x},${unit.y}`, unit.id);
  }
  const damage: Record<string, number> = {};
  const moved: Record<string, { x: number; y: number }> = {};
  const pending: { event: EventRec; valid: boolean; note: string; targets: string[] }[] = [];

  for (const event of events) {
    const actor = engine.units[event.actor];
    const startActor = startUnits[event.actor];
    let valid = true;
    let note = 'ok';
    let targets: string[] = [];

    if (!actor || !actor.alive || !startActor || !startActor.alive) {
      valid = false;
      note = 'actor-unavailable';
    } else if (event.action === 'move') {
      const dx = event.dx;
      const dy = event.dy;
      const nx = typeof dx === 'number' ? startActor.x + dx : NaN;
      const ny = typeof dy === 'number' ? startActor.y + dy : NaN;
      const occupiedBy = startOcc.get(`${nx},${ny}`);
      if (
        ![-1, 0, 1].includes(dx as number) ||
        ![-1, 0, 1].includes(dy as number) ||
        nx < 0 || ny < 0 || nx >= engine.arena.width || ny >= engine.arena.height ||
        walls.has(`${nx},${ny}`) ||
        (occupiedBy !== undefined && occupiedBy !== event.actor)
      ) {
        valid = false;
        note = 'illegal-move';
      } else {
        moved[event.actor] = { x: nx, y: ny };
      }
    } else if (event.action === 'strike') {
      const target = event.target ? startUnits[event.target] : undefined;
      if (!target || !target.alive || cheb(startActor, target) > 1) {
        valid = false;
        note = 'illegal-target';
      } else {
        targets = [target.id];
      }
    } else if (event.action === 'blast') {
      if (typeof event.x !== 'number' || typeof event.y !== 'number' || cheb(startActor, { x: event.x, y: event.y }) > 3) {
        valid = false;
        note = 'illegal-target';
      } else {
        targets = Object.values(startUnits)
          .filter(unit => unit.alive && unit.team !== startActor.team && cheb(unit, { x: event.x!, y: event.y! }) <= 1)
          .map(unit => unit.id)
          .sort();
      }
    } else if (event.action !== 'wait') {
      valid = false;
      note = 'unknown-action';
    }

    if (valid && (event.action === 'strike' || event.action === 'blast')) {
      for (const target of targets) damage[target] = (damage[target] || 0) + startActor.attack;
    }
    pending.push({ event, valid, note, targets });
  }

  for (const [actor, pos] of Object.entries(moved)) {
    if (engine.units[actor]?.alive) {
      engine.units[actor].x = pos.x;
      engine.units[actor].y = pos.y;
    }
  }
  const after: Record<string, number> = {};
  for (const [target, amount] of Object.entries(damage)) after[target] = startUnits[target].hp - amount;

  for (const item of pending) {
    const actor = startUnits[item.event.actor];
    const effects: Effect[] = [];
    if (item.valid && (item.event.action === 'strike' || item.event.action === 'blast') && actor) {
      for (const target of item.targets.sort()) {
        effects.push({ unit: target, amount: actor.attack, hpBefore: startUnits[target].hp, hpAfter: after[target] });
      }
    }
    engine.transcript.push({ round, band, actor: item.event.actor, action: item.event.action, valid: item.valid, note: item.note, effects });
    rr.actions += 1;
  }

  for (const [target, amount] of Object.entries(damage)) {
    if (engine.units[target]?.alive) engine.units[target].hp -= amount;
  }
  for (const unit of living(engine).filter(u => u.hp <= 0)) {
    unit.alive = false;
    engine.eliminated[unit.id] = { id: unit.id, round, band };
    rr.eliminated.push(unit.id);
  }
  rr.eliminated.sort();
}

function makeDossier(engine: Engine): { report: unknown; transcriptText: string; visibilityText: string } {
  const transcriptText = engine.transcript.map(row => JSON.stringify(row)).join('\n') + (engine.transcript.length > 0 ? '\n' : '');
  const visibilityText = engine.visibility.map(row => JSON.stringify(row)).join('\n') + (engine.visibility.length > 0 ? '\n' : '');
  const liveTeams = [...new Set(living(engine).map(unit => unit.team))].sort();
  const report = {
    matchId: engine.matchId,
    status: 'ok',
    winner: liveTeams.length === 1 ? liveTeams[0] : 'draw',
    rounds: Object.values(engine.rounds).sort((a, b) => a.round - b.round).map(row => ({ ...row, eliminated: [...row.eliminated].sort() })),
    survivors: living(engine).map(unit => ({ id: unit.id, team: unit.team, hp: unit.hp, x: unit.x, y: unit.y })),
    eliminated: Object.values(engine.eliminated).sort((a, b) => a.id.localeCompare(b.id)),
    checksum: createHash('sha256').update(transcriptText).update('\n').update(visibilityText).digest('hex'),
  };
  return { report, transcriptText, visibilityText };
}

function writeAccepted(outDir: string, dossier: { report: unknown; transcriptText: string; visibilityText: string }): void {
  rmSync(outDir, { recursive: true, force: true });
  mkdirSync(outDir, { recursive: true });
  writeFileSync(`${outDir}/report.json`, JSON.stringify(dossier.report, null, 2) + '\n');
  writeFileSync(`${outDir}/transcript.jsonl`, dossier.transcriptText);
  writeFileSync(`${outDir}/visibility.jsonl`, dossier.visibilityText);
}

function writeRejected(matchId: string, outDir: string): void {
  rmSync(outDir, { recursive: true, force: true });
  mkdirSync(outDir, { recursive: true });
  writeFileSync(`${outDir}/report.json`, JSON.stringify({ matchId, status: 'rejected', reason: 'prefix-mismatch' }, null, 2) + '\n');
  writeFileSync(`${outDir}/transcript.jsonl`, '');
  writeFileSync(`${outDir}/visibility.jsonl`, '');
}

function main(): void {
  const [command, matchPath, a, b, c] = process.argv.slice(2);
  if (!command || !matchPath) process.exit(64);
  const match = JSON.parse(readFileSync(matchPath, 'utf8')) as Match;

  if (command === 'adjudicate') {
    const engine = init(match);
    processEvents(engine, match.events);
    writeAccepted(a, makeDossier(engine));
    return;
  }

  if (command === 'checkpoint') {
    const count = Number(a);
    const statePath = b;
    const outDir = c;
    const engine = init(match);
    processEvents(engine, match.events.slice(0, count));
    writeAccepted(outDir, makeDossier(engine));
    mkdirSync(dirname(statePath), { recursive: true });
    writeFileSync(statePath, JSON.stringify({
      matchId: match.matchId,
      prefixLength: count,
      prefixDigest: digestPrefix(match, count),
      units: engine.units,
      transcript: engine.transcript,
      visibility: engine.visibility,
      eliminated: engine.eliminated,
      rounds: engine.rounds,
    }, null, 2) + '\n');
    return;
  }

  if (command === 'resume') {
    const state = JSON.parse(readFileSync(a, 'utf8')) as { prefixLength: number; prefixDigest: string; units: Record<string, UnitState>; transcript: TranscriptRow[]; visibility: VisibilityRow[]; eliminated: Record<string, { id: string; round: number; band: number }>; rounds: Record<string, RoundRow> };
    if (digestPrefix(match, state.prefixLength) !== state.prefixDigest) {
      writeRejected(match.matchId, b);
      process.exit(2);
    }
    const engine: Engine = { matchId: match.matchId, arena: match.arena, teams: match.teams, units: state.units, transcript: state.transcript, visibility: state.visibility, eliminated: state.eliminated, rounds: state.rounds };
    processEvents(engine, match.events.slice(state.prefixLength));
    writeAccepted(b, makeDossier(engine));
    return;
  }

  process.exit(64);
}

main();
