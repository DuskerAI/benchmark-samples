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

// One savepoint of `transaction()`. `saved` holds the entry state of every
// node/link mutated inside the frame (first touch wins), which is all that is
// needed to restore the graph exactly on abort.
interface Transaction {
	parent: Transaction | undefined;
	saved: Map<object, object>;
	// Effects and scopes created in this frame (including committed inner ones).
	created: Set<ReactiveNode>;
	// Pre-existing effects stopped tentatively; their cleanup waits for commit.
	cleanups: EffectNode[];
	// Effects forced by trigger().
	forced: Set<ReactiveNode>;
	queueLength: number;
	batchDepth: number;
	runDepth: number;
	activeSub: ReactiveNode | undefined;
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
let txFrame: Transaction | undefined;
// Set while draining the notifications of a committed transaction: every
// pending observer is attempted and only the first failure is reported.
let resilient = 0;
let resilientNext = false;

const queued: (EffectNode | undefined)[] = [];
// Effects that a transaction left dirty although its speculative reads may have
// advanced and restored their dependencies: the values they last observed.
const speculative = new Map<EffectNode, unknown[]>();
const {
	link,
	unlink,
	propagate,
	checkDirty,
	shallowPropagate,
} = createReactiveSystem({
	touch,
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

export function transaction<T>(fn: () => T): T {
	const tx: Transaction = {
		parent: txFrame,
		saved: new Map(),
		created: new Set(),
		cleanups: [],
		forced: new Set(),
		queueLength: queuedLength,
		batchDepth,
		runDepth,
		activeSub,
	};
	txFrame = tx;
	++batchDepth;
	let result: T;
	try {
		result = fn();
	} catch (error) {
		abortTransaction(tx);
		throw error;
	}
	return commitTransaction(tx, result);
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
	txFrame?.created.add(e);
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
	txFrame?.created.add(e);
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
				markForced(subs);
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
	if (speculative.size !== 0) {
		const seen = speculative.get(e);
		if (seen !== undefined) {
			speculative.delete(e);
			if (flags & ReactiveFlags.Dirty) {
				let changed: boolean;
				try {
					changed = hasChangedSince(seen);
				} catch (error) {
					e.flags = ReactiveFlags.Watching | (flags & HasChildEffect);
					throw error;
				}
				if (!changed) {
					e.flags = ReactiveFlags.Watching | (flags & HasChildEffect);
					return;
				}
			}
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

function flush(): void {
	if (resilient !== 0 || resilientNext) {
		flushResilient();
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
		speculative.clear();
	}
}

function flushResilient(): void {
	let failed = false;
	let failure: unknown;
	resilientNext = false;
	++resilient;
	try {
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
	} finally {
		if (!--resilient) {
			notifyIndex = 0;
			queuedLength = 0;
			speculative.clear();
		}
	}
	if (failed) {
		throw failure;
	}
}

function computedOper<T>(this: ComputedNode<T>): T {
	const flags = this.flags;
	if (txFrame !== undefined && (!flags || flags & (ReactiveFlags.Dirty | ReactiveFlags.Pending))) {
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
		const tx = txFrame;
		if (tx !== undefined && !tx.created.has(this)) {
			// Tentative stop of a pre-existing effect: cleanup waits for commit.
			tx.cleanups.push(this);
		} else {
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

function touch(target: object | undefined): void {
	const tx = txFrame;
	if (tx !== undefined && target !== undefined && !tx.saved.has(target)) {
		tx.saved.set(target, { ...target });
	}
}

function markForced(subs: Link): void {
	const tx = txFrame;
	let link: Link | undefined = subs;
	do {
		tx?.forced.add(link.sub);
		speculative.delete(link.sub as EffectNode);
	} while ((link = link.nextSub) !== undefined);
}

function hasChangedSince(seen: unknown[]): boolean {
	const prevSub = activeSub;
	activeSub = undefined;
	try {
		for (let i = 0; i < seen.length; i += 2) {
			const dep = seen[i] as SignalNode | ComputedNode;
			const current = 'getter' in dep ? computedOper.call(dep) : (signalOper as (this: SignalNode) => unknown).call(dep);
			if (current !== seen[i + 1]) {
				return true;
			}
		}
		return false;
	} finally {
		activeSub = prevSub;
	}
}

// Runs `cleanup` untracked; used for resources released by an abort.
function releaseResource(r: ReactiveNode): void {
	let cleanup: (() => void) | void = undefined;
	if ('fn' in r) {
		cleanup = (r as EffectNode).cleanup;
		(r as EffectNode).cleanup = undefined;
	}
	// The links of a resource created by an aborted frame are already gone
	// after the restore, so they must not be unlinked again.
	r.flags = ReactiveFlags.None;
	r.deps = r.depsTail = r.subs = r.subsTail = undefined;
	if (cleanup) {
		const prevSub = activeSub;
		activeSub = undefined;
		try {
			cleanup();
		} finally {
			activeSub = prevSub;
		}
	}
}

function abortTransaction(tx: Transaction): void {
	activeSub = tx.activeSub;
	runDepth = tx.runDepth;
	for (const [target, snapshot] of tx.saved) {
		if (!tx.created.has(target as ReactiveNode)) {
			Object.assign(target, snapshot);
		}
	}
	for (let i = tx.queueLength; i < queuedLength; i++) {
		queued[i] = undefined;
	}
	if (queuedLength > tx.queueLength) {
		queuedLength = tx.queueLength;
	}
	// Compensation runs in the nearest surviving transaction, or normally.
	txFrame = tx.parent;
	batchDepth = tx.batchDepth + 1;
	const released = [...tx.created];
	for (let i = released.length; i--;) {
		try {
			releaseResource(released[i]);
		} catch {
			// The original error wins over secondary failures.
		}
	}
	batchDepth = tx.batchDepth;
	if (!batchDepth) {
		try {
			flushResilient();
		} catch {
			// Same as above.
		}
	}
}

function commitTransaction<T>(tx: Transaction, result: T): T {
	const parent = tx.parent;
	activeSub = tx.activeSub;
	txFrame = parent;
	if (parent !== undefined) {
		for (const [target, snapshot] of tx.saved) {
			if (!parent.saved.has(target)) {
				parent.saved.set(target, snapshot);
			}
		}
		for (const r of tx.created) {
			parent.created.add(r);
		}
		parent.cleanups.push(...tx.cleanups);
		for (const e of tx.forced) {
			parent.forced.add(e);
		}
		--batchDepth;
		return result;
	}

	let failed = false;
	let failure: unknown;

	// An effect that is dirty only because speculative reads advanced a cache
	// must still not run when every dependency is back to what it observed.
	for (const [target, snapshot] of tx.saved) {
		if (
			'fn' in target
			&& (target as EffectNode).flags & ReactiveFlags.Dirty
			&& !((snapshot as EffectNode).flags & ReactiveFlags.Dirty)
			&& !tx.created.has(target as EffectNode)
			&& !tx.forced.has(target as EffectNode)
			&& !speculative.has(target as EffectNode)
		) {
			const seen: unknown[] = [];
			for (let link = (target as EffectNode).deps; link !== undefined; link = link.nextDep) {
				const dep = link.dep as SignalNode | ComputedNode;
				const entry = (tx.saved.get(dep) ?? dep) as SignalNode | ComputedNode;
				if ('getter' in dep) {
					seen.push(dep, (entry as ComputedNode).value);
				} else if ('currentValue' in dep) {
					seen.push(dep, (entry as SignalNode).currentValue);
				}
			}
			speculative.set(target as EffectNode, seen);
		}
	}

	for (const e of tx.cleanups) {
		if (e.cleanup) {
			try {
				runCleanup(e);
			} catch (error) {
				if (!failed) {
					failed = true;
					failure = error;
				}
			}
		}
	}
	if (!--batchDepth) {
		try {
			flushResilient();
		} catch (error) {
			if (!failed) {
				failed = true;
				failure = error;
			}
		}
	} else if (notifyIndex < queuedLength) {
		resilientNext = true;
	}
	if (failed) {
		throw failure;
	}
	return result;
}
