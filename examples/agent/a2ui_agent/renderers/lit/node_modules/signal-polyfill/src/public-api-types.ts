/**
 * The purpose of these tests is to make sure the types are exposed how we expect,
 * and that we double-check what we're exposing as public API
 */
import {expectTypeOf} from 'expect-type';
import {Signal} from './wrapper.ts';

/**
 * Top-Level
 */
expectTypeOf<keyof typeof Signal>().toEqualTypeOf<
  'State' | 'Computed' | 'subtle' | 'isState' | 'isComputed' | 'isWatcher'
>();

/**
 * Construction works as expected
 */
expectTypeOf(Signal.State<number>).toBeConstructibleWith(1);
expectTypeOf(Signal.State<number>).toBeConstructibleWith(1, {});
expectTypeOf(Signal.State<number>).toBeConstructibleWith(1, {equals: (a, b) => true});
expectTypeOf(Signal.State<number>).toBeConstructibleWith(1, {[Signal.subtle.watched]: () => true});
expectTypeOf(Signal.State<number>).toBeConstructibleWith(1, {
  [Signal.subtle.unwatched]: () => true,
});
expectTypeOf(Signal.Computed<number>).toBeConstructibleWith(() => 2);
expectTypeOf(Signal.Computed<number>).toBeConstructibleWith(() => 1, {equals: (a, b) => true});
expectTypeOf(Signal.Computed<number>).toBeConstructibleWith(() => 1, {
  [Signal.subtle.watched]: () => true,
});
expectTypeOf(Signal.Computed<number>).toBeConstructibleWith(() => 1, {
  [Signal.subtle.unwatched]: () => true,
});

// @ts-expect-error
expectTypeOf<Signal.State<number>>().toBeConstructibleWith();
// @ts-expect-error
expectTypeOf(Signal.State<number>).toBeConstructibleWith('wrong', {});
expectTypeOf(Signal.State<number>).toBeConstructibleWith(1, {
  // @ts-expect-error
  [Signal.subtle.watched]: 2,
});
expectTypeOf(Signal.State<number>).toBeConstructibleWith(1, {
  // @ts-expect-error
  [Signal.subtle.unwatched]: 2,
});
expectTypeOf(Signal.State<number>).toBeConstructibleWith(1, {
  // @ts-expect-error
  typo: (a, b) => true,
});
// @ts-expect-error
expectTypeOf<Signal.Computed<number>>().toBeConstructibleWith();
// @ts-expect-error
expectTypeOf(Signal.Computed<number>).toBeConstructibleWith('wrong');
// @ts-expect-error
expectTypeOf(Signal.Computed<number>).toBeConstructibleWith(2);
expectTypeOf(Signal.Computed<number>).toBeConstructibleWith(() => 1, {
  // @ts-expect-error
  [Signal.subtle.watched]: 2,
});
expectTypeOf(Signal.Computed<number>).toBeConstructibleWith(() => 1, {
  // @ts-expect-error
  [Signal.subtle.unwatched]: 2,
});
expectTypeOf(Signal.Computed<number>).toBeConstructibleWith(() => 1, {
  // @ts-expect-error
  typo: (a, b) => true,
});

/**
 * Properties on each of the instances / namespaces
 */
expectTypeOf<keyof Signal.State<unknown> & string>().toEqualTypeOf<'get' | 'set'>();
expectTypeOf<keyof Signal.Computed<unknown> & string>().toEqualTypeOf<'get'>();
expectTypeOf<keyof typeof Signal.subtle>().toEqualTypeOf<
  | 'untrack'
  | 'currentComputed'
  | 'introspectSources'
  | 'introspectSinks'
  | 'hasSinks'
  | 'hasSources'
  | 'Watcher'
  | 'watched'
  | 'unwatched'
>();

expectTypeOf<keyof Signal.subtle.Watcher & string>().toEqualTypeOf<
  'watch' | 'unwatch' | 'getPending'
>();

/**
 * Inference works
 */
expectTypeOf(new Signal.State(0)).toEqualTypeOf<Signal.State<number>>();
expectTypeOf(new Signal.State(0).get()).toEqualTypeOf<number>();
expectTypeOf(new Signal.State(0).set(1)).toEqualTypeOf<void>();

/**
 * Assigning subtypes works
 */
expectTypeOf<Signal.Computed<Narrower>>().toMatchTypeOf<Signal.Computed<Broader>>();
expectTypeOf<Signal.State<Narrower>>().toMatchTypeOf<Signal.State<Broader>>();

/**
 * Test data types
 */
interface Broader {
  strProp: string;
}
interface Narrower extends Broader {
  numProp: number;
}
