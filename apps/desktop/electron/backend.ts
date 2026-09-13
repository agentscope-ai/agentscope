import { ChildProcess, spawn, spawnSync } from 'child_process';
import * as fs from 'fs';
import * as http from 'http';
import * as path from 'path';
import { createInterface } from 'readline';
import { Writable } from 'stream';

import kill from 'tree-kill';

export const BACKEND_PORT_PREFIX = 'AGENTSCOPE_BACKEND_PORT=';
export const DESKTOP_SHUTDOWN_COMMAND = 'AGENTSCOPE_DESKTOP_SHUTDOWN';
const DEFAULT_STARTUP_TIMEOUT_MS = 30_000;
const HEALTH_RETRY_MS = 250;
const STDERR_LIMIT = 16_384;

export type DesktopBackendConfig = Readonly<{
	backendUrl: string;
	authToken: string;
	userId: string;
}>;

export type BackendExitDetails = Readonly<{
	code: number | null;
	signal: NodeJS.Signals | null;
	stderr: string;
}>;

export type BackendProcessOptions = Readonly<{
	isPackaged: boolean;
	resourcesPath: string;
	desktopScriptPath: string;
	dataDir: string;
	authToken: string;
	userId: string;
	pythonExecutable?: string;
	startupTimeoutMs?: number;
	onUnexpectedExit: (details: BackendExitDetails) => void;
}>;

type CommandSpec = Readonly<{
	command: string;
	args: string[];
}>;

export function parseBackendPort(line: string): number | null {
	const trimmed = line.trim();
	if (!trimmed.startsWith(BACKEND_PORT_PREFIX)) return null;

	const rawPort = trimmed.slice(BACKEND_PORT_PREFIX.length);
	if (!/^\d+$/.test(rawPort)) return null;
	const port = Number(rawPort);
	return Number.isInteger(port) && port > 0 && port <= 65_535 ? port : null;
}

export function getBundledExecutableName(platform: NodeJS.Platform): string {
	return platform === 'win32' ? 'agentscope-backend.exe' : 'agentscope-backend';
}

export function buildDesktopCsp(backendUrl: string): string {
	const backend = new URL(backendUrl);
	if (backend.protocol !== 'http:' || backend.hostname !== '127.0.0.1' || !backend.port) {
		throw new Error(`Desktop backend URL must use a dynamic loopback port: ${backendUrl}`);
	}
	const backendOrigin = backend.origin;
	return [
		"default-src 'self'",
		"base-uri 'none'",
		"object-src 'none'",
		"form-action 'none'",
		"script-src 'self'",
		"style-src 'self' 'unsafe-inline'",
		"font-src 'self' data:",
		`img-src 'self' data: blob: https: ${backendOrigin}`,
		`media-src 'self' data: blob: https: ${backendOrigin}`,
		`connect-src 'self' ${backendOrigin}`,
		`frame-src blob: ${backendOrigin}`,
		"worker-src 'self' blob:",
	].join('; ');
}

function findPython(configured?: string): CommandSpec {
	const candidates: CommandSpec[] = configured
		? [{ command: configured, args: [] }]
		: process.platform === 'win32'
			? [
					{ command: 'py', args: ['-3'] },
					{ command: 'python', args: [] },
				]
			: [
					{ command: 'python3', args: [] },
					{ command: 'python', args: [] },
				];

	for (const candidate of candidates) {
		const result = spawnSync(candidate.command, [...candidate.args, '--version'], {
			stdio: 'ignore',
			shell: false,
		});
		if (!result.error && result.status === 0) return candidate;
	}

	throw new Error(
		configured
			? `Configured Python executable is unavailable: ${configured}`
			: 'Python 3 was not found. Set AGENTSCOPE_PYTHON for development.',
	);
}

export function resolveBackendCommand(options: BackendProcessOptions): CommandSpec {
	if (options.isPackaged) {
		const executableName = getBundledExecutableName(process.platform);
		const executablePath = path.join(options.resourcesPath, 'backend', executableName);
		if (!fs.existsSync(executablePath)) {
			throw new Error(`Bundled backend was not found: ${executablePath}`);
		}
		return {
			command: executablePath,
			args: ['--port', '0', '--data-dir', options.dataDir],
		};
	}

	if (!fs.existsSync(options.desktopScriptPath)) {
		throw new Error(`Desktop backend script was not found: ${options.desktopScriptPath}`);
	}
	const python = findPython(options.pythonExecutable);
	return {
		command: python.command,
		args: [
			...python.args,
			options.desktopScriptPath,
			'--port',
			'0',
			'--data-dir',
			options.dataDir,
		],
	};
}

function delay(milliseconds: number): Promise<void> {
	return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function probeHealth(port: number, authToken: string): Promise<boolean> {
	return new Promise((resolve) => {
		const request = http.get(
			{
				hostname: '127.0.0.1',
				port,
				path: '/health',
				headers: { Authorization: `Bearer ${authToken}` },
				timeout: 1_000,
			},
			(response) => {
				response.resume();
				resolve(response.statusCode === 200);
			},
		);
		request.on('timeout', () => request.destroy());
		request.on('error', () => resolve(false));
	});
}

function killProcessTree(pid: number, signal: NodeJS.Signals): Promise<void> {
	return new Promise((resolve) => {
		kill(pid, signal, () => resolve());
	});
}

export function requestGracefulShutdown(stdin: Writable | null): boolean {
	if (!stdin || stdin.destroyed || stdin.writableEnded) return false;
	stdin.end(`${DESKTOP_SHUTDOWN_COMMAND}\n`);
	return true;
}

function hasExited(child: ChildProcess): boolean {
	return child.exitCode !== null || child.signalCode !== null;
}

export class BackendProcess {
	private child: ChildProcess | null = null;
	private port: number | null = null;
	private stderr = '';
	private stopping = false;
	private ready = false;
	private stopPromise: Promise<void> | null = null;

	constructor(private readonly options: BackendProcessOptions) {}

	async start(): Promise<DesktopBackendConfig> {
		if (this.child) throw new Error('Desktop backend is already running.');

		const spec = resolveBackendCommand(this.options);
		const child = spawn(spec.command, spec.args, {
			env: {
				...process.env,
				AGENTSCOPE_DESKTOP_TOKEN: this.options.authToken,
				PYTHONUNBUFFERED: '1',
			},
			stdio: ['pipe', 'pipe', 'pipe'],
			shell: false,
			windowsHide: true,
		});
		this.child = child;
		child.stdin?.on('error', (error: Error) => {
			if (!this.stopping) {
				console.error(`Backend stdin failed: ${error.message}`);
			}
		});

		child.stderr?.on('data', (data: Buffer) => {
			const text = data.toString();
			this.stderr = `${this.stderr}${text}`.slice(-STDERR_LIMIT);
			console.error(`[backend] ${text.trimEnd()}`);
		});

		const lineReader = child.stdout ? createInterface({ input: child.stdout }) : null;
		lineReader?.on('line', (line) => {
			const port = parseBackendPort(line);
			if (port !== null) {
				this.port = port;
			} else {
				console.log(`[backend] ${line}`);
			}
		});
		child.once('exit', () => lineReader?.close());

		try {
			await this.waitUntilReady();
			this.ready = true;
		} catch (error) {
			await this.stop();
			const detail = this.stderr.trim();
			const suffix = detail ? `\n\nBackend output:\n${detail}` : '';
			throw new Error(`${(error as Error).message}${suffix}`);
		}

		return Object.freeze({
			backendUrl: `http://127.0.0.1:${this.port}`,
			authToken: this.options.authToken,
			userId: this.options.userId,
		});
	}

	private waitUntilReady(): Promise<void> {
		const child = this.child;
		if (!child) throw new Error('Desktop backend process is unavailable.');

		const timeoutMs = this.options.startupTimeoutMs ?? DEFAULT_STARTUP_TIMEOUT_MS;
		const deadline = Date.now() + timeoutMs;

		return new Promise((resolve, reject) => {
			let settled = false;

			const finish = (error?: Error) => {
				if (settled) return;
				settled = true;
				child.off('error', onError);
				if (error) reject(error);
				else {
					this.ready = true;
					resolve();
				}
			};

			const onError = (error: Error) => {
				finish(new Error(`Failed to start desktop backend: ${error.message}`));
			};
			child.once('error', onError);
			child.once('exit', (code, signal) => {
				if (!this.ready) {
					finish(
						new Error(
							`Desktop backend exited before startup (code=${code}, signal=${signal}).`,
						),
					);
					return;
				}
				if (!this.stopping) {
					this.options.onUnexpectedExit({
						code,
						signal,
						stderr: this.stderr.trim(),
					});
				}
			});

			const poll = async () => {
				while (!settled && Date.now() < deadline && !hasExited(child)) {
					if (
						this.port !== null &&
						(await probeHealth(this.port, this.options.authToken))
					) {
						finish();
						return;
					}
					await delay(HEALTH_RETRY_MS);
				}
				if (!settled) {
					finish(new Error(`Desktop backend was not ready within ${timeoutMs}ms.`));
				}
			};
			void poll();
		});
	}

	stop(gracePeriodMs = 5_000): Promise<void> {
		if (this.stopPromise) return this.stopPromise;

		this.stopPromise = this.stopInternal(gracePeriodMs).finally(() => {
			this.stopPromise = null;
		});
		return this.stopPromise;
	}

	private async stopInternal(gracePeriodMs: number): Promise<void> {
		const child = this.child;
		this.child = null;
		this.ready = false;
		this.port = null;
		if (!child?.pid || hasExited(child)) return;

		this.stopping = true;
		const exited = new Promise<void>((resolve) => child.once('exit', () => resolve()));
		if (!requestGracefulShutdown(child.stdin)) {
			await killProcessTree(child.pid, 'SIGTERM');
		}
		await Promise.race([exited, delay(gracePeriodMs)]);
		if (!hasExited(child)) {
			await killProcessTree(child.pid, 'SIGKILL');
			await Promise.race([exited, delay(1_000)]);
		}
		this.stopping = false;
	}
}
