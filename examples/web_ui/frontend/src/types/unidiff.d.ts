declare module 'unidiff' {
	interface DiffOptions {
		aname?: string;
		bname?: string;
		context?: number;
		pre_context?: number;
		post_context?: number;
		format?: 'unified';
	}

	const unidiff: {
		diffAsText: (
			oldText: string | string[],
			newText: string | string[],
			options?: DiffOptions,
		) => string;
	};

	export default unidiff;
}
