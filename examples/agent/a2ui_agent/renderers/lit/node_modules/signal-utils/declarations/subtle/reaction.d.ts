export declare const __internal_testing__: {
    active: boolean;
    lastError: null | unknown;
};
/**
 * Reactions are a way to observe a value and run an effect when it changes.
 *
 * The `data` function is run and tracked in a computed signal. It returns a
 * value that is compared to the previous value. If the value changes, the
 * `effect` function is called with the new value and the previous value.
 *
 * @param data A function that returns the value to observe.
 * @param effect A function that is called when the value changes.
 * @param equals A function that compares two values for equality.
 * @returns A function that stops the reaction.
 */
export declare const reaction: <T>(data: () => T, effect: (value: T, previousValue: T) => void, equals?: (value1: any, value2: any) => boolean) => () => void;
//# sourceMappingURL=reaction.d.ts.map