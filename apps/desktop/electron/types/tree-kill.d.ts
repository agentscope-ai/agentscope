declare module 'tree-kill' {
	function kill(pid: number, signal?: NodeJS.Signals, callback?: (error?: Error) => void): void;

	export default kill;
}
