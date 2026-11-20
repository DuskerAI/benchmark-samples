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

// Transaction state. Every mutation of a node or link inside a transaction is
// preceded by a shallow snapshot of it (first touch per frame), so an aborted
// frame can be rewound exactly by replaying the journal backwards.
interface TransactionFrame {
	parent: TransactionFrame | undefined;
	mark: number;
	seen: Set<object>;
	batchDepth: number;
	runDepth: number;
	activeSub: ReactiveNode | undefined;
	queuedLength: number;
	created: (EffectNode | EffectScopeNode)[];
	createdSet: Set<object>;
	retired: Set<EffectNode>;
	forced: Set<ReactiveNode>;
}

let frame: TransactionFrame | undefined;
// Flat list of [object, snapshot] pairs.
const journal: object[] = [];
// Set while a drain of committed work must attempt every observer.
let draining = 0;
let drainFailure: { error: unknown } | undefined;
// Set when a committed transaction left notifications for an enclosing batch.
let carried = false;

const queued: (EffectNode | undefined)[] = [];
const {
	link,
	unlink,
	propagate,
	checkDirty,
	shallowPropagate,
	setJournal,
} = createReactiveSystem({
	update(node: SignalNode | ComputedNode | EffectScopeNode): boolean {
		if ('getter' in node) {
			return updateComputed(node);
		}
		if ('currentValue' in node) {
			return updateSignal(node);
		}
		if (frame !== undefined) {
			touch(node);
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
		if ('getter' in node) {
			if (node.depsTail !== undefined) {
				if (frame !== undefined) {
					touch(node);
				}
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

export function transaction<T>(fn: () => T): T {
	const f: TransactionFrame = {
		parent: frame,
		mark: journal.length,
		seen: new Set(),
		batchDepth,
		runDepth,
		activeSub,
		queuedLength,
		created: [],
		createdSet: new Set(),
		retired: new Set(),
		forced: new Set(),
	};
	if (frame === undefined) {
		setJournal(touch);
	}
	frame = f;
	++batchDepth;
	let result: T;
	try {
		result = fn();
	} catch (error) {
		abortFrame(f);
		throw error;
	}
	return commitFrame(f, result);
}

function touch(target: object): void {
	const seen = frame!.seen;
	if (!seen.has(target)) {
		seen.add(target);
		journal.push(target, Object.assign({}, target));
	}
}

function registerCreated(node: EffectNode | EffectScopeNode): void {
	frame!.created.push(node);
	frame!.createdSet.add(node);
}

function clearPending(c: ComputedNode, flags: ReactiveFlags): void {
	if (frame !== undefined) {
		touch(c);
	}
	c.flags = flags & ~ReactiveFlags.Pending;
}

function abortFrame(f: TransactionFrame): void {
	// Cleanups live outside the journal: harvest them before rewinding.
	const cleanups = f.created.map(node => (node as EffectNode).cleanup);
	for (let i = journal.length - 2; i >= f.mark; i -= 2) {
		Object.assign(journal[i]!, journal[i + 1]!);
	}
	journal.length = f.mark;
	if (queuedLength > f.queuedLength) {
		for (let i = f.queuedLength; i < queuedLength; ++i) {
			queued[i] = undefined;
		}
		queuedLength = f.queuedLength;
	}
	batchDepth = f.batchDepth;
	runDepth = f.runDepth;
	frame = f.parent;
	if (frame === undefined) {
		setJournal(undefined);
	}
	// Compensation runs untracked, in the nearest surviving frame (if any).
	activeSub = undefined;
	for (let i = f.created.length; i--;) {
		const node = f.created[i]!;
		try {
			node.flags = ReactiveFlags.None;
			disposeAllDepsInReverse(node);
			if (node.subs !== undefined) {
				unlink(node.subs);
			}
			const cleanup = cleanups[i];
			if (cleanup) {
				(node as EffectNode).cleanup = undefined;
				cleanup();
			}
		} catch {
			// The original error wins over secondary failures.
		}
	}
	activeSub = f.activeSub;
}

function commitFrame<T>(f: TransactionFrame, result: T): T {
	const parent = f.parent;
	if (parent !== undefined) {
		for (const target of f.seen) {
			parent.seen.add(target);
		}
		for (const node of f.created) {
			parent.created.push(node);
		}
		for (const node of f.createdSet) {
			parent.createdSet.add(node);
		}
		for (const node of f.retired) {
			parent.retired.add(node);
		}
		for (const node of f.forced) {
			parent.forced.add(node);
		}
		frame = parent;
		--batchDepth;
		return result;
	}

	// The commit decision is made: nothing below may roll anything back.
	let failure: { error: unknown } | undefined;
	let entries: Map<object, any> | undefined;
	if (queuedLength > f.queuedLength) {
		entries = new Map();
		for (let i = f.mark; i < journal.length; i += 2) {
			if (!entries.has(journal[i]!)) {
				entries.set(journal[i]!, journal[i + 1]!);
			}
		}
	}
	frame = undefined;
	setJournal(undefined);
	journal.length = 0;
	if (entries !== undefined) {
		failure = settle(f, entries);
	}

	const prevSub = activeSub;
	activeSub = undefined;
	for (const e of f.retired) {
		if (e.cleanup) {
			try {
				runCleanup(e);
			} catch (error) {
				failure ??= { error };
			}
		}
	}
	activeSub = prevSub;

	if (!--batchDepth) {
		carried = false;
		failure ??= drainAttemptingAll();
	} else if (queuedLength > f.queuedLength) {
		carried = true;
	}
	if (failure !== undefined) {
		throw failure.error;
	}
	return result;
}

// Speculative reads may have advanced caches past (and back to) the values an
// existing observer last saw. Decide per observer whether the committed outcome
// is really different from the state at entry.
function settle(f: TransactionFrame, entries: Map<object, any>): { error: unknown } | undefined {
	let failure: { error: unknown } | undefined;
	const prevSub = activeSub;
	activeSub = undefined;
	for (let i = f.queuedLength; i < queuedLength; ++i) {
		const e = queued[i]!;
		if (
			!(e.flags & (ReactiveFlags.Pending | ReactiveFlags.Dirty))
			|| f.forced.has(e)
			|| f.createdSet.has(e)
		) {
			continue;
		}
		const entryFlags: ReactiveFlags | undefined = entries.get(e)?.flags;
		if (
			entryFlags === undefined
			|| !(entryFlags & ReactiveFlags.Watching)
			|| entryFlags & (ReactiveFlags.Pending | ReactiveFlags.Dirty)
		) {
			continue;
		}
		let changed = false;
		try {
			for (let l = e.deps; l !== undefined && !changed; l = l.nextDep) {
				const dep = l.dep;
				let before: unknown;
				let after: unknown;
				if ('getter' in dep) {
					before = (entries.get(dep) ?? dep).value;
					after = computedOper.call(dep as ComputedNode);
				} else if ('currentValue' in dep) {
					before = (entries.get(dep) ?? dep).currentValue;
					after = (signalOper as (this: SignalNode) => unknown).call(dep as SignalNode);
				} else {
					continue;
				}
				changed = before !== after;
			}
		} catch (error) {
			failure ??= { error };
			changed = true;
		}
		if (changed) {
			e.flags |= ReactiveFlags.Dirty;
		} else {
			e.flags = ReactiveFlags.Watching | (e.flags & HasChildEffect);
		}
	}
	activeSub = prevSub;
	return failure;
}

function drain(): void {
	const failure = drainAttemptingAll();
	if (failure !== undefined) {
		throw failure.error;
	}
}

function drainAttemptingAll(): { error: unknown } | undefined {
	const outer = drainFailure;
	drainFailure = undefined;
	++draining;
	try {
		flushAttemptingAll();
	} finally {
		--draining;
	}
	const failure = drainFailure as { error: unknown } | undefined;
	drainFailure = outer;
	return failure;
}

function flushAttemptingAll(): void {
	while (notifyIndex < queuedLength) {
		const effect = queued[notifyIndex]!;
		queued[notifyIndex++] = undefined;
		try {
			run(effect);
		} catch (error) {
			drainFailure ??= { error };
		}
	}
	notifyIndex = 0;
	queuedLength = 0;
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
	const node: SignalNode = {
		currentValue: initialValue,
		pendingValue: initialValue,
		subs: undefined,
		subsTail: undefined,
		flags: ReactiveFlags.Mutable,
	};
	if (frame !== undefined) {
		// Remember the creation state so an aborted frame resets escaped handles.
		touch(node);
	}
	return signalOper.bind(node) as () => T | undefined;
}

export function computed<T>(getter: (previousValue?: T) => T): () => T {
	const node: ComputedNode = {
		value: undefined,
		subs: undefined,
		subsTail: undefined,
		deps: undefined,
		depsTail: undefined,
		flags: ReactiveFlags.None,
		getter: getter as (previousValue?: unknown) => unknown,
	};
	if (frame !== undefined) {
		touch(node);
	}
	return computedOper.bind(node) as () => T;
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
	const prevSub = setActiveSub(e);
	if (frame !== undefined) {
		registerCreated(e);
	}
	if (prevSub !== undefined) {
		link(e, prevSub, 0);
		if (frame !== undefined) {
			touch(prevSub);
		}
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
	const prevSub = setActiveSub(e);
	if (frame !== undefined) {
		registerCreated(e);
	}
	if (prevSub !== undefined) {
		link(e, prevSub, 0);
		if (frame !== undefined) {
			touch(prevSub);
		}
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
				if (frame !== undefined) {
					let consumer: Link | undefined = subs;
					do {
						frame.forced.add(consumer.sub);
					} while ((consumer = consumer.nextSub) !== undefined);
				}
				propagate(subs, !!runDepth);
				shallowPropagate(subs);
			}
		}
		if (!--batchDepth) {
			flush();
		}
	}
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
	const flags = e.flags;
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

function flush(): void {
	if (draining) {
		flushAttemptingAll();
		return;
	}
	if (carried) {
		carried = false;
		drain();
		return;
	}
	try {
		while (notifyIndex < queuedLength) {
			const effect = queued[notifyIndex]!;
			queued[notifyIndex++] = undefined;
			run(effect);
		}
	} finally {
		while (notifyIndex < queuedLength) {
			const effect = queued[notifyIndex]!;
			queued[notifyIndex++] = undefined;
			effect.flags |= ReactiveFlags.Watching | ReactiveFlags.Recursed;
		}
		notifyIndex = 0;
		queuedLength = 0;
	}
}

function computedOper<T>(this: ComputedNode<T>): T {
	const flags = this.flags;
	if (
		flags & ReactiveFlags.Dirty
		|| (
			flags & ReactiveFlags.Pending
			&& (
				checkDirty(this.deps!, this)
				|| (clearPending(this, flags), false)
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
		if (frame !== undefined) {
			touch(this);
		}
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
	if (value.length) {
		const newValue = value[0];
		if (this.pendingValue !== newValue) {
			if (frame !== undefined) {
				touch(this);
			}
			this.pendingValue = newValue;
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
	if (frame !== undefined && !frame.createdSet.has(this)) {
		// Stopping something that predates this frame is tentative: it only
		// becomes final (and runs its cleanup) when the outermost frame commits.
		stopNode(this);
		frame.retired.add(this);
		return;
	}
	stopNode(this);
	if (this.cleanup) {
		runCleanup(this);
	}
}

function effectScopeOper(this: EffectScopeNode): void {
	stopNode(this);
}

function stopNode(node: EffectScopeNode): void {
	if (frame !== undefined) {
		touch(node);
	}
	node.flags = ReactiveFlags.None;
	disposeAllDepsInReverse(node);
	const sub = node.subs;
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
