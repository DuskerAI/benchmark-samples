import { createReactiveSystem, ReactiveFlags, type Link, type ReactiveNode } from './system.js';

interface EffectScopeNode extends ReactiveNode {
}

interface EffectNode extends ReactiveNode {
	fn(): (() => void) | void;
	cleanup: (() => void) | void;
}

interface ComputedNode<T = any> extends ReactiveNode {
	value: T | undefined;
	getter: (previousValue?: T) => T;
}

interface SignalNode<T = any> extends ReactiveNode {
	currentValue: T;
	pendingValue: T;
}

// Marks a parent (effect or scope) whose deps include at least one child
// effect. Used to gate the dispose-children-first slow path in run() so
// leaf effects (no children, no own cleanup) avoid the extra deps walk.
// The bit is outside ReactiveFlags' range and never touched by system.ts.
const HasChildEffect = 64;

let cycle = 0;
let runDepth = 0;
let batchDepth = 0;
let notifyIndex = 0;
let queuedLength = 0;
let activeSub: ReactiveNode | undefined;
let frame: Frame | undefined;
let settleAll = false;
let txCheck: Set<EffectNode> | undefined;
let txBase: Map<ReactiveNode, unknown> | undefined;

// A savepoint. Nodes are snapshotted copy-on-write the first time a frame
// touches them, so aborting restores exactly the state at frame entry.
interface Frame {
	parent: Frame | undefined;
	journal: Map<ReactiveNode, Snapshot>;
	resources: ReactiveNode[];
	retired: EffectNode[];
	forced: Set<ReactiveNode> | undefined;
	activeSub: ReactiveNode | undefined;
	batchDepth: number;
	queued: number;
}

interface Snapshot {
	node: ReactiveNode;
	isNew: boolean;
	flags: number;
	deps: Link | undefined;
	depsTail: Link | undefined;
	subs: Link | undefined;
	subsTail: Link | undefined;
	depLinks: [Link, number, Link | undefined, Link | undefined][];
	subLinks: [Link, Link | undefined, Link | undefined][];
	value: unknown;
	pending: unknown;
}

const queued: (EffectNode | undefined)[] = [];
const {
	link: sysLink,
	unlink: sysUnlink,
	propagate: sysPropagate,
	checkDirty: sysCheckDirty,
	shallowPropagate: sysShallowPropagate,
} = createReactiveSystem({
	update(node: SignalNode | ComputedNode | EffectScopeNode): boolean {
		if (frame !== undefined) {
			touch(node);
			for (let l = node.subs; l !== undefined; l = l.nextSub) {
				touch(l.sub);
			}
		}
		if ('getter' in node) {
			return updateComputed(node);
		}
		if ('currentValue' in node) {
			return updateSignal(node);
		}
		node.flags = ReactiveFlags.Mutable;
		return true;
	},
	notify(effect: EffectNode) {
		let insertIndex = queuedLength;
		let firstInsertedIndex = insertIndex;

		do {
			if (frame !== undefined) {
				touch(effect);
			}
			queued[insertIndex++] = effect;
			effect.flags &= ~ReactiveFlags.Watching;
			effect = effect.subs?.sub as EffectNode;
			if (effect === undefined || !(effect.flags & ReactiveFlags.Watching)) {
				break;
			}
		} while (true);

		queuedLength = insertIndex;

		while (firstInsertedIndex < --insertIndex) {
			const left = queued[firstInsertedIndex];
			queued[firstInsertedIndex++] = queued[insertIndex];
			queued[insertIndex] = left;
		}
	},
	unwatched(node: SignalNode | ComputedNode | EffectNode | EffectScopeNode) {
		if (frame !== undefined) {
			touch(node);
		}
		if ('getter' in node) {
			if (node.depsTail !== undefined) {
				node.flags = ReactiveFlags.Mutable | ReactiveFlags.Dirty;
				disposeAllDepsInReverse(node);
			}
		}
		else if ('currentValue' in node) {
			// Nothing to do for signals, they are always mutable and never dirty until pendingValue changes
		}
		else if ('fn' in node) {
			effectOper.call(node);
		}
		else {
			effectScopeOper.call(node);
		}
	},
});

export function getActiveSub(): ReactiveNode | undefined {
	return activeSub;
}

export function setActiveSub(sub?: ReactiveNode) {
	const prevSub = activeSub;
	activeSub = sub;
	return prevSub;
}

export function getBatchDepth(): number {
	return batchDepth;
}

export function startBatch() {
	++batchDepth;
}

export function endBatch() {
	if (!--batchDepth) {
		flush();
	}
}

export function isSignal(fn: () => void): boolean {
	return fn.name === 'bound ' + signalOper.name;
}

export function isComputed(fn: () => void): boolean {
	return fn.name === 'bound ' + computedOper.name;
}

export function isEffect(fn: () => void): boolean {
	return fn.name === 'bound ' + effectOper.name;
}

export function isEffectScope(fn: () => void): boolean {
	return fn.name === 'bound ' + effectScopeOper.name;
}

export function signal<T>(): {
	(): T | undefined;
	(value: T | undefined): void;
};
export function signal<T>(initialValue: T): {
	(): T;
	(value: T): void;
};
export function signal<T>(initialValue?: T): {
	(): T | undefined;
	(value: T | undefined): void;
} {
	const s: SignalNode<T | undefined> = {
		currentValue: initialValue,
		pendingValue: initialValue,
		subs: undefined,
		subsTail: undefined,
		flags: ReactiveFlags.Mutable,
	};
	if (frame !== undefined) {
		touch(s, true);
	}
	return signalOper.bind(s) as () => T | undefined;
}

export function computed<T>(getter: (previousValue?: T) => T): () => T {
	const c: ComputedNode = {
		value: undefined,
		subs: undefined,
		subsTail: undefined,
		deps: undefined,
		depsTail: undefined,
		flags: ReactiveFlags.None,
		getter: getter as (previousValue?: unknown) => unknown,
	};
	if (frame !== undefined) {
		touch(c, true);
	}
	return computedOper.bind(c) as () => T;
}

export function effect(fn: () => void | (() => void)): () => void {
	const e: EffectNode = {
		fn,
		cleanup: undefined,
		subs: undefined,
		subsTail: undefined,
		deps: undefined,
		depsTail: undefined,
		flags: ReactiveFlags.Watching | ReactiveFlags.RecursedCheck,
	};
	if (frame !== undefined) {
		touch(e, true);
		frame.resources.push(e);
	}
	const prevSub = setActiveSub(e);
	if (prevSub !== undefined) {
		link(e, prevSub, 0);
		prevSub.flags |= HasChildEffect;
	}
	try {
		++runDepth;
		e.cleanup = e.fn();
	} finally {
		--runDepth;
		activeSub = prevSub;
		e.flags &= ~ReactiveFlags.RecursedCheck;
	}
	return effectOper.bind(e);
}

export function effectScope(fn: () => void): () => void {
	const e: EffectScopeNode = {
		deps: undefined,
		depsTail: undefined,
		subs: undefined,
		subsTail: undefined,
		flags: ReactiveFlags.Mutable,
	};
	if (frame !== undefined) {
		touch(e, true);
		frame.resources.push(e);
	}
	const prevSub = setActiveSub(e);
	if (prevSub !== undefined) {
		link(e, prevSub, 0);
		prevSub.flags |= HasChildEffect;
	}
	try {
		fn();
	} finally {
		activeSub = prevSub;
	}
	return effectScopeOper.bind(e);
}

export function trigger(fn: () => void) {
	const sub: ReactiveNode = {
		deps: undefined,
		depsTail: undefined,
		flags: ReactiveFlags.Watching | ReactiveFlags.RecursedCheck,
	};
	const prevSub = setActiveSub(sub);
	++batchDepth;
	try {
		fn();
	} finally {
		activeSub = prevSub;
		sub.flags = ReactiveFlags.None;
		let link = sub.deps;
		while (link !== undefined) {
			const dep = link.dep;
			link = unlink(link, sub);
			const subs = dep.subs;
			if (subs !== undefined) {
				propagate(subs, !!runDepth);
				shallowPropagate(subs);
				if (frame !== undefined) {
					const forced = frame.forced ??= new Set();
					for (let l: Link | undefined = subs; l !== undefined; l = l.nextSub) {
						forced.add(l.sub);
					}
				}
			}
		}
		if (!--batchDepth) {
			flush();
		}
	}
}

export function transaction<T>(fn: () => T): T {
	const f: Frame = {
		parent: frame,
		journal: new Map(),
		resources: [],
		retired: [],
		forced: undefined,
		activeSub,
		batchDepth,
		queued: queuedLength,
	};
	frame = f;
	++batchDepth;
	let result: T;
	try {
		result = fn();
	} catch (error) {
		abortFrame(f);
		throw error;
	}
	frame = f.parent;
	const parent = f.parent;
	if (parent !== undefined) {
		for (const [node, snapshot] of f.journal) {
			if (!parent.journal.has(node)) {
				parent.journal.set(node, snapshot);
			}
		}
		for (const node of f.resources) {
			parent.resources.push(node);
		}
		for (const node of f.retired) {
			parent.retired.push(node);
		}
		if (f.forced !== undefined) {
			if (parent.forced === undefined) {
				parent.forced = f.forced;
			} else {
				for (const node of f.forced) {
					parent.forced.add(node);
				}
			}
		}
		--batchDepth;
		return result;
	}
	let failed = false;
	let error: unknown;
	publish(f);
	for (const e of f.retired) {
		if (e.cleanup) {
			try {
				runCleanup(e);
			} catch (err) {
				if (!failed) {
					failed = true;
					error = err;
				}
			}
		}
	}
	if (!--batchDepth) {
		try {
			flush(true);
		} catch (err) {
			if (!failed) {
				failed = true;
				error = err;
			}
		}
	} else if (queuedLength > notifyIndex) {
		settleAll = true;
	}
	if (failed) {
		throw error;
	}
	return result;
}

function touch(node: ReactiveNode, isNew = false): void {
	const journal = frame!.journal;
	if (journal.has(node)) {
		return;
	}
	const depLinks: Snapshot['depLinks'] = [];
	const subLinks: Snapshot['subLinks'] = [];
	for (let l = node.deps; l !== undefined; l = l.nextDep) {
		depLinks.push([l, l.version, l.prevDep, l.nextDep]);
	}
	for (let l = node.subs; l !== undefined; l = l.nextSub) {
		subLinks.push([l, l.prevSub, l.nextSub]);
	}
	let value: unknown;
	let pending: unknown;
	if ('getter' in node) {
		value = (node as ComputedNode).value;
	}
	else if ('currentValue' in node) {
		value = (node as SignalNode).currentValue;
		pending = (node as SignalNode).pendingValue;
	}
	else if ('fn' in node) {
		value = (node as EffectNode).cleanup;
	}
	journal.set(node, {
		node,
		isNew,
		flags: node.flags,
		deps: node.deps,
		depsTail: node.depsTail,
		subs: node.subs,
		subsTail: node.subsTail,
		depLinks,
		subLinks,
		value,
		pending,
	});
}

function restore(snapshot: Snapshot): void {
	const node = snapshot.node;
	node.flags = snapshot.flags;
	node.deps = snapshot.deps;
	node.depsTail = snapshot.depsTail;
	node.subs = snapshot.subs;
	node.subsTail = snapshot.subsTail;
	for (const [l, version, prevDep, nextDep] of snapshot.depLinks) {
		l.version = version;
		l.prevDep = prevDep;
		l.nextDep = nextDep;
	}
	for (const [l, prevSub, nextSub] of snapshot.subLinks) {
		l.prevSub = prevSub;
		l.nextSub = nextSub;
	}
	if ('getter' in node) {
		(node as ComputedNode).value = snapshot.value;
	}
	else if ('currentValue' in node) {
		(node as SignalNode).currentValue = snapshot.value;
		(node as SignalNode).pendingValue = snapshot.pending;
	}
	else if ('fn' in node) {
		(node as EffectNode).cleanup = snapshot.value as EffectNode['cleanup'];
	}
}

function touchDownstream(link: Link | undefined): void {
	const seen = new Set<ReactiveNode>();
	const stack: Link[] = [];
	if (link !== undefined) {
		stack.push(link);
	}
	while (stack.length) {
		for (let l: Link | undefined = stack.pop(); l !== undefined; l = l.nextSub) {
			const node = l.sub;
			if (!seen.has(node)) {
				seen.add(node);
				touch(node);
				if (node.subs !== undefined) {
					stack.push(node.subs);
				}
			}
		}
	}
}

function touchUpstream(sub: ReactiveNode): void {
	const seen = new Set<ReactiveNode>([sub]);
	const stack: Link[] = [];
	touch(sub);
	if (sub.deps !== undefined) {
		stack.push(sub.deps);
	}
	while (stack.length) {
		for (let l: Link | undefined = stack.pop(); l !== undefined; l = l.nextDep) {
			const node = l.dep;
			if (!seen.has(node)) {
				seen.add(node);
				touch(node);
				if (node.deps !== undefined) {
					stack.push(node.deps);
				}
			}
		}
	}
}

function link(dep: ReactiveNode, sub: ReactiveNode, version: number): void {
	if (frame !== undefined) {
		touch(dep);
		touch(sub);
	}
	sysLink(dep, sub, version);
}

function unlink(l: Link, sub = l.sub): Link | undefined {
	if (frame !== undefined) {
		touch(l.dep);
		touch(sub);
	}
	return sysUnlink(l, sub);
}

function propagate(l: Link, innerWrite: boolean): void {
	if (frame !== undefined) {
		touchDownstream(l);
	}
	sysPropagate(l, innerWrite);
}

function checkDirty(l: Link, sub: ReactiveNode): boolean {
	if (frame !== undefined) {
		touchUpstream(sub);
	}
	return sysCheckDirty(l, sub);
}

function shallowPropagate(l: Link): void {
	if (frame !== undefined) {
		for (let x: Link | undefined = l; x !== undefined; x = x.nextSub) {
			touch(x.sub);
		}
	}
	sysShallowPropagate(l);
}

function abortFrame(f: Frame): void {
	frame = f.parent;
	const resources = f.resources;
	const cleanups: unknown[] = [];
	for (const node of resources) {
		cleanups.push('fn' in node ? (node as EffectNode).cleanup : undefined);
	}
	for (const snapshot of f.journal.values()) {
		restore(snapshot);
	}
	activeSub = f.activeSub;
	batchDepth = f.batchDepth;
	while (queuedLength > f.queued) {
		queued[--queuedLength] = undefined;
	}
	const prevSettle = settleAll;
	for (let i = resources.length; i--;) {
		const node = resources[i];
		settleAll = true;
		try {
			node.flags = ReactiveFlags.None;
			disposeAllDepsInReverse(node);
			const sub = node.subs;
			if (sub !== undefined) {
				unlink(sub);
			}
			const cleanup = cleanups[i] as (() => void) | void;
			if (cleanup) {
				(node as EffectNode).cleanup = undefined;
				const prevSub = activeSub;
				activeSub = undefined;
				try {
					cleanup();
				} finally {
					activeSub = prevSub;
				}
			}
		} catch {
			// Secondary failures never replace the callback's own error.
		}
	}
	settleAll = prevSettle;
}

// Decides what the outermost commit publishes: effects only re-run when a
// dependency's final value differs from its value at transaction entry.
function publish(f: Frame): void {
	let checks: EffectNode[] | undefined;
	for (const [node, snapshot] of f.journal) {
		if (snapshot.isNew || !('fn' in node)) {
			continue;
		}
		let flags = node.flags;
		if (
			flags & ReactiveFlags.Dirty
			&& !(flags & (ReactiveFlags.Watching | ReactiveFlags.RecursedCheck))
			&& !(snapshot.flags & ReactiveFlags.Dirty)
			&& !f.forced?.has(node)
		) {
			node.flags = flags &= ~ReactiveFlags.Dirty;
		}
		if (
			flags & ReactiveFlags.Pending
			&& !(flags & (ReactiveFlags.Dirty | ReactiveFlags.Watching | ReactiveFlags.RecursedCheck))
		) {
			(checks ??= []).push(node as EffectNode);
		}
	}
	if (checks === undefined) {
		return;
	}
	const base = txBase ??= new Map();
	for (const [node, snapshot] of f.journal) {
		if (
			!snapshot.isNew
			&& !base.has(node)
			&& ('currentValue' in node || ('getter' in node && snapshot.flags !== ReactiveFlags.None))
		) {
			base.set(node, snapshot.value);
		}
	}
	const check = txCheck ??= new Set();
	for (const e of checks) {
		check.add(e);
	}
}

function refreshComputed(c: ComputedNode): void {
	const prevSub = activeSub;
	activeSub = undefined;
	try {
		computedOper.call(c);
	} finally {
		activeSub = prevSub;
	}
}

function txDirty(e: EffectNode): boolean {
	const base = txBase!;
	for (let l = e.deps; l !== undefined; l = l.nextDep) {
		const dep = l.dep;
		if ('getter' in dep) {
			if (base.has(dep)) {
				refreshComputed(dep as ComputedNode);
				if ((dep as ComputedNode).value !== base.get(dep)) {
					return true;
				}
				continue;
			}
		}
		else if ('currentValue' in dep) {
			if (base.has(dep)) {
				if ((dep as SignalNode).pendingValue !== base.get(dep)) {
					return true;
				}
				continue;
			}
		}
		else {
			continue;
		}
		if (dep.flags & (ReactiveFlags.Dirty | ReactiveFlags.Pending)) {
			const probe: ReactiveNode = { flags: ReactiveFlags.Pending };
			if (checkDirty({
				version: 0,
				dep,
				sub: probe,
				prevSub: undefined,
				nextSub: undefined,
				prevDep: undefined,
				nextDep: undefined,
			}, probe)) {
				return true;
			}
		}
	}
	return false;
}

function updateComputed(c: ComputedNode): boolean {
	if (frame !== undefined) {
		touch(c);
	}
	if (c.flags & HasChildEffect) {
		let link = c.depsTail;
		while (link !== undefined) {
			const prev = link.prevDep;
			const dep = link.dep;
			if (!('getter' in dep) && !('currentValue' in dep)) {
				unlink(link, c);
			}
			link = prev;
		}
	}
	c.depsTail = undefined;
	c.flags = ReactiveFlags.Mutable | ReactiveFlags.RecursedCheck;
	const prevSub = setActiveSub(c);
	try {
		++cycle;
		const oldValue = c.value;
		return oldValue !== (c.value = c.getter(oldValue));
	} finally {
		activeSub = prevSub;
		c.flags &= ~ReactiveFlags.RecursedCheck;
		purgeDeps(c);
	}
}

function updateSignal(s: SignalNode): boolean {
	if (frame !== undefined) {
		touch(s);
	}
	s.flags = ReactiveFlags.Mutable;
	return s.currentValue !== (s.currentValue = s.pendingValue);
}

function run(e: EffectNode): void {
	let flags = e.flags;
	if (
		txCheck !== undefined
		&& flags & ReactiveFlags.Pending
		&& !(flags & ReactiveFlags.Dirty)
		&& txCheck.has(e)
	) {
		if (txDirty(e)) {
			e.flags = flags |= ReactiveFlags.Dirty;
		} else {
			if (e.deps !== undefined) {
				e.flags = ReactiveFlags.Watching | (flags & HasChildEffect);
			}
			return;
		}
	}
	if (
		flags & ReactiveFlags.Dirty
		|| (
			flags & ReactiveFlags.Pending
			&& checkDirty(e.deps!, e)
		)
	) {
		if (flags & HasChildEffect) {
			let link = e.depsTail;
			while (link !== undefined) {
				const prev = link.prevDep;
				const dep = link.dep;
				if (!('getter' in dep) && !('currentValue' in dep)) {
					unlink(link, e);
				}
				link = prev;
			}
		}
		if (e.cleanup) {
			runCleanup(e);
			if (!e.flags) {
				return;
			}
		}
		e.depsTail = undefined;
		e.flags = ReactiveFlags.Watching | ReactiveFlags.RecursedCheck;
		const prevSub = setActiveSub(e);
		try {
			++cycle;
			++runDepth;
			e.cleanup = e.fn();
		} finally {
			--runDepth;
			activeSub = prevSub;
			e.flags &= ~ReactiveFlags.RecursedCheck;
			purgeDeps(e);
		}
	} else if (e.deps !== undefined) {
		e.flags = ReactiveFlags.Watching | (flags & HasChildEffect);
	}
}

function flush(settle = false): void {
	const attemptAll = settle || settleAll;
	let failed = false;
	let error: unknown;
	try {
		while (notifyIndex < queuedLength) {
			const effect = queued[notifyIndex]!;
			queued[notifyIndex++] = undefined;
			if (attemptAll) {
				try {
					run(effect);
				} catch (err) {
					if (!failed) {
						failed = true;
						error = err;
					}
				}
			} else {
				run(effect);
			}
		}
	} finally {
		while (notifyIndex < queuedLength) {
			const effect = queued[notifyIndex]!;
			queued[notifyIndex++] = undefined;
			effect.flags |= ReactiveFlags.Watching | ReactiveFlags.Recursed;
		}
		notifyIndex = 0;
		queuedLength = 0;
		settleAll = false;
		txCheck = undefined;
		txBase = undefined;
	}
	if (failed) {
		throw error;
	}
}

function computedOper<T>(this: ComputedNode<T>): T {
	if (frame !== undefined) {
		touch(this);
	}
	const flags = this.flags;
	if (
		flags & ReactiveFlags.Dirty
		|| (
			flags & ReactiveFlags.Pending
			&& (
				checkDirty(this.deps!, this)
				|| (this.flags = flags & ~ReactiveFlags.Pending, false)
			)
		)
	) {
		if (updateComputed(this)) {
			const subs = this.subs;
			if (subs !== undefined) {
				shallowPropagate(subs);
			}
		}
	} else if (!flags) {
		this.flags = ReactiveFlags.Mutable | ReactiveFlags.RecursedCheck;
		const prevSub = setActiveSub(this);
		try {
			this.value = this.getter();
		} finally {
			activeSub = prevSub;
			this.flags &= ~ReactiveFlags.RecursedCheck;
		}
	}
	const sub = activeSub;
	if (sub !== undefined) {
		link(this, sub, cycle);
	}
	return this.value!;
}

function signalOper<T>(this: SignalNode<T>, ...value: [T]): T | void {
	if (frame !== undefined) {
		touch(this);
	}
	if (value.length) {
		if (this.pendingValue !== (this.pendingValue = value[0])) {
			this.flags = ReactiveFlags.Mutable | ReactiveFlags.Dirty;
			const subs = this.subs;
			if (subs !== undefined) {
				propagate(subs, !!runDepth);
				if (!batchDepth) {
					flush();
				}
			}
		}
	} else {
		if (this.flags & ReactiveFlags.Dirty) {
			if (updateSignal(this)) {
				const subs = this.subs;
				if (subs !== undefined) {
					shallowPropagate(subs);
				}
			}
		}
		const sub = activeSub;
		if (sub !== undefined) {
			link(this, sub, cycle);
		}
		return this.currentValue;
	}
}

function runCleanup(e: EffectNode): void {
	const cleanup = e.cleanup!;
	e.cleanup = undefined;
	const prevSub = activeSub;
	activeSub = undefined;
	try {
		cleanup();
	} finally {
		activeSub = prevSub;
	}
}

function effectOper(this: EffectNode): void {
	effectScopeOper.call(this);
	if (this.cleanup) {
		if (frame !== undefined) {
			// Tentative stop: the cleanup waits for the outermost commit.
			frame.retired.push(this);
		} else {
			runCleanup(this);
		}
	}
}

function effectScopeOper(this: EffectScopeNode): void {
	if (frame !== undefined) {
		touch(this);
	}
	this.flags = ReactiveFlags.None;
	disposeAllDepsInReverse(this);
	const sub = this.subs;
	if (sub !== undefined) {
		unlink(sub);
	}
}

function disposeAllDepsInReverse(sub: ReactiveNode): void {
	let link = sub.depsTail;
	while (link !== undefined) {
		const prev = link.prevDep;
		unlink(link, sub);
		link = prev;
	}
}

function purgeDeps(sub: ReactiveNode) {
	const depsTail = sub.depsTail;
	let dep = depsTail !== undefined ? depsTail.nextDep : sub.deps;
	while (dep !== undefined) {
		dep = unlink(dep, sub);
	}
}
