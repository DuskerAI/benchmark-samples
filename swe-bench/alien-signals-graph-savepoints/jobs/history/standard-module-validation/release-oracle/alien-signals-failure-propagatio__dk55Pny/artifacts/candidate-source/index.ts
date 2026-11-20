import { createReactiveSystem, ReactiveFlags, type Link, type ReactiveNode } from './system.js';

interface EffectScopeNode extends ReactiveNode {
}

interface EffectNode extends ReactiveNode {
	fn(): (() => void) | void;
	cleanup: (() => void) | void;
}

interface ComputedNode<T = any> extends ReactiveNode {
	value: T | undefined;
	hasError: boolean;
	error: unknown;
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
let flushing = false;

// A thrown undefined is a failure too. Keep presence separate from its value.
class Failures {
	has = false;
	value: unknown;
	add(error: unknown) {
		if (!this.has) {
			this.has = true;
			this.value = error;
		}
	}
	capture(fn: () => void) {
		try { fn(); } catch (error) { this.add(error); }
	}
	rethrow() {
		if (this.has) throw this.value;
	}
}

function unlinkDep(link: Link, owner: ReactiveNode) {
	// depsTail is the tracked prefix during evaluation, not always the list tail.
	if (owner.depsTail === link) owner.depsTail = link.prevDep;
	return unlink(link, owner);
}

function disposeChildren(owner: ReactiveNode) {
	const failures = new Failures();
	let link = owner.depsTail;
	while (link !== undefined) {
		const dep = link.dep;
		if (!('getter' in dep) && !('currentValue' in dep)) {
			const current = link;
			failures.capture(() => unlinkDep(current, owner));
			// A cleanup may dispose another sibling: restart from the live list.
			link = owner.depsTail;
		} else {
			link = link.prevDep;
		}
	}
	owner.flags &= ~HasChildEffect;
	failures.rethrow();
}

const queued: (EffectNode | undefined)[] = [];
const {
	link,
	unlink,
	propagate,
	checkDirty,
	shallowPropagate,
} = createReactiveSystem({
	update(node: SignalNode | ComputedNode | EffectScopeNode): boolean {
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
		hasError: false,
		error: undefined,
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
	if (prevSub !== undefined) {
		link(e, prevSub, 0);
		prevSub.flags |= HasChildEffect;
	}
	const failures = new Failures();
	try {
		++runDepth;
		try { e.cleanup = e.fn(); } catch (error) { failures.add(error); }
	} finally {
		--runDepth;
		activeSub = prevSub;
		e.flags &= ~ReactiveFlags.RecursedCheck;
	}
	if (failures.has) {
		failures.capture(() => effectOper.call(e));
		failures.rethrow();
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
	if (prevSub !== undefined) {
		link(e, prevSub, 0);
		prevSub.flags |= HasChildEffect;
	}
	const failures = new Failures();
	try { fn(); } catch (error) { failures.add(error); }
	finally { activeSub = prevSub; }
	if (failures.has) {
		failures.capture(() => effectScopeOper.call(e));
		failures.rethrow();
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
			}
		}
		if (!--batchDepth) {
			flush();
		}
	}
}

function updateComputed(c: ComputedNode): boolean {
	const failures = new Failures();
	if (c.flags & HasChildEffect) failures.capture(() => disposeChildren(c));
	c.depsTail = undefined;
	c.flags = ReactiveFlags.Mutable | ReactiveFlags.RecursedCheck;
	const prevSub = setActiveSub(c);
	let value: unknown;
	try {
		++cycle;
		failures.capture(() => { value = c.getter(c.value); });
	} finally {
		activeSub = prevSub;
		c.flags &= ~ReactiveFlags.RecursedCheck;
		failures.capture(() => purgeDeps(c));
	}
	if (failures.has) {
		failures.capture(() => disposeChildren(c));
		const changed = !c.hasError || c.error !== failures.value;
		c.hasError = true;
		c.error = failures.value;
		return changed;
	}
	const changed = c.hasError || c.value !== value;
	c.hasError = false;
	c.error = undefined;
	c.value = value;
	return changed;
}

function updateSignal(s: SignalNode): boolean {
	s.flags = ReactiveFlags.Mutable;
	return s.currentValue !== (s.currentValue = s.pendingValue);
}

function run(e: EffectNode): void {
	const flags = e.flags;
	if (
		flags & ReactiveFlags.Dirty
		|| (flags & ReactiveFlags.Pending && checkDirty(e.deps!, e))
	) {
		const failures = new Failures();
		if (flags & HasChildEffect) failures.capture(() => disposeChildren(e));
		let cleanupFailed = false;
		if (e.cleanup) {
			try { runCleanup(e); }
			catch (error) { cleanupFailed = true; failures.add(error); }
		}
		if (cleanupFailed) failures.capture(() => effectScopeOper.call(e));
		if (!e.flags) {
			failures.rethrow();
			return;
		}
		e.depsTail = undefined;
		e.flags = ReactiveFlags.Watching | ReactiveFlags.RecursedCheck;
		const prevSub = setActiveSub(e);
		let bodyFailed = false;
		try {
			++cycle;
			++runDepth;
			try { e.cleanup = e.fn(); }
			catch (error) { bodyFailed = true; failures.add(error); }
		} finally {
			--runDepth;
			activeSub = prevSub;
			e.flags &= ~ReactiveFlags.RecursedCheck;
			failures.capture(() => purgeDeps(e));
		}
		if (bodyFailed) failures.capture(() => disposeChildren(e));
		failures.rethrow();
	} else if (e.deps !== undefined) {
		e.flags = ReactiveFlags.Watching | (flags & HasChildEffect);
	}
}

function flush(): void {
	if (flushing) return;
	flushing = true;
	const failures = new Failures();
	try {
		while (notifyIndex < queuedLength) {
			const effect = queued[notifyIndex]!;
			queued[notifyIndex++] = undefined;
			failures.capture(() => run(effect));
		}
	} finally {
		notifyIndex = 0;
		queuedLength = 0;
		flushing = false;
	}
	failures.rethrow();
}

function computedOper<T>(this: ComputedNode<T>): T {
	const flags = this.flags;
	if (
		!flags
		|| flags & ReactiveFlags.Dirty
		|| (flags & ReactiveFlags.Pending && (
			checkDirty(this.deps!, this)
			|| (this.flags = flags & ~ReactiveFlags.Pending, false)
		))
	) {
		if (updateComputed(this)) {
			const subs = this.subs;
			if (subs !== undefined) shallowPropagate(subs);
		}
	}
	const sub = activeSub;
	if (sub !== undefined) link(this, sub, cycle);
	if (this.hasError) throw this.error;
	return this.value!;
}

function signalOper<T>(this: SignalNode<T>, ...value: [T]): T | void {
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
	const failures = new Failures();
	failures.capture(() => effectScopeOper.call(this));
	if (this.cleanup) failures.capture(() => runCleanup(this));
	failures.rethrow();
}

function effectScopeOper(this: EffectScopeNode): void {
	const failures = new Failures();
	this.flags = ReactiveFlags.None;
	failures.capture(() => disposeAllDepsInReverse(this));
	const sub = this.subs;
	if (sub !== undefined) failures.capture(() => unlink(sub));
	failures.rethrow();
}

function disposeAllDepsInReverse(sub: ReactiveNode): void {
	const failures = new Failures();
	// A disposer can run in the middle of dependency tracking. Include the
	// unvisited suffix as well as the currently tracked prefix.
	let tail = sub.deps;
	while (tail?.nextDep !== undefined) tail = tail.nextDep;
	sub.depsTail = tail;
	while (sub.depsTail !== undefined) {
		const current = sub.depsTail;
		failures.capture(() => unlinkDep(current, sub));
	}
	failures.rethrow();
}

function purgeDeps(sub: ReactiveNode) {
	const failures = new Failures();
	let dep = sub.depsTail !== undefined ? sub.depsTail.nextDep : sub.deps;
	while (dep !== undefined) {
		const current = dep;
		failures.capture(() => unlinkDep(current, sub));
		dep = sub.depsTail !== undefined ? sub.depsTail.nextDep : sub.deps;
	}
	failures.rethrow();
}
