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

// A resource (effect or scope) created while a transaction was active.
interface Resource {
	node: EffectNode | EffectScopeNode;
	owner: Transaction;
	cleanup: (() => void) | void;
	released: boolean;
}

// A cleanup whose execution is deferred until the outermost commit.
interface RetiredCleanup {
	resource: Resource | undefined;
	cleanup: () => void;
}

interface Transaction {
	parent: Transaction | undefined;
	// First-touch snapshots of every node and link mutated in this frame.
	saved: Map<object, object>;
	created: Resource[];
	retired: RetiredCleanup[];
	// Direct consumers force-notified by a successful `trigger`.
	forced: Set<ReactiveNode>;
	entryDepth: number;
	entrySub: ReactiveNode | undefined;
	entryQueued: number;
}

let cycle = 0;
let runDepth = 0;
let batchDepth = 0;
let notifyIndex = 0;
let queuedLength = 0;
let activeSub: ReactiveNode | undefined;
let txn: Transaction | undefined;

const owners = new Map<ReactiveNode, Resource>();
const queued: (EffectNode | undefined)[] = [];
const {
	link,
	unlink,
	propagate,
	checkDirty,
	shallowPropagate,
} = createReactiveSystem({
	journal: touch,
	update(node: SignalNode | ComputedNode | EffectScopeNode): boolean {
		if ('getter' in node) {
			return updateComputed(node);
		}
		if ('currentValue' in node) {
			return updateSignal(node);
		}
		touch(node);
		node.flags = ReactiveFlags.Mutable;
		return true;
	},
	notify(effect: EffectNode) {
		let insertIndex = queuedLength;
		let firstInsertedIndex = insertIndex;

		do {
			touch(effect);
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
				touch(node);
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
	return signalOper.bind({
		currentValue: initialValue,
		pendingValue: initialValue,
		subs: undefined,
		subsTail: undefined,
		flags: ReactiveFlags.Mutable,
	}) as () => T | undefined;
}

export function computed<T>(getter: (previousValue?: T) => T): () => T {
	return computedOper.bind({
		value: undefined,
		subs: undefined,
		subsTail: undefined,
		deps: undefined,
		depsTail: undefined,
		flags: ReactiveFlags.None,
		getter: getter as (previousValue?: unknown) => unknown,
	}) as () => T;
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
	const resource = own(e);
	if (prevSub !== undefined) {
		link(e, prevSub, 0);
		touch(prevSub);
		prevSub.flags |= HasChildEffect;
	}
	try {
		++runDepth;
		e.cleanup = e.fn();
		if (resource !== undefined) {
			resource.cleanup = e.cleanup;
		}
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
	own(e);
	if (prevSub !== undefined) {
		link(e, prevSub, 0);
		touch(prevSub);
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
				if (txn !== undefined) {
					for (let l: Link | undefined = subs; l !== undefined; l = l.nextSub) {
						txn.forced.add(l.sub);
					}
				}
			}
		}
		if (!--batchDepth) {
			flush();
		}
	}
}

function updateComputed(c: ComputedNode): boolean {
	touch(c);
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
	touch(s);
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
		touch(e);
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
		touch(e);
		e.flags = ReactiveFlags.Watching | (flags & HasChildEffect);
	}
}

function flush(): void {
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
				|| (touch(this), this.flags = flags & ~ReactiveFlags.Pending, false)
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
		touch(this);
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
		if (this.pendingValue !== value[0]) {
			touch(this);
			this.pendingValue = value[0];
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
	touch(e);
	e.cleanup = undefined;
	callUntracked(cleanup);
}

function callUntracked(fn: () => void): void {
	const prevSub = activeSub;
	activeSub = undefined;
	try {
		fn();
	} finally {
		activeSub = prevSub;
	}
}

function effectOper(this: EffectNode): void {
	effectScopeOper.call(this);
	const cleanup = this.cleanup;
	if (cleanup) {
		if (txn === undefined) {
			runCleanup(this);
			return;
		}
		// Stopping inside a transaction is tentative: the node is already
		// detached (and restored by a rollback), but its cleanup must only run
		// once the outermost transaction commits.
		touch(this);
		this.cleanup = undefined;
		const resource = owners.get(this);
		if (resource !== undefined && resource.owner === txn) {
			// Created by this very frame: nothing older can be restored.
			resource.released = true;
			resource.cleanup = undefined;
			callUntracked(cleanup as () => void);
		} else {
			txn.retired.push({ resource, cleanup: cleanup as () => void });
		}
	}
}

function effectScopeOper(this: EffectScopeNode): void {
	touch(this);
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

function touch(target: object | undefined): void {
	if (txn !== undefined && target !== undefined && !txn.saved.has(target)) {
		txn.saved.set(target, { ...target });
	}
}

function own(node: EffectNode | EffectScopeNode): Resource | undefined {
	if (txn === undefined) {
		return;
	}
	const resource: Resource = { node, owner: txn, cleanup: undefined, released: false };
	txn.created.push(resource);
	owners.set(node, resource);
	return resource;
}

export function transaction<T>(fn: () => T): T {
	const t: Transaction = {
		parent: txn,
		saved: new Map(),
		created: [],
		retired: [],
		forced: new Set(),
		entryDepth: batchDepth,
		entrySub: activeSub,
		entryQueued: queuedLength,
	};
	txn = t;
	++batchDepth;
	let result: T;
	try {
		result = fn();
	} catch (error) {
		rollback(t);
		throw error;
	}
	commit(t);
	return result;
}

function rollback(t: Transaction): void {
	for (const [target, state] of t.saved) {
		Object.assign(target, state);
	}
	for (let i = t.entryQueued; i < queuedLength; ++i) {
		queued[i] = undefined;
	}
	if (queuedLength > t.entryQueued) {
		queuedLength = t.entryQueued;
	}
	batchDepth = t.entryDepth;
	activeSub = t.entrySub;
	txn = t.parent;
	for (const resource of t.created) {
		owners.delete(resource.node);
	}
	// Compensation runs in the surviving frame (or plainly after an outer
	// abort). Every step is attempted; the callback's own error wins.
	for (let i = t.created.length; i--;) {
		const resource = t.created[i];
		const node = resource.node;
		const cleanup = resource.released ? undefined : resource.cleanup;
		resource.released = true;
		node.flags = ReactiveFlags.None;
		node.deps = node.depsTail = node.subs = node.subsTail = undefined;
		(node as EffectNode).cleanup = undefined;
		if (cleanup) {
			try {
				callUntracked(cleanup as () => void);
			} catch { }
		}
	}
}

function commit(t: Transaction): void {
	const parent = t.parent;
	batchDepth = t.entryDepth;
	activeSub = t.entrySub;
	if (parent !== undefined) {
		for (const [target, state] of t.saved) {
			if (!parent.saved.has(target)) {
				parent.saved.set(target, state);
			}
		}
		for (const resource of t.created) {
			resource.owner = parent;
			parent.created.push(resource);
		}
		parent.retired.push(...t.retired);
		for (const node of t.forced) {
			parent.forced.add(node);
		}
		txn = parent;
		return;
	}

	txn = undefined;
	batchDepth = t.entryDepth + 1;
	try {
		settleUnchanged(t);
	} catch { }
	owners.clear();

	let failed = false;
	let failure: unknown;
	for (const { resource, cleanup } of t.retired) {
		if (resource !== undefined) {
			if (resource.released) {
				continue;
			}
			resource.released = true;
		}
		try {
			callUntracked(cleanup);
		} catch (error) {
			if (!failed) {
				failed = true;
				failure = error;
			}
		}
	}
	// Writes made by cleanups and observers stay queued while the depth is held
	// so that every committed observer is attempted exactly once.
	if (!t.entryDepth) {
		while (notifyIndex < queuedLength) {
			const effect = queued[notifyIndex]!;
			queued[notifyIndex++] = undefined;
			try {
				run(effect);
			} catch (error) {
				if (!failed) {
					failed = true;
					failure = error;
				}
			}
		}
		notifyIndex = 0;
		queuedLength = 0;
	}
	batchDepth = t.entryDepth;
	if (failed) {
		throw failure;
	}
}

// Speculative reads can advance a cache that later returns to its entry value,
// leaving observers flagged although nothing net changed. Re-derive their state
// from the entry values of their dependencies.
function settleUnchanged(t: Transaction): void {
	const prevSub = activeSub;
	activeSub = undefined;
	let cleaned: Set<ReactiveNode> | undefined;
	try {
		for (let i = notifyIndex; i < queuedLength; ++i) {
			const e = queued[i]!;
			const flags = e.flags;
			if (
				!(flags & (ReactiveFlags.Dirty | ReactiveFlags.Pending))
				|| flags & ReactiveFlags.RecursedCheck
				|| owners.has(e)
				|| t.forced.has(e)
			) {
				continue;
			}
			const entry = t.saved.get(e) as EffectNode | undefined;
			if (entry === undefined || entry.flags & ReactiveFlags.Dirty) {
				continue;
			}
			if (hasNetChange(e, t)) {
				e.flags |= ReactiveFlags.Dirty;
			} else {
				e.flags = ReactiveFlags.Watching | (flags & HasChildEffect);
				(cleaned ??= new Set()).add(e);
			}
		}
	} finally {
		activeSub = prevSub;
	}
	if (cleaned !== undefined) {
		let write = notifyIndex;
		for (let read = notifyIndex; read < queuedLength; ++read) {
			const e = queued[read];
			if (!cleaned.has(e!)) {
				queued[write++] = e;
			}
		}
		for (let i = write; i < queuedLength; ++i) {
			queued[i] = undefined;
		}
		queuedLength = write;
	}
}

function hasNetChange(e: EffectNode, t: Transaction): boolean {
	for (let l = e.deps; l !== undefined; l = l.nextDep) {
		const dep = l.dep;
		const before = t.saved.get(dep);
		if ('currentValue' in dep) {
			const entryValue = before !== undefined ? (before as SignalNode).currentValue : dep.currentValue;
			if (entryValue !== (dep as SignalNode).pendingValue) {
				return true;
			}
		}
		else if ('getter' in dep && before !== undefined) {
			try {
				computedOper.call(dep as ComputedNode);
			} catch {
				return true;
			}
			if ((before as ComputedNode).value !== (dep as ComputedNode).value) {
				return true;
			}
		}
	}
	return false;
}
