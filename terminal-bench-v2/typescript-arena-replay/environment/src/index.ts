import { createHash } from 'node:crypto';
import { rmSync, mkdirSync, writeFileSync, readFileSync } from 'node:fs';
import { dirname } from 'node:path';

type Action = 'wait' | 'move' | 'strike' | 'blast';
type EventRec = { round: number; actor: string; action: Action; target?: string; dx?: number; dy?: number; x?: number; y?: number };
type UnitInput = { id: string; team: string; hp: number; attack: number; speed: number; vision: number; x: number; y: number };
type Match = { matchId: string; arena: { width: number; height: number; walls?: number[][] }; teams: string[]; units: UnitInput[]; events: EventRec[] };
type UnitState = UnitInput & { alive: boolean };
type Engine = { matchId: string; arena: Match['arena']; teams: string[]; units: Record<string, UnitState>; transcript: any[]; visibility: any[]; eliminated: Record<string, any>; rounds: Record<string, any> };

function sorted<T>(xs: T[], key: (x: T) => string): T[] { return [...xs].sort((a, b) => key(a).localeCompare(key(b))); }
function canonical(value: any): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  return '{' + Object.keys(value).sort().map(k => JSON.stringify(k) + ':' + canonical(value[k])).join(',') + '}';
}
function prefixDigest(match: Match, count: number): string {
  return createHash('sha256').update(canonical({ matchId: match.matchId, arena: match.arena, teams: match.teams, units: match.units, events: match.events.slice(0, count) })).digest('hex');
}
function init(match: Match): Engine {
  const units: Record<string, UnitState> = {};
  for (const u of match.units) units[u.id] = { ...u, alive: u.hp > 0 };
  const eng = { matchId: match.matchId, arena: match.arena, teams: match.teams, units, transcript: [], visibility: [], eliminated: {}, rounds: {} };
  logVisibility(eng, 0, 0);
  return eng;
}
function wallSet(arena: Match['arena']): Set<string> { return new Set((arena.walls || []).map(w => `${w[0]},${w[1]}`)); }
function occupied(eng: Engine): Set<string> { return new Set(Object.values(eng.units).filter(u => u.alive).map(u => `${u.x},${u.y}`)); }
function inBounds(eng: Engine, x: number, y: number): boolean { return x >= 0 && y >= 0 && x < eng.arena.width && y < eng.arena.height; }
function cheb(a: {x:number;y:number}, b: {x:number;y:number}): number { return Math.max(Math.abs(a.x - b.x), Math.abs(a.y - b.y)); }
function logVisibility(eng: Engine, round: number, band: number): void {
  for (const team of eng.teams) {
    const viewers = Object.values(eng.units).filter(u => u.alive && u.team === team);
    const visible = Object.values(eng.units).filter(u => u.alive && viewers.some(v => cheb(v, u) <= v.vision)).map(u => u.id).sort();
    eng.visibility.push({ round, band, team, visible });
  }
}
function roundRec(eng: Engine, round: number): any {
  const key = String(round);
  if (!eng.rounds[key]) eng.rounds[key] = { round, actions: 0, eliminated: [] };
  return eng.rounds[key];
}
function processEvents(eng: Engine, events: EventRec[]): void {
  const byRound = new Map<number, EventRec[]>();
  for (const ev of events) if (!byRound.has(ev.round)) byRound.set(ev.round, []), byRound.get(ev.round)!.push(ev); else byRound.get(ev.round)!.push(ev);
  const walls = wallSet(eng.arena);
  for (const round of [...byRound.keys()].sort((a, b) => a - b)) {
    const ordered = byRound.get(round)!.sort((a, b) => {
      const ua = eng.units[a.actor]; const ub = eng.units[b.actor];
      const sa = ua?.speed ?? -999; const sb = ub?.speed ?? -999;
      if (sa !== sb) return sa - sb;
      return a.actor.localeCompare(b.actor);
    });
    let band = 0; let lastSpeed: number | undefined;
    for (const ev of ordered) {
      const actor = eng.units[ev.actor];
      const speed = actor?.speed ?? -999;
      if (lastSpeed === undefined || speed !== lastSpeed) { band += 1; lastSpeed = speed; }
      const effects: any[] = [];
      let valid = true; let note = 'ok';
      if (!actor || !actor.alive) { valid = false; note = 'actor-unavailable'; }
      else if (ev.action === 'move') {
        const nx = actor.x + Number(ev.dx); const ny = actor.y + Number(ev.dy);
        const occ = occupied(eng);
        occ.delete(`${actor.x},${actor.y}`);
        if (![ -1, 0, 1 ].includes(Number(ev.dx)) || ![ -1, 0, 1 ].includes(Number(ev.dy)) || !inBounds(eng, nx, ny) || walls.has(`${nx},${ny}`) || occ.has(`${nx},${ny}`)) { valid = false; note = 'illegal-move'; }
        else { actor.x = nx; actor.y = ny; }
      } else if (ev.action === 'strike') {
        const target = ev.target ? eng.units[ev.target] : undefined;
        if (!target || !target.alive || cheb(actor, target) > 1) { valid = false; note = 'illegal-target'; }
        else { const before = target.hp; target.hp -= actor.attack; effects.push({ unit: target.id, amount: actor.attack, hpBefore: before, hpAfter: target.hp }); }
      } else if (ev.action === 'blast') {
        if (typeof ev.x !== 'number' || typeof ev.y !== 'number' || cheb(actor, {x: ev.x, y: ev.y}) > 3) { valid = false; note = 'illegal-target'; }
        else {
          for (const target of sorted(Object.values(eng.units).filter(u => u.alive && u.team !== actor.team && cheb(u, {x: ev.x!, y: ev.y!}) <= 1), u => u.id)) {
            const before = target.hp; target.hp -= actor.attack; effects.push({ unit: target.id, amount: actor.attack, hpBefore: before, hpAfter: target.hp });
          }
        }
      } else if (ev.action !== 'wait') { valid = false; note = 'unknown-action'; }
      eng.transcript.push({ round, band, actor: ev.actor, action: ev.action, valid, note, effects });
      const rr = roundRec(eng, round); rr.actions += 1;
      for (const u of sorted(Object.values(eng.units).filter(u => u.alive && u.hp <= 0), u => u.id)) {
        u.alive = false; eng.eliminated[u.id] = { id: u.id, round, band }; rr.eliminated.push(u.id);
      }
      logVisibility(eng, round, band);
    }
  }
}
function dossier(eng: Engine): { report: any; transcriptText: string; visibilityText: string } {
  const transcriptText = eng.transcript.map(x => JSON.stringify(x)).join('\n') + (eng.transcript.length ? '\n' : '');
  const visibilityText = eng.visibility.map(x => JSON.stringify(x)).join('\n') + (eng.visibility.length ? '\n' : '');
  const liveTeams = new Set(Object.values(eng.units).filter(u => u.alive).map(u => u.team));
  const survivors = sorted(Object.values(eng.units).filter(u => u.alive), u => u.id).map(u => ({ id: u.id, team: u.team, hp: u.hp, x: u.x, y: u.y }));
  const eliminated = sorted(Object.values(eng.eliminated), e => e.id);
  const rounds = Object.values(eng.rounds).sort((a: any, b: any) => a.round - b.round).map((r: any) => ({ ...r, eliminated: [...r.eliminated].sort() }));
  const checksum = createHash('sha256').update(transcriptText).update('\n').update(visibilityText).digest('hex');
  return { report: { matchId: eng.matchId, status: 'ok', winner: liveTeams.size === 1 ? [...liveTeams][0] : 'draw', rounds, survivors, eliminated, checksum }, transcriptText, visibilityText };
}
function writeDossier(outDir: string, d: {report:any; transcriptText:string; visibilityText:string}): void {
  rmSync(outDir, { recursive: true, force: true }); mkdirSync(outDir, { recursive: true });
  writeFileSync(`${outDir}/report.json`, JSON.stringify(d.report, null, 2) + '\n');
  writeFileSync(`${outDir}/transcript.jsonl`, d.transcriptText);
  writeFileSync(`${outDir}/visibility.jsonl`, d.visibilityText);
}
function main(): void {
  const [cmd, matchPath, arg3, arg4, arg5] = process.argv.slice(2);
  if (!cmd || !matchPath) process.exit(64);
  const match = JSON.parse(readFileSync(matchPath, 'utf8')) as Match;
  if (cmd === 'adjudicate') {
    const eng = init(match); processEvents(eng, match.events); writeDossier(arg3, dossier(eng)); return;
  }
  if (cmd === 'checkpoint') {
    const count = Number(arg3); const statePath = arg4; const outDir = arg5;
    const eng = init(match); processEvents(eng, match.events.slice(0, count)); writeDossier(outDir, dossier(eng));
    mkdirSync(dirname(statePath), { recursive: true });
    writeFileSync(statePath, JSON.stringify({ matchId: match.matchId, prefixLength: count, prefixDigest: String(count), engine: eng }, null, 2) + '\n'); return;
  }
  if (cmd === 'resume') {
    const state = JSON.parse(readFileSync(arg3, 'utf8'));
    if (String(state.prefixLength) !== state.prefixDigest) {
      rmSync(arg4, { recursive: true, force: true }); mkdirSync(arg4, { recursive: true });
      writeFileSync(`${arg4}/report.json`, JSON.stringify({ matchId: match.matchId, status: 'rejected', reason: 'prefix-mismatch' }, null, 2) + '\n');
      writeFileSync(`${arg4}/transcript.jsonl`, ''); writeFileSync(`${arg4}/visibility.jsonl`, ''); process.exit(2);
    }
    const eng = state.engine as Engine; processEvents(eng, match.events.slice(state.prefixLength)); writeDossier(arg4, dossier(eng)); return;
  }
  process.exit(64);
}
main();
