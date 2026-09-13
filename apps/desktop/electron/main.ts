import { randomBytes } from 'crypto';
import { app, BrowserWindow, dialog, ipcMain, shell, WebContents } from 'electron';
import * as path from 'path';
import { pathToFileURL } from 'url';

import {
	BackendExitDetails,
	BackendProcess,
	DesktopBackendConfig,
	buildDesktopCsp,
} from './backend';

const DEV_SERVER_URL = 'http://localhost:5173';
const DESKTOP_USER_ID = 'local-user';

let mainWindow: BrowserWindow | null = null;
let backend: BackendProcess | null = null;
let backendConfig: DesktopBackendConfig | null = null;
let quitInProgress = false;
let allowQuit = false;

function isHttpUrl(value: string): boolean {
	try {
		const protocol = new URL(value).protocol;
		return protocol === 'http:' || protocol === 'https:';
	} catch {
		return false;
	}
}

function openExternalUrl(value: string): void {
	if (!isHttpUrl(value)) return;
	void shell.openExternal(value).catch((error) => {
		console.error(`Failed to open external URL: ${error}`);
	});
}

function isApplicationNavigation(value: string, applicationUrl: URL): boolean {
	try {
		const target = new URL(value);
		if (applicationUrl.protocol === 'file:') {
			return target.protocol === 'file:' && target.pathname === applicationUrl.pathname;
		}
		return target.origin === applicationUrl.origin;
	} catch {
		return false;
	}
}

function hardenNavigation(window: BrowserWindow, applicationUrl: URL): void {
	window.webContents.setWindowOpenHandler(({ url }) => {
		openExternalUrl(url);
		return { action: 'deny' };
	});
	window.webContents.on('will-navigate', (event, url) => {
		if (isApplicationNavigation(url, applicationUrl)) return;
		event.preventDefault();
		openExternalUrl(url);
	});
}

function installDesktopCsp(window: BrowserWindow, applicationUrl: URL, backendUrl: string): void {
	const policy = buildDesktopCsp(backendUrl);
	window.webContents.session.webRequest.onHeadersReceived(
		{ urls: ['file://*/*'] },
		(details, callback) => {
			if (
				details.resourceType !== 'mainFrame' ||
				!isApplicationNavigation(details.url, applicationUrl)
			) {
				callback({ responseHeaders: details.responseHeaders });
				return;
			}
			callback({
				responseHeaders: {
					...(details.responseHeaders ?? {}),
					'Content-Security-Policy': [policy],
				},
			});
		},
	);
}

async function createWindow(config: DesktopBackendConfig): Promise<BrowserWindow> {
	const isDev = !app.isPackaged;
	const indexPath = path.join(__dirname, 'frontend-dist', 'index.html');
	const applicationUrl = isDev ? new URL(DEV_SERVER_URL) : pathToFileURL(indexPath);

	const window = new BrowserWindow({
		width: 1400,
		height: 900,
		minWidth: 800,
		minHeight: 600,
		title: 'AgentScope',
		show: false,
		webPreferences: {
			preload: path.join(__dirname, 'preload.js'),
			contextIsolation: true,
			nodeIntegration: false,
			sandbox: true,
			webSecurity: true,
		},
	});
	mainWindow = window;

	hardenNavigation(window, applicationUrl);
	if (!isDev) {
		installDesktopCsp(window, applicationUrl, config.backendUrl);
	}
	window.once('ready-to-show', () => window.show());
	window.webContents.on('render-process-gone', (_event, details) => {
		console.error(`Renderer crashed: ${details.reason}, exitCode=${details.exitCode}`);
	});
	window.webContents.on('unresponsive', () => {
		console.error('Renderer became unresponsive');
	});
	window.on('closed', () => {
		if (mainWindow === window) mainWindow = null;
	});

	if (isDev) {
		await window.loadURL(DEV_SERVER_URL);
	} else {
		await window.loadFile(indexPath);
	}
	return window;
}

function registerDesktopConfigIpc(): void {
	ipcMain.on('desktop:get-config', (event) => {
		const trustedSender: WebContents | undefined = mainWindow?.webContents;
		event.returnValue = trustedSender && event.sender === trustedSender ? backendConfig : null;
	});
}

function unexpectedBackendExit(details: BackendExitDetails): void {
	const output = details.stderr ? `\n\n${details.stderr}` : '';
	dialog.showErrorBox(
		'AgentScope backend stopped',
		`The local backend exited unexpectedly (code=${details.code}, ` +
			`signal=${details.signal}).${output}`,
	);
	app.quit();
}

async function startDesktop(): Promise<void> {
	const authToken = randomBytes(32).toString('base64url');
	backend = new BackendProcess({
		isPackaged: app.isPackaged,
		resourcesPath: process.resourcesPath,
		desktopScriptPath: path.resolve(__dirname, '../main.py'),
		dataDir: app.getPath('userData'),
		authToken,
		userId: DESKTOP_USER_ID,
		pythonExecutable: process.env.AGENTSCOPE_PYTHON,
		onUnexpectedExit: unexpectedBackendExit,
	});
	backendConfig = await backend.start();
	await createWindow(backendConfig);
}

async function stopDesktop(): Promise<void> {
	await backend?.stop();
	backend = null;
	backendConfig = null;
}

const hasSingleInstanceLock = app.requestSingleInstanceLock();

if (!hasSingleInstanceLock) {
	app.quit();
} else {
	registerDesktopConfigIpc();
	app.on('second-instance', () => {
		if (!mainWindow) return;
		if (mainWindow.isMinimized()) mainWindow.restore();
		mainWindow.show();
		mainWindow.focus();
	});
	app.whenReady()
		.then(startDesktop)
		.catch((error) => {
			dialog.showErrorBox('AgentScope failed to start', (error as Error).message);
			app.quit();
		});
}

app.on('window-all-closed', () => app.quit());

app.on('before-quit', (event) => {
	if (allowQuit) return;
	event.preventDefault();
	if (quitInProgress) return;
	quitInProgress = true;
	void stopDesktop().finally(() => {
		allowQuit = true;
		app.quit();
	});
});
