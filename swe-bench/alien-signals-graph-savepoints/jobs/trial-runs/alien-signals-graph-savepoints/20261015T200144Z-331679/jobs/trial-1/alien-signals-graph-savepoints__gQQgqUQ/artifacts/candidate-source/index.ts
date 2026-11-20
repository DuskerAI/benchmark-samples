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

// A transaction frame is a savepoint. Every graph object is copied the first
// time it is touched inside a frame, so aborting can put the exact entry state
// back. Inner frames merge into their parent when they succeed.
interface Frame {
	parent: Frame | undefined;
	saved: Map<object, object>;
	owned: (EffectNode | EffectScopeNode)[];
	ownedSet: Set<object>;
	queued: number;
	deferred: number;
	forced: number;
	activeSub: ReactiveNode | undefined;
	batchDepth: number;
	runDepth: number;
}

let frame: Frame | undefined;
// Effects stopped inside a transaction whose cleanup waits for the outer commit.
const deferredCleanups: EffectNode[] = [];
// Direct consumers of a successful trigger() inside a transaction.
const forcedSubs: ReactiveNode[] = [];

const queued: (EffectNode | undefined)[] = [];
const {
	link,
	unlink,
	propagate,
	checkDirty,
	shallowPropagate,
} = createReactiveSystem({
	touch,
	update(node: SignalNode | ComputedNode | EffectScopeNode): boolean {
		touch(node);
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
	const node: SignalNode<T | undefined> = {
		currentValue: initialValue,
		pendingValue: initialValue,
		subs: undefined,
		subsTail: undefined,
		flags: ReactiveFlags.Mutable,
	};
	touch(node);
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
	touch(node);
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
	own(e);
	const prevSub = setActiveSub(e);
	if (prevSub !== undefined) {
		link(e, prevSub, 0);
		touch(prevSub);
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
	own(e);
	const prevSub = setActiveSub(e);
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
				if (frame !== undefined) {
					let forced: Link | undefined = subs;
					do {
						forcedSubs.push(forced.sub);
					} while ((forced = forced.nextSub) !== undefined);
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
	touch(e);
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
	if (flags !== ReactiveFlags.Mutable) {
		touch(this);
	}
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
	if (value.length) {
		touch(this);
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
			// Stopping is tentative inside a transaction: cleanup waits for the outer commit.
			if (deferredCleanups[deferredCleanups.length - 1] !== this) {
				deferredCleanups.push(this);
			}
		}
		else {
			runCleanup(this);
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

function touch(target: object): void {
	const f = frame;
	if (f !== undefined && !f.saved.has(target)) {
		f.saved.set(target, { ...target });
	}
}

function own(e: EffectNode | EffectScopeNode): void {
	const f = frame;
	if (f !== undefined) {
		f.owned.push(e);
		f.ownedSet.add(e);
	}
}

export function transaction<T>(fn: () => T): T {
	const f: Frame = {
		parent: frame,
		saved: new Map(),
		owned: [],
		ownedSet: new Set(),
		queued: queuedLength,
		deferred: deferredCleanups.length,
		forced: forcedSubs.length,
		activeSub,
		batchDepth,
		runDepth,
	};
	frame = f;
	++batchDepth;
	let result: T;
	try {
		result = fn();
	} catch (error) {
		abortTransaction(f);
		throw error;
	}
	commitTransaction(f);
	return result;
}

function commitTransaction(f: Frame): void {
	activeSub = f.activeSub;
	runDepth = f.runDepth;
	frame = f.parent;

	const parent = f.parent;
	if (parent !== undefined) {
		for (const [target, state] of f.saved) {
			if (!parent.saved.has(target)) {
				parent.saved.set(target, state);
			}
		}
		for (const e of f.owned) {
			parent.owned.push(e);
			parent.ownedSet.add(e);
		}
		batchDepth = f.batchDepth;
		return;
	}

	// The outermost commit is decided. From here on nothing may roll back.
	// The batch stays held while releases run, then observers drain.
	batchDepth = f.batchDepth + 1;
	let failed = false;
	let failure: unknown;
	const record = (error: unknown) => {
		if (!failed) {
			failed = true;
			failure = error;
		}
	};
	try {
		reconcile(f);
	} catch (error) {
		record(error);
	}
	forcedSubs.length = 0;
	const pending = deferredCleanups.splice(0);
	for (const e of pending) {
		if (e.cleanup) {
			try {
				runCleanup(e);
			} catch (error) {
				record(error);
			}
		}
	}
	batchDepth = f.batchDepth;
	if (!batchDepth) {
		++batchDepth;
		drain(record);
		--batchDepth;
	}
	if (failed) {
		throw failure;
	}
}

function abortTransaction(f: Frame): void {
	for (const [target, state] of f.saved) {
		if (!f.ownedSet.has(target)) {
			Object.assign(target, state);
		}
	}
	while (queuedLength > f.queued && queuedLength > notifyIndex) {
		queued[--queuedLength] = undefined;
	}
	deferredCleanups.length = f.deferred;
	forcedSubs.length = f.forced;
	activeSub = f.activeSub;
	runDepth = f.runDepth;
	batchDepth = f.batchDepth + 1;
	frame = f.parent;

	// Compensation runs untracked in the surviving context; failures never
	// replace the callback's error.
	activeSub = undefined;
	for (let i = f.owned.length - 1; i >= 0; i--) {
		const e = f.owned[i];
		e.flags = ReactiveFlags.None;
		e.deps = e.depsTail = e.subs = e.subsTail = undefined;
		if ('fn' in e && e.cleanup) {
			try {
				runCleanup(e as EffectNode);
			} catch { }
		}
	}
	activeSub = f.activeSub;
	batchDepth = f.batchDepth;
	if (f.parent === undefined && !batchDepth) {
		++batchDepth;
		drain(() => { });
		--batchDepth;
	}
}

// Effects that only saw a speculative cache advance and came back to their
// entry value must not run: demote them unless something really changed.
function reconcile(f: Frame): void {
	if (!f.saved.size) {
		return;
	}
	let forced: Set<ReactiveNode> | undefined;
	for (let i = notifyIndex; i < queuedLength; i++) {
		const e = queued[i]!;
		const before = f.saved.get(e) as ReactiveNode | undefined;
		if (
			before === undefined
			|| !(e.flags & ReactiveFlags.Dirty)
			|| before.flags & ReactiveFlags.Dirty
			|| f.ownedSet.has(e)
		) {
			continue;
		}
		forced ??= new Set(forcedSubs);
		if (forced.has(e) || hasNetChange(e, f)) {
			continue;
		}
		e.flags = ReactiveFlags.Watching | (e.flags & HasChildEffect);
	}
}

function hasNetChange(e: EffectNode, f: Frame): boolean {
	for (let l = e.deps; l !== undefined; l = l.nextDep) {
		const dep = l.dep as SignalNode | ComputedNode;
		const before = f.saved.get(dep) as SignalNode | ComputedNode | undefined;
		if ('currentValue' in dep) {
			if (dep.pendingValue !== (before !== undefined ? (before as SignalNode).currentValue : dep.currentValue)) {
				return true;
			}
		}
		else if ('getter' in dep) {
			const base = before !== undefined ? (before as ComputedNode).value : dep.value;
			const prevSub = activeSub;
			activeSub = undefined;
			try {
				computedOper.call(dep);
			} catch {
				return true;
			} finally {
				activeSub = prevSub;
			}
			if (dep.value !== base) {
				return true;
			}
		}
	}
	return false;
}

// Like flush(), but attempts every queued observer even if some throw.
function drain(record: (error: unknown) => void): void {
	while (notifyIndex < queuedLength) {
		const effect = queued[notifyIndex]!;
		queued[notifyIndex++] = undefined;
		try {
			run(effect);
		} catch (error) {
			record(error);
		}
	}
	notifyIndex = 0;
	queuedLength = 0;
}
